# coding=utf-8
import json
import logging
import urllib
import urlparse

from django.http.response import HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import render
from owslib import fes
from owslib.csw import CatalogueServiceWeb
from owslib.iso import MD_Metadata

from geosafe.forms import MetaSearchForm
from geosafe.helpers.metasearch.csw_helper import csw_query_metadata_by_id
from geosafe.helpers.utils import download_file
from geosafe.tasks import metasearch

__author__ = 'Rizky Maulana Nugraha <lana.pcfre@gmail.com>'

__date__ = '7/28/16'

LOGGER = logging.getLogger(__name__)


def index(request, *args, **kwargs):
    result = None
    if request.method == 'POST':
        form = MetaSearchForm(request.POST)
        if form.is_valid():
            url = form.cleaned_data['csw_url']
            user = form.cleaned_data['user']
            password = form.cleaned_data['password']
            keywords = form.cleaned_data['keywords']

            # set session
            request.session['csw_url'] = url
            request.session['user'] = user
            request.session['password'] = password
            request.session['keywords'] = keywords

            template = 'geosafe/metasearch/metasearch_result.html'

    else:
        form = MetaSearchForm({
            'csw_url': request.session.get('csw_url'),
            'user': request.session.get('user'),
            'password': request.session.get('password'),
            'keywords': request.session.get('keywords')
        })
        template = 'geosafe/metasearch/metasearch.html'

    context = {
        'form': form,
        'result': result
    }
    return render(request, template, context)


def csw_ajax(request, *args, **kwargs):
    if request.method == 'GET':
        csw_url = request.session['csw_url']
        user = request.session['user']
        password = request.session['password']
        keywords = request.session['keywords']
        keywords_query = [fes.PropertyIsLike(
            'csw:AnyText', '%%%s%%' % keywords)]
        if not csw_url:
            return HttpResponseServerError()

        try:
            csw = CatalogueServiceWeb(
                csw_url,
                username=user,
                password=password)
            result = csw.identification.type
            if result == 'CSW':
                page = int(request.GET['page'])
                offset = int(request.GET['offset'])
                per_page = int(request.GET['perPage'])
                csw.getrecords2(
                    typenames='gmd:MD_Metadata',
                    esn='full',
                    outputschema='http://www.isotc211.org/2005/gmd',
                    constraints=keywords_query,
                    startposition=offset,
                    maxrecords=per_page)
                result = []
                for key in csw.records:
                    rec = csw.records[key]
                    res = {}
                    if isinstance(rec, MD_Metadata):
                        res['id'] = rec.identifier
                        res['title'] = rec.identification.title
                        res['inasafe_keywords'] = rec.identification.supplementalinformation
                        if res['inasafe_keywords']:
                            res['inasafe_layer'] = '<inasafe_keywords/>' in res['inasafe_keywords']
                        result.append(res)
            json_result = {
                'records': result,
                'queryRecordCount': csw.results['matches'],
                'totalRecordCount': csw.results['matches']
            }
            return JsonResponse(json_result, safe=False)
        except Exception as e:
            LOGGER.exception(e)
            return HttpResponseServerError()

    return HttpResponseServerError()


def show_add_layer_dialog(request, *args, **kwargs):
    if request.method == 'POST':
        csw_url = request.session['csw_url']
        user = request.session['user']
        password = request.session['password']
        layer_id = request.POST['layer_id']
        try:
            csw = CatalogueServiceWeb(
                csw_url,
                username=user,
                password=password)
            constraints = [
                fes.PropertyIsEqualTo(
                    'dc:identifier', layer_id)
            ]
            result = csw.identification.type
            if result == 'CSW':
                csw.getrecords2(constraints=constraints)
                record = None
                for key in csw.records:
                    rec = csw.records[key]
                    for ref in rec.references:
                        if 'OGC:WCS' in ref['scheme']:
                            rec.type = 'WCS'
                            rec.endpoint = ref['url']
                            record = rec
                            break
                        if 'OGC:WFS' in ref['scheme']:
                            rec.type = 'WFS'
                            rec.endpoint = ref['url']
                            record = rec
                            break
                    if record:
                        break

                if record.type == 'WCS':
                    # get describe coverage
                    # find coverage id from references
                    coverage_id = None
                    version = None
                    for ref in record.references:
                        if 'service=WCS' in ref['url']:
                            url = ref['url']
                            parse_result = urlparse.urlparse(url)
                            query = parse_result.query
                            query_dict = urlparse.parse_qs(query)
                            coverage_id = query_dict['coverageid'][0]
                            version = query_dict['version'][0]
                            if coverage_id and version:
                                break
                    record.service_id = coverage_id
                    record.service_version = version
                elif record.type == 'WFS':
                    typename = None
                    version = None
                    for ref in record.references:
                        if 'service=WFS' in ref['url']:
                            url = ref['url']
                            parse_result = urlparse.urlparse(url)
                            query = parse_result.query
                            query_dict = urlparse.parse_qs(query)
                            typename = query_dict['typename'][0]
                            version = query_dict['version'][0]
                            if typename and version:
                                break

                    record.service_id = typename
                    record.service_version = version
                #     wcs = WebCoverageService(record.endpoint)
                #     result = wcs.getDescribeCoverage(coverage_id)
                context = {
                    'record': record
                }
                return render(
                    request,
                    'geosafe/metasearch/modal/add_layer.html',
                    context)
        except Exception as e:
            return HttpResponseServerError()
    return HttpResponseServerError()


def show_metadata(request, *args, **kwargs):
    if request.method == 'GET':
        csw_url = request.session['csw_url']
        user = request.session['user']
        password = request.session['password']
        layer_id = request.GET['layer_id']
        try:
            record = csw_query_metadata_by_id(
                csw_url,
                layer_id,
                username=user,
                password=password)

            context = {
                'metadata': record.xml
            }

            return render(
                request,
                'geosafe/metasearch/modal/layer_metadata.html',
                context)

        except Exception as e:
            return HttpResponseServerError()
    return HttpResponseServerError()


def add_layer(request, *args, **kwargs):
    result = {
        'success': False
    }
    if request.method == 'POST':
        endpoint = request.POST['endpoint']
        type = request.POST['type']
        csw_url = request.session['csw_url']
        user = request.session['user']
        password = request.session['password']
        identifier = request.POST['identifier']
        service_id = request.POST['service_id']
        service_version = request.POST['service_version']
        minx = request.POST['minx']
        miny = request.POST['miny']
        maxx = request.POST['maxx']
        maxy = request.POST['maxy']
        bbox = [
            minx, miny,
            maxx, maxy
        ]
        try:
            record = csw_query_metadata_by_id(
                csw_url,
                identifier,
                username=user,
                password=password)
            metadata_string = record.xml if record else None
            if type == 'WCS':
                metasearch.add_wcs_layer.delay(
                    endpoint,
                    service_version,
                    service_id,
                    metadata_string=metadata_string,
                    bbox=bbox,
                    user=user, password=password)
            elif type == 'WFS':
                # bbox = (
                #     float(minx), float(miny),
                #     float(maxx), float(maxy)
                # )
                metasearch.add_wfs_layer.delay(
                    endpoint,
                    service_version,
                    service_id,
                    metadata_string=metadata_string,
                    bbox=bbox,
                    user=user, password=password)
            result['success'] = True
        except Exception as e:
            pass
    return HttpResponse(
        json.dumps(result), content_type='application/json')


def wfs_proxy(request, *args, **kwargs):
    if request.method == 'GET':
        endpoint = request.GET['endpoint']
        typename = request.GET['typename']
        user = request.session['user']
        password = request.session['password']

        endpoint_parsed = urlparse.urlparse(endpoint)
        q_dict = {
            'version': '1.0.0',
            'typename': typename,
            'outputFormat': 'json',
            'request': 'GetFeature',
            'service': 'WFS',
            'srsName': 'EPSG:4326',
        }
        parsed_url = urlparse.ParseResult(
            scheme=endpoint_parsed.scheme,
            netloc=endpoint_parsed.netloc,
            path=endpoint_parsed.path,
            params=None,
            query=urllib.urlencode(q_dict),
            fragment=None
        )
        LOGGER.info('Proxy to url: %s' % parsed_url.geturl())
        tmpfile = download_file(parsed_url.geturl(), user=user, password=password)
        with open(tmpfile) as f:
            return HttpResponse(f.read(), content_type='application/json')
    return HttpResponseServerError()
