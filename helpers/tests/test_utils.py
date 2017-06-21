# coding=utf-8
import os
import urlparse

from django.core.management import call_command
from django.core.urlresolvers import reverse
from django.test import LiveServerTestCase

from geonode.layers.utils import file_upload
from geosafe.app_settings import settings
from geosafe.helpers.inasafe_helper import InaSAFETestData
from geosafe.helpers.utils import (
    download_file,
    get_layer_path,
    get_impact_path)
from geosafe.models import Analysis


class TestHelpersUtils(LiveServerTestCase):

    def setUp(self):
        call_command('loaddata', 'people_data', verbosity=0)

    def test_download_file_url(self):
        """Test downloading file directly using url."""

        data_helper = InaSAFETestData()
        filename = data_helper.hazard('flood_multipart_polygons.shp')
        hazard = file_upload(filename)

        # download zipped layer
        hazard_layer_url = Analysis.get_layer_url(hazard)

        downloaded_file = download_file(hazard_layer_url)

        # check that file is downloaded
        self.assertTrue(os.path.exists(downloaded_file))

        basename, ext = os.path.splitext(downloaded_file)

        # check that file has .zip extension
        self.assertEqual(ext, '.zip')

        # delete zipfile
        os.remove(downloaded_file)

        # download metadata xml file of geosafe
        hazard_layer_xml = reverse(
            'geosafe:layer-metadata',
            kwargs={'layer_id': hazard.id})
        hazard_layer_xml = urlparse.urljoin(
            settings.GEONODE_BASE_URL, hazard_layer_xml)

        downloaded_file = downloaded_file(hazard_layer_xml)

        # check that file is donwloaded
        self.assertTrue(os.path.exists(downloaded_file))

        # check that file has .xml extension
        self.assertEqual(ext, '.xml')

        # delete xmlfile
        os.remove(downloaded_file)

        # delete layer
        hazard.delete()

    def test_download_file_path(self):
        """Test that download_file returns correctly.

        Will test against several file:// scheme
        """

        cases = [
            {
                # Check regular file:// scheme case
                'input': 'file:///foo/bar.zip',
                'output': '/foo/bar.zip'
            },
            {
                # Check that relative file:// scheme case
                'input': 'file://foo/bar.zip',
                'output': '/bar.zip'
            },
            {
                # Check with + sign
                'input': 'file:///foo+bar/file+name+long.zip',
                'output': '/foo bar/file name long.zip'
            },
            {
                # Check urlencoded
                'input': 'file:///foo%20bar%2Flong%20file%2Cname.zip',
                'output': '/foo bar/long file,name.zip'
            },
            {
                # Check urlencoded and utf-8 encode
                'input': 'file:///movies%2F'
                         '%E5%90%9B%E3%81%AE%E5%90%8D%E3%81%AF.mkv',
                'output': u'/movies/君の名は.mkv'
            },
            {
                # Check regular path
                'input': '/foo/bar.xml',
                'output': '/foo/bar.xml'
            },
            {
                # Check do not decode regular path
                'input': '/foo/file+name.shp',
                'output': '/foo/file+name.shp'
            },
            {
                # Check do not decode regular path
                'input': '/foo/file%20name.shp',
                'output': '/foo/file%20name.shp'
            },
            {
                # Check do not decode regular path
                'input': u'/movies/君の名は.mkv',
                'output': u'/movies/君の名は.mkv'
            }
        ]

        for case in cases:
            message = 'The following case has failed: {case}'.format(
                case=case)
            self.assertEqual(
                case.get('output'),
                download_file(case.get('input'), direct_access=True),
                msg=message)

        # Check that download file will generate temporary file
        data_helper = InaSAFETestData()
        filename = data_helper.hazard('flood_multipart_polygons.shp')
        hazard = file_upload(filename)
        """:type: geonode.layers.models.Layer"""

        hazard_base_file, _ = hazard.get_base_file()
        hazard_path = hazard_base_file.file.path

        # Check file exists
        self.assertTrue(os.path.exists(hazard_path))

        downloaded_file = download_file(hazard_path, direct_access=False)

        # Should be a different filename
        self.assertNotEqual(hazard_path, downloaded_file)

        # Check file exists
        self.assertTrue(os.path.exists(downloaded_file))

        os.remove(downloaded_file)

        hazard.delete()

    def test_get_layer_path(self):
        """Test return file://schema if using direct file access."""
        data_helper = InaSAFETestData()
        filename = data_helper.hazard('flood_multipart_polygons.shp')
        hazard = file_upload(filename)
        """:type: geonode.layers.models.Layer"""

        layer_uri = get_layer_path(hazard)

        parsed_uri = urlparse.urlparse(layer_uri)

        # Use direct access by default
        self.assertEqual(parsed_uri.scheme, 'file')

        inasafe_layer_dir = settings.INASAFE_LAYER_DIRECTORY

        settings.set('INASAFE_LAYER_DIRECTORY', None)

        layer_uri = get_layer_path(hazard)

        parsed_uri = urlparse.urlparse(layer_uri)

        # Use http access when lack configuration
        self.assertEqual(parsed_uri.scheme, 'http')

        hazard.delete()

        settings.set('INASAFE_LAYER_DIRECTORY', inasafe_layer_dir)

    def test_get_impact_path(self):
        """Test return impact file path if using direct file access."""

        impact_url = 'http://inasafe-output/output/200/tmp1234.zip'

        converted_impact_path = get_impact_path(impact_url)

        # Use direct access by default
        self.assertEqual(
            converted_impact_path,
            '/home/geosafe/impact_layers/200/tmp1234.zip')

        geosafe_impact_output_dir = settings.GEOSAFE_IMPACT_OUTPUT_DIRECTORY

        settings.set('GEOSAFE_IMPACT_OUTPUT_DIRECTORY', None)

        converted_impact_path = get_impact_path(impact_url)

        # Doesn't change anything if not configured
        self.assertEqual(converted_impact_path, impact_url)
