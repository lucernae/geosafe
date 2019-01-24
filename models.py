from __future__ import absolute_import

import json
import os
import urlparse
from datetime import datetime

from celery.result import AsyncResult
from django.contrib.gis.geos import GEOSGeometry
from django.core.files.base import File
from django.core.urlresolvers import reverse
from django.db import models
from django.utils.translation import ugettext as _
from geonode.utils import bbox_to_wkt

from geonode.layers.models import Layer
from geosafe.app_settings import settings

# flat xpath for the keyword container tag
from geosafe.helpers.suggestions.error_suggestions import AnalysisError

ISO_METADATA_INASAFE_KEYWORD_TAG = (
    '//gmd:supplementalInformation/inasafe')
ISO_METADATA_INASAFE_PROVENANCE_KEYWORD_TAG = (
    '//gmd:supplementalInformation/inasafe_provenance')

ISO_METADATA_NAMESPACES = {
    'gmd': 'http://www.isotc211.org/2005/gmd',
    'gco': 'http://www.isotc211.org/2005/gco'
}


class GeoSAFEException(BaseException):
    pass


class MetadataManager(models.Manager):

    def get_queryset(self):
        """Defer text fields"""
        return super(MetadataManager, self).get_queryset().defer(
            'keywords_xml', 'keywords_json')


# Create your models here.
class Metadata(models.Model):
    """Represent metadata for a layer."""
    layer = models.OneToOneField(
        Layer, primary_key=True, related_name='inasafe_metadata')
    layer_purpose = models.CharField(
        verbose_name='Purpose of the Layer',
        max_length=20,
        blank=True,
        null=True,
        default=''
    )
    category = models.CharField(
        verbose_name='The category of layer purpose that describes a kind of'
                     'hazard or exposure this layer is',
        max_length=30,
        blank=True,
        null=True,
        default=''
    )
    keywords_xml = models.TextField(
        verbose_name='Full representation of InaSAFE keywords in xml format',
        blank=True,
        null=True,
        default=''
    )
    keywords_json = models.TextField(
        verbose_name='Full representation of InaSAFE keywords in json format',
        blank=True,
        null=True,
        default='{}'
    )

    objects = MetadataManager()

    @property
    def keywords(self):
        """Return InaSAFE keywords dict."""
        try:
            return json.loads(self.keywords_json)
        except ValueError:
            return {}

    def reset_metadata(self):
        """Reset to empty metadata."""
        Metadata.objects.filter(pk=self.pk).update(
            keywords_xml='',
            keywords_json='',
            layer_purpose='',
            category='')


class Analysis(models.Model):
    """Represent GeoSAFE analysis"""
    HAZARD_EXPOSURE_CURRENT_VIEW_CODE = 1
    HAZARD_EXPOSURE_CODE = 2
    HAZARD_EXPOSURE_BBOX_CODE = 3

    HAZARD_EXPOSURE_CURRENT_VIEW_TEXT = (
        'Use intersection of hazard, exposure, and current view extent')
    HAZARD_EXPOSURE_TEXT = 'Use intersection of hazard and exposure'
    HAZARD_EXPOSURE_BBOX_TEXT = (
        'Use intersection of hazard, exposure, and bounding box')

    EXTENT_CHOICES = (
        (HAZARD_EXPOSURE_CURRENT_VIEW_CODE, HAZARD_EXPOSURE_CURRENT_VIEW_TEXT),
        (HAZARD_EXPOSURE_CODE, HAZARD_EXPOSURE_TEXT),
        # Disable for now
        # (HAZARD_EXPOSURE_BBOX_CODE, HAZARD_EXPOSURE_BBOX_TEXT),
    )

    class Meta:
        verbose_name_plural = 'Analyses'

    user_title = models.CharField(
        max_length=255,
        verbose_name='User defined title for analysis',
        help_text='Title to assign after analysis is generated.',
        blank=True,
        null=True,
    )
    user_extent = models.CharField(
        max_length=255,
        verbose_name='Analysis extent',
        help_text='User defined BBOX for analysis extent',
        blank=True,
        null=True,
    )
    exposure_layer = models.ForeignKey(
        Layer,
        verbose_name='Exposure Layer',
        help_text='Exposure layer for analysis.',
        blank=False,
        null=True,
        related_name='exposure_layer',
        on_delete=models.SET_NULL
    )
    hazard_layer = models.ForeignKey(
        Layer,
        verbose_name='Hazard Layer',
        help_text='Hazard layer for analysis.',
        blank=False,
        null=True,
        related_name='hazard_layer',
        on_delete=models.SET_NULL
    )
    aggregation_layer = models.ForeignKey(
        Layer,
        verbose_name='Aggregation Layer',
        help_text='Aggregation layer for analysis.',
        blank=True,
        null=True,
        related_name='aggregation_layer',
        on_delete=models.SET_NULL
    )
    aggregation_filter = models.TextField(
        verbose_name='Serialized JSON of selected aggregation area name',
        help_text='List of aggregation area being used in aggregation layer',
        blank=True,
        null=True,
    )
    filtered_aggregation = models.CharField(
        max_length=255,
        verbose_name='Temporary file location of filtered aggregation',
        blank=True,
        null=True
    )
    impact_function_id = models.CharField(
        max_length=100,
        verbose_name='ID of Impact Function',
        help_text='The ID of Impact Function used in the analysis.',
        blank=True,
        null=True
    )
    extent_option = models.IntegerField(
        choices=EXTENT_CHOICES,
        default=HAZARD_EXPOSURE_CODE,
        verbose_name='Analysis extent',
        help_text='Extent option for analysis.'
    )

    impact_layer = models.ForeignKey(
        Layer,
        verbose_name='Impact Layer',
        help_text='Impact layer from this analysis.',
        blank=True,
        null=True,
        related_name='impact_layer'
    )

    task_id = models.CharField(
        max_length=40,
        verbose_name='Task UUID',
        help_text='Task UUID that runs analysis',
        blank=True,
        null=True
    )

    task_state = models.CharField(
        max_length=10,
        verbose_name='Task State',
        help_text='Task State recorded in the model',
        blank=True,
        null=True
    )

    keep = models.BooleanField(
        verbose_name='Keep impact result',
        help_text='True if the impact will be kept',
        blank=True,
        default=False,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='Author',
        help_text='The author of the analysis',
        blank=True,
        null=True
    )

    report_map = models.FileField(
        verbose_name='Report Map',
        help_text='The map report of the analysis',
        blank=True,
        null=True,
        upload_to='analysis/report/'
    )

    report_table = models.FileField(
        verbose_name='Report Table',
        help_text='The table report of the analysis',
        blank=True,
        null=True,
        upload_to='analysis/report/'
    )

    start_time = models.DateTimeField(
        default=datetime.now
    )

    end_time = models.DateTimeField(
        default=datetime.now
    )

    language_code = models.CharField(
        max_length=10,
        verbose_name='Language Code',
        help_text='Language being used by the django app',
        default='en'
    )

    def assign_report_map(self, filename):
        try:
            self.report_map.delete()
        except BaseException:
            pass
        self.report_map = File(open(filename))

    def assign_report_table(self, filename):
        try:
            self.report_table.delete()
        except BaseException:
            pass
        self.report_table = File(open(filename))

    def get_task_result(self):
        """
        :return: celery AsyncResult
        :rtype: celery.result.AsyncResult
        """
        return AsyncResult(self.task_id)

    def user_extent_bbox(self):
        """Return user extent as BBOX array.

        Format: [[left,top], [right, bottom]]
        """
        bbox_arr = self.user_extent.split(',')
        bbox_float = [float(i) for i in bbox_arr]
        return [[bbox_float[0], bbox_float[1]],
                [bbox_float[2], bbox_float[3]]]

    def user_extent_area(self):
        """Return user extent area in meter square."""
        bbox_float = self.user_extent_bbox()
        extent_geom = GEOSGeometry(
            bbox_to_wkt(
                bbox_float[0][0], bbox_float[1][0],
                bbox_float[0][1], bbox_float[1][1]), srid=4326)
        # Check the size of the extent
        # convert to EPSG:3410 for equal area projection
        extent_geom.transform('3410')
        # Return in km^2
        return extent_geom.area / 1000000

    def get_label_class(self):
        state = self.get_task_state()
        if state == 'SUCCESS':
            return 'success'
        elif state == 'FAILURE':
            return 'danger'
        else:
            return 'info'

    def get_task_state(self):
        """Check task state

        State need to be evaluated from task result.
        However after a certain time, the task result is removed from broker.
        In this case, the state will always return 'PENDING'. For this, we
        receive the actual result from self.state, which is the cached state

        :return: task state string
        :rtype: str
        """
        result = self.get_task_result()
        if not result.task_id:
            # If no task_id, we don't have any task yet.
            return 'FAILURE'
        # chain state, iterate result
        if result.children:
            for child in result.children:
                try:
                    if child.state == 'PENDING':
                        return self.task_state
                    else:
                        return child.state
                except BaseException:
                    return 'FAILURE'
        # If state result is not PENDING, this could be the current state
        if result.state != 'PENDING':
            return result.state

        # If state result is PENDING, it is possible the result is gone.
        # So, retrieve the state from cache
        return self.task_state

    def get_default_impact_title(self):
        title_dict = {
            'hazard': self.hazard_layer.title,
            'exposure': self.exposure_layer.title
        }
        if self.aggregation_layer:
            title_template = _('{hazard} on {exposure} around {aggregation}')
            title_dict['aggregation'] = self.aggregation_layer.title
        else:
            title_template = _('{hazard} on {exposure}')

        return title_template.format(**title_dict)

    def aggregation_field_name(self):
        # Get aggregation field name from InaSAFE keywords
        if self.aggregation_layer:
            keywords = self.aggregation_layer.inasafe_metadata.keywords
            try:
                return keywords['inasafe_fields']['aggregation_name_field']
            except KeyError:
                return ''
        return ''

    def impact_function_name(self):
        # Set impact function name from provenance data
        if self.impact_layer:
            keywords = self.impact_layer.inasafe_metadata.keywords
            try:
                return keywords['provenance_data']['impact_function_name']
            except KeyError:
                self.get_default_impact_title()
        return self.get_default_impact_title()

    @property
    def custom_template_exists(self):
        """Use to check if particular language code for this analysis have
        custom template.
        """
        locale_template_map = settings.LOCALIZED_QGIS_REPORT_TEMPLATE
        has_dict_mapping = self.language_code in locale_template_map
        if has_dict_mapping:
            custom_template = locale_template_map.get(self.language_code)
            return os.path.exists(custom_template)
        return False

    @property
    def custom_template(self):
        """Return custom report template path if exists and defined."""
        if not self.custom_template_exists:
            return None
        locale_template_map = settings.LOCALIZED_QGIS_REPORT_TEMPLATE
        return locale_template_map.get(self.language_code)

    @classmethod
    def get_layer_url(cls, layer):
        layer_id = layer.id
        layer_url = reverse(
            'geosafe:layer-archive',
            kwargs={'layer_id': layer_id})
        layer_url = urlparse.urljoin(settings.GEONODE_BASE_URL, layer_url)
        return layer_url

    @classmethod
    def get_base_layer_path(cls, layer):
        """Helper function to get path of the layer.

        :param layer:
        :type layer: geonode.layers.models.Layer

        :return: Layer path or url
        :rtype: str
        """
        return layer.qgis_layer.base_layer_path

    def save(self, force_insert=False, force_update=False, using=None,
             update_fields=None, run_analysis_flag=True):
        super(Analysis, self).save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields)

    def __unicode__(self):
        return 'Analysis ID: {}'.format(self.id)


class AnalysisTaskInfo(models.Model):
    """Represents Analysis Task information."""

    analysis = models.OneToOneField(
        Analysis,
        related_name='task_info')

    start = models.DateTimeField(
        default=datetime.now
    )

    end = models.DateTimeField(
        default=datetime.now
    )

    finished = models.BooleanField()

    result = models.TextField()

    exception_class = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    traceback = models.TextField(
        blank=True,
        null=True
    )

    def duration(self):
        if self.finished:
            return self.end - self.start
        return datetime.now() - self.start

    @classmethod
    def iterate_task_children(cls, result):
        """Perform DFS iteration of task children to search current task."""
        if not result:
            yield None

        # yield the first task (root)
        yield result

        # yield children if any
        children = result.children
        if not children:
            yield None

        for c in children:
            # since the child is also a task result, we iterate it again
            # by DFS
            it = cls.iterate_task_children(c)
            while True:
                cur_result = next(it)
                if not cur_result:
                    # if no next task result, break out of while loop
                    break
                # yield current task result
                yield cur_result

    def task_result_missing(self):
        result = self.analysis.get_task_result()
        return result.info is None and result.result is None

    def update_info(self):
        if self.task_result_missing():
            # We don't have anything to update if task result is missing
            return
        # track current result if not pending
        result = self.analysis.get_task_result()
        it = self.iterate_task_children(result)
        while True:
            result = next(it)
            if result:
                self.finished = True
                self.result = str(result.result)

                if result.state == 'FAILURE':
                    # On failure the result is the Exception class
                    module_name = result.result.__class__.__module__
                    class_name = result.result.__class__.__name__
                    self.exception_class = '.'.join([module_name, class_name])
                    self.traceback = str(result.traceback)

                self.start = self.analysis.start_time
                self.end = self.analysis.end_time or datetime.now()

                self.save()

                if not result.state == 'SUCCESS':
                    # On the first known non-pending and non-success task
                    # break out of loop because we only need to report the
                    # first one we find.
                    break

    @classmethod
    def create_from_analysis(cls, analysis):
        defaults = {
            'start': analysis.start_time,
            'end': analysis.end_time,
            'finished': not analysis.task_state == 'PENDING',
        }
        task_info, created = AnalysisTaskInfo.objects.get_or_create(
            analysis=analysis,
            defaults=defaults)
        return task_info

    def troubleshoot(self):
        """Attempt to get troubleshoot suggestions."""
        return AnalysisError.attempt_troubleshoot_message(self)

    def __unicode__(self):
        return 'Analysis: {0}'.format(self.analysis.id)


# needed to load signals
# noinspection PyUnresolvedReferences
from geosafe.signals import *  # noqa
