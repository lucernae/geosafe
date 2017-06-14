# coding=utf-8
import unittest
import time

import logging
from django.core.management import call_command
from django.test.testcases import LiveServerTestCase

from geonode.layers.utils import file_upload
from geosafe.views.analysis import retrieve_layers
from geosafe.helpers.inasafe_helper import (
    InaSAFETestData,
    INASAFE_TESTING_ENVIRONMENT,
    INASAFE_TESTING_ENVIRONMENT_NOT_CONFIGURED_MESSAGE)
from geosafe.models import Metadata

LOGGER = logging.getLogger(__file__)


class ViewsTest(LiveServerTestCase):

    def setUp(self):
        call_command('loaddata', 'people_data', verbosity=0)

    @unittest.skipUnless(
        INASAFE_TESTING_ENVIRONMENT,
        INASAFE_TESTING_ENVIRONMENT_NOT_CONFIGURED_MESSAGE)
    def test_retrieve_layers(self):
        """Test that retrieve layers functionality works."""
        data_helper = InaSAFETestData()
        filename = data_helper.hazard('flood_multipart_polygons.shp')
        hazard = file_upload(filename)

        metadata_created = False
        while not metadata_created:
            try:
                Metadata.objects.get(layer=hazard)
                metadata_created = True
            except Metadata.DoesNotExist:
                LOGGER.info('Metadata not created, waiting...')
                time.sleep(1)

        retrieved_layer, is_filtered = retrieve_layers('hazard')

        self.assertEqual(len(retrieved_layer), 1)
        self.assertEqual(hazard.id, retrieved_layer[0].id)
        self.assertFalse(is_filtered)

        hazard.delete()

