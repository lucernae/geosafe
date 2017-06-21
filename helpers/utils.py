# coding=utf-8
import os
import shutil
import tempfile
import urllib
import urlparse

import re
import requests

from geosafe.app_settings import settings
from geosafe.models import Analysis


def download_file(url, direct_access=False, user=None, password=None):
    """Download file using http or file scheme.

    :param url: URL param, can be file url
    :type url: str

    :param user: User credentials for Http basic auth
    :type user: str

    :param password: Password credentials for Http basic auth
    :type password: str

    :param direct_access: directly pass the file uri if True,
        if not then copy to temporary file
    :type direct_access: bool

    :return: downloaded file location
    """
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

        # get extension
        content_disposition = r.headers['content-disposition']
        fname = re.findall("filename=[\'\"]?(.+)[\'\"]", content_disposition)
        _, ext = os.path.splitext(fname[0])
        shutil.move(tmpfile, '%s%s' % (tmpfile, ext))
        tmpfile = '%s%s' % (tmpfile, ext)
        return tmpfile
    elif parsed_uri.scheme == 'file':
        file_path = urllib.unquote_plus(parsed_uri.path).decode('utf-8')
    elif not parsed_uri.scheme:
        file_path = parsed_uri.path
    else:
        raise Exception(
            'URI scheme not recognized %s' % url)

    if direct_access:
        return file_path

    tmpfile = tempfile.mktemp()
    shutil.copy(file_path, tmpfile)
    return tmpfile


def get_layer_path(layer):
    """Helper function to get path for InaSAFE worker.

    :param layer: geonode layer
    :type layer: geonode.layers.models.Layer

    :p

    :return: Layer path or url
    :rtype: str
    """
    using_direct_access = (
        hasattr(settings, 'INASAFE_LAYER_DIRECTORY') and
        settings.INASAFE_LAYER_DIRECTORY)
    if using_direct_access and not layer.is_remote:
        base_layer_path = Analysis.get_base_layer_path(layer)
        basename = os.path.basename(base_layer_path)
        layer_url = os.path.join(
            settings.INASAFE_LAYER_DIRECTORY,
            basename)
        layer_url = urlparse.urljoin('file://', layer_url)
    else:
        layer_url = Analysis.get_layer_url(layer)
    return layer_url


def get_impact_path(impact_url):
    """Helper function to get path for Impact Result.

    :param impact_url: Impact layer url or uri
    :type impact_url: str


    :return: Layer path or url
    :rtype: str
    """
    using_direct_access = (
        hasattr(settings, 'GEOSAFE_IMPACT_OUTPUT_DIRECTORY') and
        settings.GEOSAFE_IMPACT_OUTPUT_DIRECTORY and
        hasattr(settings, 'INASAFE_IMPACT_BASE_URL') and
        settings.INASAFE_IMPACT_BASE_URL)

    if using_direct_access:
        parsed_uri = urlparse.urlparse(impact_url)
        if parsed_uri.scheme in ['http', 'https']:
            basename = urllib.unquote_plus(parsed_uri.path).decode('utf-8')
            # Calculate relative path to GEOSAFE_IMPACT_OUTPUT_DIRECTORY
            basename = os.path.relpath(
                basename, settings.INASAFE_IMPACT_BASE_URL)
            basedir = settings.GEOSAFE_IMPACT_OUTPUT_DIRECTORY
            return os.path.join(basedir, basename)
        elif parsed_uri.scheme == 'file' or not parsed_uri.scheme:
            return impact_url
        else:
            raise Exception(
                'URI scheme not recognized %s' % impact_url)

    return impact_url
