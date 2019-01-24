# coding=utf-8

from __future__ import absolute_import

import glob
import json
import logging
import os
import shutil
import tempfile
import time
import urlparse
from datetime import datetime
from zipfile import ZipFile

import requests
from celery import chain
from celery.result import allow_join_result, AsyncResult
from django.core.serializers.json import DjangoJSONEncoder
from django.core.urlresolvers import reverse
from django.db.models.query_utils import Q
from django.core.files import File
from lxml import etree
from lxml.etree import XML, Element

from geonode.base.models import ResourceBase
from geonode.layers.models import Layer, cov_exts, vec_exts
from geonode.layers.utils import file_upload
from geonode.qgis_server.helpers import qgis_server_endpoint
from geosafe.app_settings import settings
from geosafe.celery_app import app
from geosafe.helpers.utils import (
    download_file, get_layer_path, get_impact_path,
    copy_inasafe_metadata, send_analysis_result_email)
from geosafe.models import Analysis, Metadata, \
    ISO_METADATA_INASAFE_KEYWORD_TAG, \
    ISO_METADATA_INASAFE_PROVENANCE_KEYWORD_TAG, AnalysisTaskInfo
from geosafe.tasks.headless.analysis import (
    get_keywords, generate_report, run_analysis, RESULT_SUCCESS)
from geosafe.utils import substitute_layer_order

__author__ = 'lucernae'


LOGGER = logging.getLogger(__name__)


@app.task(
    name='geosafe.tasks.analysis.inasafe_metadata_fix',
    queue='geosafe')
def inasafe_metadata_fix(layer_id):
    """Attempt to fix problem of InaSAFE metadata.

    This fix is needed to make sure InaSAFE metadata is persisted in GeoNode
    and is used correctly by GeoSAFE.

    This bug happens because InaSAFE metadata implement wrong schema type in
    supplementalInformation.

    :param layer_id: layer ID
    :type layer_id: int
    :return:
    """

    # Take InaSAFE keywords from xml metadata *file*
    try:
        instance = Layer.objects.get(id=layer_id)
        xml_file = instance.upload_session.layerfile_set.get(name='xml')

        # if xml file exists, check supplementalInformation field
        namespaces = {
            'gmd': 'http://www.isotc211.org/2005/gmd',
            'gco': 'http://www.isotc211.org/2005/gco'
        }
        content = xml_file.file.read()
        root = XML(content)
        # supplemental_info = root.xpath(
        #     '//gmd:supplementalInformation',
        #     namespaces=namespaces)[0]

        # Check that it contains InaSAFE metadata
        inasafe_el = root.xpath(ISO_METADATA_INASAFE_KEYWORD_TAG)
        inasafe_provenance_el = root.xpath(
            ISO_METADATA_INASAFE_PROVENANCE_KEYWORD_TAG)

        # Take InaSAFE metadata
        if not inasafe_el:
            # Do nothing if InaSAFE tag didn't exists
            return

        # Take root xml from layer metadata_xml field
        layer_root_xml = XML(instance.metadata_xml)
        layer_sup_info = layer_root_xml.xpath(
            '//gmd:supplementalInformation',
            namespaces=namespaces)[0]

        char_string_tagname = '{gco}CharacterString'.format(**namespaces)

        layer_sup_info_content = layer_sup_info.find(char_string_tagname)
        if layer_sup_info_content is None:
            # Insert gco:CharacterString value
            el = Element(char_string_tagname)
            layer_sup_info.insert(0, el)

        # put InaSAFE keywords after CharacterString
        layer_inasafe_meta_content = layer_sup_info.find('inasafe')
        if layer_inasafe_meta_content is not None:
            # Clear existing InaSAFE keywords, replace with new one
            layer_sup_info.remove(layer_inasafe_meta_content)
        layer_sup_info.insert(1, inasafe_el)

        # provenance only shows up on impact layers
        layer_inasafe_meta_provenance = layer_sup_info.find(
            'inasafe_provenance')
        if inasafe_provenance_el is not None:
            if layer_inasafe_meta_provenance is not None:
                # Clear existing InaSAFE keywords, replace with new one
                layer_sup_info.remove(layer_inasafe_meta_provenance)
            layer_sup_info.insert(1, inasafe_provenance_el)

        # write back to resource base so the same thing returned by csw
        resources = ResourceBase.objects.filter(
            id=instance.resourcebase_ptr.id)
        resources.update(
            metadata_xml=etree.tostring(layer_root_xml, pretty_print=True))

        # update qgis server xml file
        with open(xml_file.file.path, mode='w') as f:
            f.write(etree.tostring(layer_root_xml, pretty_print=True))

        qgis_layer = instance.qgis_layer
        qgis_xml_file = '{prefix}.xml'.format(
            prefix=qgis_layer.qgis_layer_path_prefix)
        with open(qgis_xml_file, mode='w') as f:
            f.write(etree.tostring(layer_root_xml, pretty_print=True))

        # update InaSAFE keywords cache

        metadata, created = Metadata.objects.get_or_create(layer=instance)
        inasafe_metadata_xml = etree.tostring(inasafe_el, pretty_print=True)
        if inasafe_provenance_el:
            inasafe_metadata_xml += '\n'
            inasafe_metadata_xml += etree.tostring(
                inasafe_provenance_el, pretty_print=True)
        metadata.keywords_xml = inasafe_metadata_xml
        metadata.save()

    except Exception as e:
        LOGGER.debug(e)
        pass


@app.task(
    name='geosafe.tasks.analysis.create_metadata_object',
    queue='geosafe',
    bind=True)
def create_metadata_object(self, layer_id):
    """Create metadata object of a given layer

    :param self: Celery task instance
    :type self: celery.app.task.Task

    :param layer_id: layer ID
    :type layer_id: int

    :return: True if success
    :rtype: bool
    """
    try:
        layer = Layer.objects.get(id=layer_id)
        # Now that layer exists, get InaSAFE keywords
        using_direct_access = (
            hasattr(settings, 'INASAFE_LAYER_DIRECTORY') and
            settings.INASAFE_LAYER_DIRECTORY)
        if using_direct_access and not layer.remote_service:
            # If direct disk access were configured, then use it.
            layer_url = urlparse.urljoin('file://', get_layer_path(layer))
        else:
            # InaSAFE Headless celery will download metadata from url
            layer_url = reverse(
                'geosafe:layer-metadata',
                kwargs={'layer_id': layer.id})
            layer_url = urlparse.urljoin(settings.GEONODE_BASE_URL, layer_url)
        # Execute in chain:
        # - Get InaSAFE keywords from InaSAFE worker
        # - Set Layer metadata according to InaSAFE keywords
        get_keywords_queue = get_keywords.queue
        set_layer_purpose_queue = set_layer_purpose.queue
        tasks_chain = chain(
            get_keywords.s(layer_url).set(
                queue=get_keywords_queue),
            set_layer_purpose.s(layer_id).set(
                queue=set_layer_purpose_queue)
        )
        tasks_chain.delay()
    except Layer.DoesNotExist as e:
        # Perhaps layer wasn't saved yet.
        # Retry later
        LOGGER.debug('Layer with id: {0} not saved yet'.format(layer_id))
        self.retry(exc=e, countdown=5)
    except AttributeError as e:
        # This signal is called too early
        # We can't get layer file
        pass
    return True


@app.task(
    name='geosafe.tasks.analysis.set_layer_purpose',
    queue='geosafe')
def set_layer_purpose(keywords, layer_id):
    """Set layer keywords based on what InaSAFE gave.

    :param keywords: Keywords taken from InaSAFE metadata.
    :type keywords: dict

    :param layer_id: layer ID
    :type layer_id: int

    :return: True if success
    :rtype: bool
    """
    metadata, created = Metadata.objects.get_or_create(layer_id=layer_id)

    layer_purpose = keywords.get('layer_purpose', None)
    metadata.layer_purpose = (
        layer_purpose if (
            layer_purpose != 'hazard_aggregation_summary') else (
            'impact_analysis'))
    metadata.category = keywords.get(metadata.layer_purpose, None)
    metadata.keywords_json = json.dumps(keywords, cls=DjangoJSONEncoder)
    Metadata.objects.filter(pk=metadata.pk).update(
        layer_purpose=metadata.layer_purpose,
        keywords_json=metadata.keywords_json,
        category=metadata.category)

    return True


@app.task(
    name='geosafe.tasks.analysis.clean_impact_result',
    queue='geosafe')
def clean_impact_result():
    """Clean all the impact results not marked kept

    :return:
    """
    query = Q(keep=True)
    for a in Analysis.objects.filter(~query):
        a.delete()

    for i in Metadata.objects.filter(layer_purpose='impact'):
        try:
            Analysis.objects.get(impact_layer=i.layer)
        except Analysis.DoesNotExist:
            i.delete()


def prepare_aggregation_filter(analysis_id):
    """Filter current aggregation layer.

    :param analysis_id: analysis id of the object
    :type analysis_id: int

    :return: uri path of filtered aggregation layer
    :rtype: basestring
    """
    analysis = Analysis.objects.get(id=analysis_id)
    if not analysis.aggregation_layer:
        return None
    aggregation_layer = analysis.aggregation_layer.qgis_layer

    endpoint = qgis_server_endpoint(internal=True)

    # construct WFS filter query_params
    query_string = {
        'MAP': aggregation_layer.qgis_project_path,
        'SERVICE': 'WFS',
        'REQUEST': 'GetFeature',
        'TYPENAME': aggregation_layer.layer.name,
        'OUTPUTFORMAT': 'GeoJSON'
    }
    filter_string = None
    if analysis.aggregation_filter:
        try:
            filter_dict = json.loads(analysis.aggregation_filter)

            property_name = filter_dict['property_name']
            property_values = filter_dict['values']

            like_statement = []
            for val in property_values:
                like_statement.append(
                    '<PropertyIsLike>'
                    '<PropertyName>{name}</PropertyName>'
                    '<Literal>{value}</Literal>'
                    '</PropertyIsLike>'.format(name=property_name, value=val)
                )
            filter_string = '<Filter>{filter}</Filter>'.format(
                filter=''.join(like_statement))

        except BaseException as e:
            LOGGER.error(e)
            # something happened, don't use filter
            filter_string = None

    if filter_string:
        query_string['FILTER'] = filter_string

    response = requests.get(endpoint, params=query_string)
    if response.ok:
        try:
            # try parse geojson
            geojson = response.json()
            # if successful, create temporary inasafe layer
            prefix_name = '{layer_name}_'.format(
                layer_name=aggregation_layer.qgis_layer_name)
            # the files needs to be at the same dir where aggregation layer is
            dirname = os.path.dirname(aggregation_layer.base_layer_path)
            temp_aggregation = tempfile.mkstemp(
                    prefix=prefix_name,
                    suffix='.geojson',
                    dir=dirname)[1]
            with open(temp_aggregation, mode='w+b') as f:
                # Re dump just to be safe
                f.write(json.dumps(geojson))
            filename, _ = os.path.splitext(os.path.basename(temp_aggregation))
            # copy metadata
            copy_inasafe_metadata(
                aggregation_layer.base_layer_path, dirname, filename)

            # Update filtered aggregation location
            Analysis.objects.filter(id=analysis_id).update(
                filtered_aggregation=temp_aggregation)

            # Return temporary path
            return get_layer_path(temp_aggregation)
        except BaseException as e:
            LOGGER.error(e)
            # Failed to filter aggregation layer somehow

    # when everything fails
    return get_layer_path(analysis.aggregation_layer)


def prepare_analysis(analysis_id):
    """Prepare and run analysis

    :param analysis_id: analysis id of the object
    :type analysis_id: int

    :return: Celery Async Result
    :rtype: celery.result.AsyncResult
    """
    analysis = Analysis.objects.get(id=analysis_id)

    # Set analysis start time
    Analysis.objects.filter(id=analysis_id).update(
        start_time=datetime.now())
    analysis.refresh_from_db()

    hazard = get_layer_path(analysis.hazard_layer)
    exposure = get_layer_path(analysis.exposure_layer)
    aggregation = (
        get_layer_path(analysis.aggregation_layer) if (
            analysis.aggregation_layer) else None)

    # Create temporary aggregation layer if aggregation filter exists
    if aggregation:
        aggregation = prepare_aggregation_filter(analysis_id)

    # Execute analysis in chains:
    # - Run analysis
    # - Process analysis result
    tasks_chain = chain(
        run_analysis.s(
            hazard, exposure, aggregation,
            locale=analysis.language_code).set(
            queue=run_analysis.queue).set(
            time_limit=settings.INASAFE_ANALYSIS_RUN_TIME_LIMIT),
        process_impact_result.s(analysis_id).set(
            queue=process_impact_result.queue),
        clean_up_temp_aggregation.s(analysis_id).set(
            queue=clean_up_temp_aggregation.queue)
    )
    result = tasks_chain.delay()
    # Parent information will be lost later.
    # What we should save is the run_analysis task result as this is the
    # chain's parent
    while result.parent:
        result = result.parent
    return result


@app.task(
    name='geosafe.tasks.analysis.process_impact_result',
    queue='geosafe',
    bind=True)
def process_impact_result(self, impact_result, analysis_id):
    """Extract impact analysis after running it via InaSAFE-Headless celery

    :param self: Task instance
    :type self: celery.task.Task

    :param impact_result: A dictionary of output's layer key and Uri with
        status and message.
    :type impact_result: dict

    :param analysis_id: analysis id of the object
    :type analysis_id: int

    :return: True if success
    :rtype: bool
    """
    # Track the current task_id
    analysis = Analysis.objects.get(id=analysis_id)

    success = False
    report_success = False
    impact_url = None
    impact_path = None

    if impact_result['status'] == RESULT_SUCCESS:
        impact_url = (
            impact_result['output'].get('impact_analysis') or
            impact_result['output'].get('hazard_aggregation_summary'))
        analysis_summary_url = (
            impact_result['output'].get('analysis_summary'))

        custom_template_path = prepare_custom_template(analysis, impact_url)

        layer_order = prepare_context_layer_order(analysis, impact_url)

        # generate report when analysis has ran successfully
        result = generate_report.delay(
            impact_url,
            # If it is None, it will use default headless template
            custom_report_template_uri=custom_template_path,
            custom_layer_order=layer_order,
            locale=analysis.language_code)

        retries = 10
        for r in range(retries):
            try:

                with allow_join_result():
                    report_metadata = result.get().get('output', {})
                break
            except BaseException as e:
                if result.state == 'FAILURE':
                    raise e
                LOGGER.exception(e)
                time.sleep(5)
                result = AsyncResult(result.id)
                if r >= retries - 1:
                    # Generate report has failed.
                    # We need to reraise the error so we know something is
                    # wrong
                    raise e

        for product_key, products in report_metadata.iteritems():
            for report_key, report_url in products.iteritems():
                report_url = download_file(report_url, direct_access=True)
                report_metadata[product_key][report_key] = report_url

        # decide if we are using direct access or not
        impact_url = get_impact_path(impact_url)

        # download impact layer path
        impact_path = download_file(impact_url, direct_access=True)
        dir_name = os.path.dirname(impact_path)
        is_zipfile = os.path.splitext(impact_path)[1].lower() == '.zip'
        if is_zipfile:
            # Extract the layer first
            with ZipFile(impact_path) as zf:
                zf.extractall(path=dir_name)
                for name in zf.namelist():
                    basename, ext = os.path.splitext(name)
                    if ext in cov_exts + vec_exts:
                        # process this in the for loop to make sure it
                        # works only when we found the layer
                        success = process_impact_layer(
                            analysis, dir_name, basename, name)
                        report_success = process_impact_report(
                            analysis, report_metadata)
                        break

                # cleanup
                for name in zf.namelist():
                    filepath = os.path.join(dir_name, name)
                    try:
                        os.remove(filepath)
                    except BaseException:
                        pass
        else:
            # It means it is accessing an shp or tif directly
            analysis_summary_filename = os.path.basename(analysis_summary_url)
            impact_filename = os.path.basename(impact_path)
            impact_basename, ext = os.path.splitext(impact_filename)
            success = process_impact_layer(
                analysis, dir_name, impact_basename,
                impact_filename, analysis_summary_filename)
            report_success = process_impact_report(analysis, report_metadata)

            # cleanup
            for name in os.listdir(dir_name):
                filepath = os.path.join(dir_name, name)
                is_file = os.path.isfile(filepath)
                should_delete = name.split('.')[0] == impact_basename
                if is_file and should_delete:
                    try:
                        os.remove(filepath)
                    except BaseException:
                        pass

    # cleanup
    try:
        os.remove(impact_path)
    except BaseException:
        pass

    if not success:
        LOGGER.info('No impact layer found in {0}'.format(impact_url))

    if not report_success:
        LOGGER.info('No impact report generated.')

    send_analysis_result_email(analysis)

    return success


def prepare_context_layer_order(analysis, impact_url):
    """This helper method will prepare context layer order.

    This order will be used in report generation.

    :param analysis: Analysis object to prepare
    :type analysis: Analysis

    :param impact_url: The impact URI returned by Headless
    :type impact_url: basestring

    :return: The layer order data source uri to be loaded.
        It will contain a list of QGIS data source. It can be a filepath or
        any valid QGIS datasource URI. example:
        [
            '/path/to/impact',
            '/path/to/hazard',
            'type=xyz&url=http://basemap.url/z/x/y.png|qgis_provider=wms'
        ]
    :rtype: list(basestring)
    """
    # define the layer order of the map report
    layer_order = (
        list(settings.REPORT_LAYER_ORDER)
        if settings.REPORT_LAYER_ORDER else None)
    if layer_order:
        # Regular context
        layer_source = {
            'impact': impact_url,
            'hazard': get_layer_path(analysis.hazard_layer),
            'exposure': get_layer_path(analysis.exposure_layer)
        }
        # Aggregation layer
        if analysis.aggregation_layer:
            layer_source.update({
                'aggregation': get_layer_path(analysis.aggregation_layer)})
        else:
            try:
                layer_order.remove('@aggregation')
            except ValueError:
                pass

        # Basemap
        if settings.REPORT_DEFAULT_BASEMAP:
            try:
                basemap_url = settings.REPORT_DEFAULT_BASEMAP

                basemap_source_uri = 'type=xyz&url={0}|qgis_provider=wms' \
                    .format(basemap_url)
                layer_source['basemap'] = basemap_source_uri
            except BaseException:
                pass

        if 'basemap' not in layer_source:
            try:
                layer_order.remove('@basemap')
            except ValueError:
                pass

        # Prepare styles
        # Impact layer will have default style defined, so we don't deal
        # with it again.

        # GeoNode layer will have default style defined in QGISServerStyle
        layer_list = [
            analysis.hazard_layer,
            analysis.exposure_layer
        ]
        if analysis.aggregation_layer:
            layer_list.append(analysis.aggregation_layer)

        for l in layer_list:
            l.qgis_layer.extract_default_style_to_qml()

        layer_order = substitute_layer_order(layer_order, layer_source)
    return layer_order


def prepare_custom_template(analysis, impact_url):
    """This helper method will prepare custom template.

    This template will be included in report generations.

    :param analysis: Analysis object to prepare
    :type analysis: Analysis

    :param impact_url: The impact URI returned by Headless
    :type impact_url: basestring

    :return: The location of custom template path, relative to
        Headless service
    :rtype: basestring
    """
    # define the location of qgis template file
    custom_template_path = None
    if analysis.custom_template:
        # resolve headless impact layer directory on django environment
        filename = os.path.basename(analysis.custom_template)
        dirname = os.path.dirname(impact_url)
        # template path from headless service
        headless_template_path = os.path.join(dirname, filename)
        # template path in GeoSAFE
        template_path = get_impact_path(headless_template_path)
        # each analysis path is unique, so we can just copy/overwrite
        # the template
        shutil.copy(analysis.custom_template, template_path)

        # pass on template path according to headless service
        custom_template_path = headless_template_path
    return custom_template_path


@app.task(
    name='geosafe.tasks.analysis.clean_up_temp_aggregation',
    queue='geosafe')
def clean_up_temp_aggregation(process_impact_result, analysis_id):
    """Clean up generated aggregation filter regardless of analysis result.

    :param process_impact_result:
    :param analysis_id:
    :return:
    """
    # check does analysis uses aggregation filter
    analysis = Analysis.objects.get(id=analysis_id)
    filtered_aggregation = analysis.filtered_aggregation
    if filtered_aggregation and os.path.exists(filtered_aggregation):
        basename, _ = os.path.splitext(filtered_aggregation)
        for p in glob.glob('{basename}.*'.format(basename=basename)):
            os.remove(p)
    return True


def process_impact_layer(
        analysis,
        dir_name,
        impact_basename,
        impact_filename,
        analysis_summary_filename=None):
    """Internal function to actually process the layer.

    :param analysis: Analysis object
    :type analysis: Analysis

    :param basename: basename (without dirname and extension)
    :type basename: str

    :param dir_name: dirname
    :type dir_name: str

    :param name: the name of the layer path
    :type name: str

    :return: True if success
    """
    # If User is anonymous then let admin upload the impact layer
    if analysis.user.is_anonymous() or \
            analysis.user.username == 'AnonymousUser' or \
            analysis.user.id == -1:
        # Set to none
        # File upload will think Admin is the uploader
        upload_user = None
    else:
        # Retain owner to person who initiate the analysis
        upload_user = analysis.user

    # Upload impact layer
    saved_layer = file_upload(
        os.path.join(dir_name, impact_filename),
        user=upload_user)

    # add analysis summary file
    analysis_summary_basename, type_name = os.path.split(
        analysis_summary_filename)
    with open(os.path.join(dir_name, analysis_summary_filename), 'rb') as f:
        saved_layer.upload_session.layerfile_set.create(
            name=type_name,
            base=analysis_summary_basename,
            file=File(f, name=('%s.%s' % (saved_layer.name, type_name))))

    saved_layer.set_default_permissions()
    if analysis.user_title:
        layer_name = analysis.user_title
    else:
        layer_name = analysis.get_default_impact_title()
    saved_layer.title = layer_name
    saved_layer.save()
    current_impact = None
    if analysis.impact_layer:
        current_impact = analysis.impact_layer
    analysis.impact_layer = saved_layer
    analysis.task_state = 'SUCCESS'
    analysis.end_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    analysis.save(update_fields=[
        'task_id',
        'task_state',
        'end_time',
        'impact_layer'
    ])
    analysis.refresh_from_db()
    task_info = AnalysisTaskInfo.create_from_analysis(analysis)
    task_info.update_info()
    if current_impact:
        current_impact.delete()
    success = True
    return success


def process_impact_report(analysis, report_metadata):
    """Internal method to process impact report.

    :param analysis: Analysis object
    :type analysis: Analysis

    :param report_metadata: Impact report metadata
    :type report_metadata: dict

    :return: True if success
    """
    success = False
    try:
        # upload using document upload form post request
        # TODO: find out how to upload document using post request

        assign_report = {
            'impact-report-pdf': analysis.assign_report_table
        }

        if analysis.custom_template:
            # If we provide custom_template, search custom template key
            assign_report['map-report'] = analysis.assign_report_map
        else:
            # If not, pick default headless report
            assign_report['inasafe-map-report-portrait'] = \
                analysis.assign_report_map

        # List of tags of PDF report product
        pdf_product_tag = report_metadata['pdf_product_tag']
        for metadata_key in pdf_product_tag.keys():
            for assign_key, assign_method in assign_report.iteritems():

                # If the key we search doesn't exists in metadata,
                # just continue
                if assign_key not in metadata_key:
                    continue

                # if it is exists, check the file is exists too
                report_path = pdf_product_tag[metadata_key]
                # convert to GeoSAFE path location
                report_path = get_impact_path(report_path)
                if os.path.exists(report_path):
                    # save the path location
                    assign_method(report_path)

        # Avoid race conditions
        analysis.save(update_fields=['report_map', 'report_table'])

        # reference to impact layer
        # TODO: find out how to upload document using post request first

        # delete all possible temporary styles being used to generate report
        layer_list = [
            analysis.hazard_layer,
            analysis.exposure_layer
        ]
        if analysis.aggregation_layer:
            layer_list.append(analysis.aggregation_layer)

        for l in layer_list:
            l.qgis_layer.remove_qml_file_style()

        success = True
    except Exception as e:
        LOGGER.debug(e)
        pass

    return success


@app.task(
    name='geosafe.tasks.analysis.check_tasks',
    queue='geosafe')
def check_tasks():
    """Check the state of analysis task and gather info.
    """
    analysis = Analysis.objects.exclude(task_state='SUCCESS')

    for a in analysis:

        try:
            task_info = a.task_info
        except AnalysisTaskInfo.DoesNotExist:
            task_info = AnalysisTaskInfo.create_from_analysis(a)

        task_info.update_info()
