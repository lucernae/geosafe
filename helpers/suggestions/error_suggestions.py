# coding=utf-8

from django.utils.translation import ugettext_lazy as _


class Troubleshoot(object):
    """Base class of troubleshoot.

    This class is reserved as barebones for suggestion generations.
    """

    title = None
    message = None
    template = 'geosafe/analysis/troubleshoot/generic.html'
    suggested_actions = []


class WorkerLostTroubleshoot(Troubleshoot):
    """Specific suggestion class for WorkerLostError."""

    title = _('Worker Lost Error Suggestion')
    message = _(
        'The task has been forcefully shut down.'
        'This can happen for various reasons.'
        'It may take a very long time for the task to complete and stale in '
        'the background without recovering.'
        'It is also possible that the task requires significant resources '
        'to complete.'
        'Consider to reduce the analysis extent, if this happens '
        'to be the case.'
        'If you are using Raster Layer, consider the total extent and '
        'resolution is not overly complex.'
        'You can also divide the Raster layer to smaller extent so it can '
        'be easily processed, or use aggregation layer.'
    )
    suggested_actions = [
        _('Reduce analysis extent'),
        _('Use aggregation layer'),
        _('Use less resolution or smaller Raster layer '
          '(if it involves raster layer)'),
        _('Create new smaller layer to be used specifically for the analysis')
    ]


class AnalysisError(object):
    """Reserved singleton class to handle Analysis Troubleshooting suggestion.
    """

    @classmethod
    def attempt_troubleshoot_message(cls, task_info):
        """Big blob of function to predict suggestion for a certain errors.

        :param task_info: A task info model
        :type task_info: geosafe.models.AnalysisTaskInfo

        :return: Troubleshoot model
        :rtype: Troubleshoot
        """
        exception_class = task_info.exception_class

        if not exception_class:
            return None

        if 'WorkerLostError' in exception_class:
            return WorkerLostTroubleshoot()
