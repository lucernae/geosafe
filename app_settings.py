# coding=utf-8

from django.conf import settings as django_settings
from geosafe import default_settings


class SettingsWrapper(object):

    def get(self, key, default=None):
        """Get settings from key.

        :param key: setting key
        :type key: str

        :param default: default value if key doesn't exists
        :type default: object

        :return: Return setting value
        :rtype: object
        """
        if hasattr(django_settings, key):
            return getattr(django_settings, key)
        elif default:
            return default
        elif hasattr(default_settings, key):
            return getattr(default_settings, key)
        else:
            return None

    def set(self, key, value):
        """Set setting key,value pair.

        :param key: setting key
        :type key: str

        :param value: value to set
        :type value: object
        """
        setattr(django_settings, key, value)

    def __getattr__(self, item):
        return self.get(item)

    def __setattr__(self, key, value):
        self.set(key, value)


settings = SettingsWrapper()
