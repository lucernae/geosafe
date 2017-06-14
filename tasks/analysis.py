# coding=utf-8

from __future__ import absolute_import

import logging
import os
import tempfile
import time
import urlparse
from zipfile import ZipFile

import requests
import shutil

from celery import chain
from django.conf import settings
from django.core.files.base import File
from django.core.urlresolvers import reverse
from django.db.models.query_utils import Q

from geonode.layers.models import Layer
from geonode.layers.utils import file_upload
from geosafe.models import Analysis, Metadata
from geosafe.celery import app
from geosafe.tasks.headless.analysis import read_keywords_iso_metadata
from geosafe.tasks.headless.analysis import run_analysis

__author__ = 'lucernae'


LOGGER = logging.getLogger(__name__)


def download_file(url, user=None, password=None):
    parsed_uri = urlparse.urlparse(url)
    if parsed_uri.scheme == 'http' or parsed_uri.scheme == 'https':
        tmpfile = tempfile.mktemp()
        # NOTE the stream=True parameter
        # Assign User-Agent to emulate browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; U; Linux i686) '
                          'Gecko/20071127 Firefox/2.0.0.11'
        }
        if user:
            r = requests.get(
                url, headers=headers, stream=True, auth=(user, password))
        else:
            r = requests.get(url, headers=headers, stream=True)
        with open(tmpfile, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        return tmpfile
    elif parsed_uri.scheme == 'file' or not parsed_uri.scheme:
        return parsed_uri.path


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
        layer_url = reverse(
            'geosafe:layer-metadata',
            kwargs={'layer_id': layer.id})
        layer_url = urlparse.urljoin(settings.GEONODE_BASE_URL, layer_url)
        # Execute in chain:
        # Get InaSAFE keywords from InaSAFE worker
        # Set Layer metadata according to InaSAFE keywords
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


@app.task(
    name='geosafe.tasks.analysis.process_impact_result',
    queue='geosafe')
def process_impact_result(analysis_id):
    """Extract impact analysis after running it via InaSAFE-Headless celery

    :param analysis_id: analysis id of the object
    :type analysis_id: int

    :return: True if success
    :rtype: bool
    """
    analysis = Analysis.objects.get(id=analysis_id)

    hazard = analysis.get_layer_url(analysis.hazard_layer)
    exposure = analysis.get_layer_url(analysis.exposure_layer)
    function = analysis.impact_function_id
    try_count = 0
    while try_count < 5:
        time.sleep(5)
        try:
            impact_url = run_analysis.delay(
                hazard,
                exposure,
                function,
                generate_report=True).get()
            break
        except:
            try_count += 1

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
