# coding=utf-8

from __future__ import absolute_import

import ast
import logging
import os
import urlparse
from datetime import datetime
from zipfile import ZipFile

from celery import chain
from django.core.urlresolvers import reverse
from django.db.models.query_utils import Q
from lxml import etree
from lxml.etree import XML, Element

from geonode.base.models import ResourceBase
from geonode.layers.models import Layer
from geonode.layers.utils import file_upload
from geosafe.app_settings import settings
from geosafe.celery import app
from geosafe.helpers.utils import (
    download_file,
    get_layer_path,
    get_impact_path)
from geosafe.models import Analysis, Metadata
from geosafe.tasks.headless.analysis import (
    read_keywords_iso_metadata,
    run_analysis)

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
        supplemental_info = root.xpath(
            '//gmd:supplementalInformation',
            namespaces=namespaces)[0]

        # Check that it contains InaSAFE metadata
        inasafe_el = supplemental_info.find('inasafe')
        inasafe_provenance_el = supplemental_info.find('inasafe_provenance')

        # Take InaSAFE metadata
        if inasafe_el is None:
            # Do nothing if InaSAFE tag didn't exists
            return

        # Take root xml from layer metadata_xml field
        layer_root_xml = XML(instance.metadata_xml)
        layer_sup_info = layer_root_xml.xpath(
            '//gmd:supplementalInformation',
            namespaces=namespaces)[0]

        char_string_tagname = '{%s}CharacterString' % namespaces['gco']

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
        if using_direct_access and not layer.is_remote:
            # If direct disk access were configured, then use it.
            base_file_path = get_layer_path(layer)
            base_file_path, _ = os.path.splitext(base_file_path)
            xml_file_path = base_file_path + '.xml'
            layer_url = urlparse.urljoin('file://', xml_file_path)
        else:
            # InaSAFE Headless celery will download metadata from url
            layer_url = reverse(
                'geosafe:layer-metadata',
                kwargs={'layer_id': layer.id})
            layer_url = urlparse.urljoin(settings.GEONODE_BASE_URL, layer_url)
        # Execute in chain:
        # - Get InaSAFE keywords from InaSAFE worker
        # - Set Layer metadata according to InaSAFE keywords
        read_keywords_iso_metadata_queue = read_keywords_iso_metadata.queue
        set_layer_purpose_queue = set_layer_purpose.queue
        tasks_chain = chain(
            read_keywords_iso_metadata.s(
                layer_url, ('layer_purpose', 'hazard', 'exposure')).set(
                queue=read_keywords_iso_metadata_queue),
            set_layer_purpose.s(layer_id).set(
                queue=set_layer_purpose_queue)
        )
        tasks_chain.delay()
    except Layer.DoesNotExist as e:
        # Perhaps layer wasn't saved yet.
        # Retry later
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
    layer = Layer.objects.get(id=layer_id)
    metadata, created = Metadata.objects.get_or_create(layer=layer)

    metadata.layer_purpose = keywords.get('layer_purpose', None)
    metadata.category = keywords.get(metadata.layer_purpose, None)
    metadata.save()

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


def prepare_analysis(analysis_id):
    """Prepare and run analysis

    :param analysis_id: analysis id of the object
    :type analysis_id: int

    :return: Celery Async Result
    :rtype: celery.result.AsyncResult
    """
    analysis = Analysis.objects.get(id=analysis_id)

    hazard = get_layer_path(analysis.hazard_layer)
    exposure = get_layer_path(analysis.exposure_layer)
    function = analysis.impact_function_id

    extent = analysis.user_extent

    if extent:
        # Reformat extent into list(float)
        extent = ast.literal_eval('[' + extent + ']')

    # Execute analysis in chains:
    # - Run analysis
    # - Process analysis result
    tasks_chain = chain(
        run_analysis.s(
            hazard,
            exposure,
            function,
            generate_report=True,
            requested_extent=extent,
            archive_impact=False
        ).set(
            queue='inasafe-headless-analysis').set(
            time_limit=settings.INASAFE_ANALYSIS_RUN_TIME_LIMIT),
        process_impact_result.s(
            analysis_id
        ).set(queue='geosafe')
    )
    result = tasks_chain.delay()
    # Parent information will be lost later.
    # What we should save is the run_analysis task result as this is the
    # chain's parent
    return result.parent


@app.task(
    name='geosafe.tasks.analysis.process_impact_result',
    queue='geosafe',
    bind=True)
def process_impact_result(self, impact_url, analysis_id):
    """Extract impact analysis after running it via InaSAFE-Headless celery

    :param self: Task instance
    :type self: celery.task.Task

    :param impact_url: impact url returned from analysis
    :type impact_url: str

    :param analysis_id: analysis id of the object
    :type analysis_id: int

    :return: True if success
    :rtype: bool
    """
    # Track the current task_id
    analysis = Analysis.objects.get(id=analysis_id)

    analysis.task_id = self.request.id
    analysis.save()

    # decide if we are using direct access or not
    impact_url = get_impact_path(impact_url)

    # download impact layer path
    impact_path = download_file(impact_url, direct_access=True)
    dir_name = os.path.dirname(impact_path)
    success = False
    is_zipfile = os.path.splitext(impact_path)[1].lower() == '.zip'
    if is_zipfile:
        # Extract the layer first
        with ZipFile(impact_path) as zf:
            zf.extractall(path=dir_name)
            for name in zf.namelist():
                basename, ext = os.path.splitext(name)
                if ext in ['.shp', '.tif']:
                    # process this in the for loop to make sure it works only
                    # when we found the layer
                    success = process_impact_layer(
                        analysis, basename, dir_name, name)
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
        filename = os.path.basename(impact_path)
        basename, ext = os.path.splitext(filename)
        success = process_impact_layer(analysis, basename, dir_name, filename)

        # cleanup
        for name in os.listdir(dir_name):
            filepath = os.path.join(dir_name, name)
            is_file = os.path.isfile(filepath)
            should_delete = name.split('.')[0] == basename
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
        LOGGER.info('No impact layer found in %s' % impact_url)

    return success


def process_impact_layer(analysis, basename, dir_name, name):
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
    saved_layer = file_upload(
        os.path.join(dir_name, name),
        overwrite=True)
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
    # check map report and table
    report_map_path = os.path.join(
        dir_name, '%s.pdf' % basename
    )
    if os.path.exists(report_map_path):
        analysis.assign_report_map(report_map_path)
    report_table_path = os.path.join(
        dir_name, '%s_table.pdf' % basename
    )
    if os.path.exists(report_table_path):
        analysis.assign_report_table(report_table_path)
    analysis.task_id = process_impact_result.request.id
    analysis.task_state = 'SUCCESS'
    analysis.end_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    analysis.save()
    if current_impact:
        current_impact.delete()
    success = True
    return success
