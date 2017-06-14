# coding=utf-8
import os
from distutils.util import strtobool

from django.core.exceptions import ImproperlyConfigured


INASAFE_TESTING_ENVIRONMENT = strtobool(
    os.environ.get('INASAFE_TESTING_ENVIRONMENT', 'False'))

INASAFE_TESTING_ENVIRONMENT_NOT_CONFIGURED_MESSAGE = (
    'InaSAFE Testing Environment should be configured correctly for the '
    'remote tasks to work.'
)


class InaSAFETestData(object):

    @classmethod
    def path_finder(cls, *args):
        """Finding InaSAFE test data path on runtime.

        :return: path to safe package
        :rtype: str
        """
        # SAFE_PACKAGE were defined in Travis
        # Change this to your InaSAFE Safe module location
        # If you want to run tests.
        message = (
            'SAFE_PACKAGE were defined in Travis. '
            'Change this to your InaSAFE Safe module '
            'location If you want to run tests.')
        safe_package = os.environ.get(
            'SAFE_PACKAGE', '/usr/src/inasafe/safe')
        if not os.path.exists(safe_package):
            raise ImproperlyConfigured(message)
        return os.path.join(safe_package, 'test', 'data', *args)

    @classmethod
    def hazard(cls, *args):
        """Resolve path to hazard test data.

        :return: path to safe package
        :rtype: str
        """
        return cls.path_finder('hazard', *args)

    @classmethod
    def exposure(cls, *args):
        """Resolve path to exposure test data.

        :return: path to safe package
        :rtype: str
        """
        return cls.path_finder('exposure', *args)
