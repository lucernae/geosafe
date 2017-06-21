# coding=utf-8

from __future__ import absolute_import

import logging
import os
import urlparse
from zipfile import ZipFile

from celery import chain
from django.core.urlresolvers import reverse
from django.db.models.query_utils import Q

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
            base_file_path = Analysis.get_base_layer_path(layer)
            base_file_path = os.path.basename(base_file_path)
            xml_file_path = base_file_path.split('.')[0] + '.xml'
            base_dir = settings.INASAFE_LAYER_DIRECTORY
            layer_url = os.path.join(base_dir, xml_file_path)
            layer_url = urlparse.urljoin('file://', layer_url)
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
    try:
        metadata = Metadata.objects.get(layer=layer)
    except Metadata.DoesNotExist:
        metadata = Metadata()
        metadata.layer = layer

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

    # Execute analysis in chains:
    # - Run analysis
    # - Process analysis result
    tasks_chain = chain(
        run_analysis.s(
            hazard,
            exposure,
            function,
            generate_report=True
        ).set(queue='inasafe-headless-analysis'),
        process_impact_result.s(
            analysis_id
        ).set(queue='geosafe')
    )
    result = tasks_chain.delay()
    return result


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
    analysis = Analysis.objects.get(id=analysis_id)

    # decide if we are using direct access or not
    impact_url = get_impact_path(impact_url)

    # download impact zip
    impact_path = download_file(impact_url)
    dir_name = os.path.dirname(impact_path)
    success = False
    with ZipFile(impact_path) as zf:
        zf.extractall(path=dir_name)
        for name in zf.namelist():
            basename, ext = os.path.splitext(name)
            if ext in ['.shp', '.tif']:
                # process this in the for loop to make sure it works only
                # when we found the layer
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
                analysis.save()

                if current_impact:
                    current_impact.delete()
                success = True
                break

        # cleanup
        for name in zf.namelist():
            filepath = os.path.join(dir_name, name)
            try:
                os.remove(filepath)
            except:
                pass

    # cleanup
    try:
        os.remove(impact_path)
    except:
        pass

    if not success:
        LOGGER.info('No impact layer found in %s' % impact_url)

    return success
