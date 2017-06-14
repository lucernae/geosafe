# coding=utf-8
from django.test import TestCase

from geosafe.tasks.headless.analysis import RemoteTaskException, \
    filter_impact_function


class InaSAFERemoteTaskTest(TestCase):

    def test_remote_execution_exception(self):
        """Test to raise remote execution exception.

        Raise if remote function were executed in the wrong way.
        """

        with self.assertRaises(RemoteTaskException):
            filter_impact_function()
