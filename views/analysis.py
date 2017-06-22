import json
import logging
import os
import tempfile
from zipfile import ZipFile

from django.core.urlresolvers import reverse
from django.db.models.expressions import F
from django.db.models.query_utils import Q
from django.http.response import HttpResponseServerError, HttpResponse, \
    HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.views.generic import (
    ListView, CreateView, DetailView)

from geonode.layers.models import Layer
from geosafe.app_settings import settings
from geosafe.forms import (AnalysisCreationForm)
from geosafe.helpers.impact_summary.polygon_people_summary import \
    PolygonPeopleSummary
from geosafe.helpers.impact_summary.population_summary import \
    PopulationSummary
from geosafe.helpers.impact_summary.road_summary import RoadSummary
from geosafe.helpers.impact_summary.structure_summary import StructureSummary
from geosafe.helpers.impact_summary.summary_base import ImpactSummary
from geosafe.helpers.utils import get_layer_path
from geosafe.models import Analysis, Metadata
from geosafe.signals import analysis_post_save
from geosafe.tasks.headless.analysis import filter_impact_function

LOGGER = logging.getLogger("geosafe")


logger = logging.getLogger("geonode.geosafe.analysis")


def retrieve_layers(purpose, category=None, bbox=None):
    """List all required layers.

    :param purpose: InaSAFE layer purpose that want to be filtered.
        Can be 'hazard', 'exposure', or 'impact'
    :type purpose: str

    :param category: InaSAFE layer category that want to be filtered.
        Vary, depend on purpose. Example: 'flood', 'tsunami'
    :type category: str

    :param bbox: Layer bbox to filter
    :type bbox: (float, float, float, float)

    :returns: filtered layer and a status for filtered.
        Status will return True, if it is filtered.
    :rtype: list[Layer], bool

    """

    if not category:
        category = None
    if bbox:
        bbox = json.loads(bbox)
        # normalize bbox
        if bbox[2] < bbox[0]:
            temp = bbox[0]
            bbox[0] = bbox[2]
            bbox[2] = temp
        if bbox[3] < bbox[1]:
            temp = bbox[1]
            bbox[1] = bbox[3]
            bbox[3] = temp
        intersect = (
            Q(layer__bbox_x0__lte=bbox[2]) &
            Q(layer__bbox_x1__gte=bbox[0]) &
            Q(layer__bbox_y0__lte=bbox[3]) &
            Q(layer__bbox_y1__gte=bbox[1]) &
            Q(layer__bbox_x0__lte=F('layer__bbox_x1')) &
            Q(layer__bbox_y0__lte=F('layer__bbox_y1'))
        ) | (
            # in case of swapped value
            Q(layer__bbox_x0__lte=bbox[2]) &
            Q(layer__bbox_x1__gte=bbox[0]) &
            Q(layer__bbox_y0__gte=bbox[1]) &
            Q(layer__bbox_y1__lte=bbox[3]) &
            Q(layer__bbox_x0__lte=F('layer__bbox_x1')) &
            Q(layer__bbox_y1__lte=F('layer__bbox_y0'))
        )
        metadatas = Metadata.objects.filter(
            Q(layer_purpose=purpose),
            Q(category=category),
            intersect
        )
        layer_count = Metadata.objects.filter(
            layer_purpose=purpose,
            category=category).count()
        if len(metadatas) == layer_count:
            # it means unfiltered by bbox
            is_filtered = False
        else:
            # it means filtered by bbox
            is_filtered = True
    else:
        metadatas = Metadata.objects.filter(
            layer_purpose=purpose, category=category)
        is_filtered = False
    return [m.layer for m in metadatas], is_filtered


class AnalysisCreateView(CreateView):
    model = Analysis
    form_class = AnalysisCreationForm
    template_name = 'geosafe/analysis/create.html'
    context_object_name = 'analysis'

    @classmethod
    def options_panel_dict(cls, bbox=None):
        """Prepare a dictionary to be used in the template view

        :return: dict containing metadata for options panel
        :rtype: dict
        """
        purposes = [
            {
                'name': 'hazard',
                'categories': ['flood', 'tsunami', 'earthquake', 'volcano',
                               'volcanic-ash'],
                'list_titles': [
                    'Select a flood layer',
                    'Select a tsunami layer',
                    'Select an earthquake layer',
                    'Select a volcano layer',
                    'Select a volcanic ash layer',
                ]
            },
            {
                'name': 'exposure',
                'categories': [
                    'population',
                    'road',
                    'structure',
                    # 'land_cover',
                    ],
                'list_titles': [
                    'Select a population layer',
                    'Select a roads layer',
                    'Select a structure layer',
                    # 'Select a land_cover layer',
                ]
            }
        ]
        sections = []
        for p in purposes:
            categories = []
            is_section_filtered = False
            for idx, c in enumerate(p.get('categories')):
                layers, is_filtered = retrieve_layers(p.get('name'), c, bbox=bbox)
                if is_filtered:
                    is_section_filtered = True
                category = {
                    'name': c,
                    'layers': layers,
                    'total_layers': len(layers),
                    'filter_status': (
                        'filtered' if is_filtered else 'unfiltered'),
                    'list_title': p.get('list_titles')[idx]
                }
                categories.append(category)
            section = {
                'name': p.get('name'),
                'total_layers': sum(
                    [len(c['layers']) for c in categories]),
                'filter_status': (
                    'filtered' if is_section_filtered else 'unfiltered'),
                'categories': categories
            }
            sections.append(section)

        impact_layers, is_filtered = retrieve_layers('impact', bbox=bbox)
        total_impact_layers = len(impact_layers)
        sections.append({
            'name': 'impact',
            'total_layers': total_impact_layers,
            'filter_status': (
                'filtered' if is_filtered else 'unfiltered'),
            'categories': [
                {
                    'name': 'impact',
                    'layers': impact_layers,
                    'total_layers': total_impact_layers,
                }
            ]
        })
        return sections

    def get_context_data(self, **kwargs):
        sections = self.options_panel_dict()
        try:
            analysis = Analysis.objects.get(id=self.kwargs.get('pk'))
        except:
            analysis = None
        context = super(AnalysisCreateView, self).get_context_data(**kwargs)
        context.update(
            {
                'sections': sections,
                'analysis': analysis,
                'report_type': None,
            }
        )
        return context

    def get_form(self, form_class):
        kwargs = self.get_form_kwargs()
        kwargs.update({
            'impact_functions': Analysis.impact_function_list()
        })
        logger.error(kwargs)
        return form_class(**kwargs)

    def post(self, request, *args, **kwargs):
        retval = super(AnalysisCreateView, self).post(request, *args, **kwargs)

        form_class = self.get_form_class()
        form = self.get_form(form_class)
        if form.is_valid():
            # return retval
            return HttpResponse(json.dumps({
                'success': True,
                'redirect': self.get_success_url()
            }), content_type='application/json')
        else:
            return HttpResponse(json.dumps({
                'success': False
            }), content_type='application/json')

    def get_success_url(self):
        kwargs = {
            'pk': self.object.pk
        }
        return reverse('geosafe:analysis-create', kwargs=kwargs)

    def get_form_kwargs(self):
        kwargs = super(AnalysisCreateView, self).get_form_kwargs()
        kwargs.update({'user': self.request.user})
        return kwargs


class AnalysisListView(ListView):
    model = Analysis
    template_name = 'geosafe/analysis/list.html'
    context_object_name = 'analysis_list'
    queryset = Analysis.objects.all().order_by("-impact_layer__date")

    def get_context_data(self, **kwargs):
        context = super(AnalysisListView, self).get_context_data(**kwargs)
        context.update({'user': self.request.user})
        return context


class AnalysisDetailView(DetailView):
    model = Analysis
    template_name = 'geosafe/analysis/detail.html'
    context_object_name = 'analysis'

    def get_context_data(self, **kwargs):
        context = super(AnalysisDetailView, self).get_context_data(**kwargs)
        return context


def impact_function_filter(request):
    """Ajax Request for filtered available IF
    """
    if request.method != 'GET':
        return HttpResponseBadRequest()

    exposure_id = request.GET.get('exposure_id')
    hazard_id = request.GET.get('hazard_id')

    if not (exposure_id and hazard_id):
        return HttpResponse(
            json.dumps([]), content_type="application/json")

    try:
        hazard_layer = Layer.objects.get(id=hazard_id)
        exposure_layer = Layer.objects.get(id=exposure_id)

        hazard_url = get_layer_path(hazard_layer)
        exposure_url = get_layer_path(exposure_layer)

        async_result = filter_impact_function.delay(
            hazard_url,
            exposure_url)

        impact_functions = async_result.get()

        return HttpResponse(
            json.dumps(impact_functions), content_type="application/json")
    except Exception as e:
        LOGGER.exception(e)
        raise HttpResponseServerError()


def layer_tiles(request):
    """Ajax request to get layer's url to show in the map.
    """
    if request.method != 'GET':
        raise HttpResponseBadRequest
    layer_id = request.GET.get('layer_id')
    if not layer_id:
        raise HttpResponseBadRequest
    try:
        layer = Layer.objects.get(id=layer_id)
        context = {
            'layer_tiles_url': layer.get_tiles_url(),
            'layer_bbox_x0': float(layer.bbox_x0),
            'layer_bbox_x1': float(layer.bbox_x1),
            'layer_bbox_y0': float(layer.bbox_y0),
            'layer_bbox_y1': float(layer.bbox_y1),
            'layer_name': layer.name,
            'legend_url': layer.get_legend_url()
        }

        return HttpResponse(
            json.dumps(context), content_type="application/json"
        )
    except Exception as e:
        LOGGER.exception(e)
        raise HttpResponseServerError


def layer_metadata(request, layer_id):
    """request to get layer's xml metadata"""
    if request.method != 'GET':
        return HttpResponseBadRequest()
    if not layer_id:
        return HttpResponseBadRequest()
    try:
        layer = Layer.objects.get(id=layer_id)
        base_file, _ = layer.get_base_file()
        if not base_file:
            return HttpResponseServerError()
        base_file_path, _ = os.path.splitext(base_file.file.path)
        xml_file_path = base_file_path + '.xml'
        if not os.path.exists(xml_file_path):
            return HttpResponseServerError()
        with open(xml_file_path) as f:
            response = HttpResponse(f.read(), content_type='text/xml')
            response['Content-Disposition'] = (
                'attachment; filename="{filename}.xml"'.format(
                    filename=base_file_path))
            return response

    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def layer_archive(request, layer_id):
    """request to get layer's zipped archive"""
    if request.method != 'GET':
        return HttpResponseBadRequest()

    if not layer_id:
        return HttpResponseBadRequest()

    try:
        layer = Layer.objects.get(id=layer_id)
        tmp = tempfile.mktemp()
        with ZipFile(tmp, mode='w') as zf:
            for layer_file in layer.upload_session.layerfile_set.all():
                base_name = os.path.basename(layer_file.file.name)
                zf.writestr(
                    base_name,
                    layer_file.file.read())

        base_file, _ = layer.get_base_file()
        base_file_name, _ = os.path.splitext(
            os.path.basename(base_file.file.path))
        with open(tmp) as f:
            response = HttpResponse(f.read(), content_type='application/zip')
            response['Content-Disposition'] = (
                'attachment; filename="{filename}.zip"'.format(
                    filename=base_file_name))
            return response

    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def is_bbox_intersects(bbox_1, bbox_2):
    """

    bbox is in the format: (x0,y0, x1, y1)

    :param bbox_1:
    :param bbox_2:
    :return:
    """
    points = [
        (bbox_1[0], bbox_1[1]),
        (bbox_1[0], bbox_1[3]),
        (bbox_1[2], bbox_1[1]),
        (bbox_1[2], bbox_1[3])
        ]
    for p in points:
        if bbox_2[0] <= p[0] << bbox_2[2] and bbox_2[1] <= p[1] <= bbox_2[3]:
            return True

    return False


def layer_list(request, layer_purpose, layer_category=None, bbox=None):
    if request.method != 'GET':
        return HttpResponseBadRequest()

    if not layer_purpose:
        return HttpResponseBadRequest()

    try:
        layers_object, _ = retrieve_layers(layer_purpose, layer_category, bbox)
        layers = []
        for l in layers_object:
            layer_obj = dict()
            layer_obj['id'] = l.id
            layer_obj['name'] = l.name
            layers.append(layer_obj)

        return HttpResponse(
            json.dumps(layers), content_type="application/json")

    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def layer_panel(request, bbox=None):
    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        sections = AnalysisCreateView.options_panel_dict(bbox=bbox)
        form = AnalysisCreationForm(
            user=request.user,
            exposure_layer=retrieve_layers('exposure', bbox=bbox)[0],
            hazard_layer=retrieve_layers('hazard', bbox=bbox)[0],
            impact_functions=Analysis.impact_function_list())
        context = {
            'sections': sections,
            'form': form,
            'user': request.user,
        }
        return render(request, "geosafe/analysis/options_panel.html", context)

    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def rerun_analysis(request, analysis_id=None):
    if request.method != 'POST':
        return HttpResponseBadRequest()

    if not analysis_id:
        analysis_id = request.POST.get('analysis_id')

    if not analysis_id:
        return HttpResponseBadRequest()

    try:
        analysis = Analysis.objects.get(id=analysis_id)
        analysis_post_save(None, analysis, True)
        return HttpResponseRedirect(
            reverse('geosafe:analysis-detail', kwargs={'pk': analysis.pk})
        )
    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def analysis_json(request, analysis_id):
    """Return the status of an analysis

    :param request:
    :param analysis_id:
    :return:
    """
    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        analysis = Analysis.objects.get(id=analysis_id)
        retval = {
            'analysis_id': analysis_id,
            'impact_layer_id': analysis.impact_layer_id
        }
        return HttpResponse(
            json.dumps(retval), content_type="application/json")
    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def toggle_analysis_saved(request, analysis_id):
    """Toggle the state of keep of analysis

    :param request:
    :param analysis_id:
    :return:
    """
    if request.method != 'POST':
        return HttpResponseBadRequest()

    try:
        analysis = Analysis.objects.get(id=analysis_id)
        analysis.keep = not analysis.keep
        analysis.save()
        return HttpResponse(json.dumps({
            'success': True,
            'is_saved': analysis.keep,
        }), content_type='application/json')
        # return HttpResponseRedirect(reverse('geosafe:analysis-list'))
    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def serve_files(file_stream, content_type, filename):
    response = HttpResponse(
        file_stream,
        content_type=content_type)
    response['Content-Disposition'] = 'inline; filename="%s";' % filename
    return response


def download_report(request, analysis_id, data_type='map'):
    """Download the pdf files of the analysis

    available options for data_type:
    map: only map report
    table: only table report
    report: only map and table report
    all: map, table, and impact layer

    :param request:
    :param analysis_id:
    :param data_type: can be 'map' or 'table'
    """
    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        analysis = Analysis.objects.get(id=analysis_id)
        layer_title = analysis.impact_layer.title
        if data_type == 'map':
            return serve_files(
                analysis.report_map.read(),
                'application/pdf',
                '%s_map.pdf' % layer_title)
        elif data_type == 'table':
            return serve_files(
                analysis.report_table.read(),
                'application/pdf',
                '%s_table.pdf' % layer_title)
        elif data_type == 'reports':
            tmp = tempfile.mktemp()
            with ZipFile(tmp, mode='w') as zf:
                zf.writestr(
                    '%s_map.pdf' % layer_title,
                    analysis.report_map.read())
                zf.writestr(
                    '%s_table.pdf' % layer_title,
                    analysis.report_table.read())

            return serve_files(
                open(tmp),
                'application/zip',
                '%s_reports.zip' % layer_title)
        elif data_type == 'all':
            tmp = tempfile.mktemp()
            with ZipFile(tmp, mode='w') as zf:
                zf.writestr(
                    '%s_map.pdf' % layer_title,
                    analysis.report_map.read())
                zf.writestr(
                    '%s_table.pdf' % layer_title,
                    analysis.report_table.read())
                layer = analysis.impact_layer

                for layer_file in layer.upload_session.layerfile_set.all():
                    base_name = os.path.basename(layer_file.file.name)
                    zf.writestr(
                        base_name.replace(layer.name, layer.title),
                        layer_file.file.read())

            return serve_files(
                open(tmp),
                'application/zip',
                '%s_download.zip' % layer_title)

        return HttpResponseServerError()
    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()


def analysis_summary(request, impact_id):
    """Get analysis summary from a given impact id"""

    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        analysis = Analysis.objects.get(impact_layer__id=impact_id)
        report_type = None
        summary = ImpactSummary(analysis.impact_layer)
        if 'building' in summary.exposure_type():
            report_type = 'structure'
            summary = StructureSummary(analysis.impact_layer)
        elif 'population' in summary.exposure_type():
            report_type = 'population'
            summary = PopulationSummary(analysis.impact_layer)
        elif 'polygon people' in summary.exposure_type():
            report_type = 'polygon_people'
            summary = PolygonPeopleSummary(analysis.impact_layer)
        elif 'road' in summary.exposure_type():
            report_type = 'road'
            summary = RoadSummary(analysis.impact_layer)
        elif 'landcover' in summary.exposure_type():
            report_type = 'landcover'

        context = {
            'analysis': analysis,
            'report_type': report_type,
            'report_template': 'geosafe/analysis/summary/%s_report.html' % (
                report_type, ),
            'summary': summary
        }

        # provides download links
        analysis_layer = analysis.impact_layer
        has_download_permissions = request.user.has_perm(
            'download_resourcebase',
            analysis_layer.get_self_resource())
        if has_download_permissions:
            if analysis_layer.storeType == 'dataStore':
                download_format = settings.DOWNLOAD_FORMATS_VECTOR
            else:
                download_format = settings.DOWNLOAD_FORMATS_RASTER

            links = analysis_layer.link_set.download().filter(
                name__in=download_format)
            context['links'] = links

        return render(request, "geosafe/analysis/modal/impact_card.html",
                      context)
    except Exception as e:
        LOGGER.exception(e)
        return HttpResponseServerError()
