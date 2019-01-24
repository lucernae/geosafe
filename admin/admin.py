# coding=utf-8
from django.contrib import admin

from geosafe.models import Metadata, Analysis, AnalysisTaskInfo


# Register your models here.
class MetadataAdmin(admin.ModelAdmin):
    list_display = (
        'layer',
        'layer_purpose',
        'category',
    )


class AnalysisAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'user_title',
        'exposure_layer',
        'hazard_layer',
        'aggregation_layer',
        'extent_option',
        'keep',
        'task_state',
        'report_map',
        'report_table'
    )


class AnalysisTaskInfoAdmin(admin.ModelAdmin):

    list_display = (
        'id'
        'analysis',
        'finished',
        'start',
        'end',
        'result',
        'traceback'
    )


admin.site.register(Metadata, MetadataAdmin)
admin.site.register(Analysis, AnalysisAdmin)
admin.site.register(AnalysisTaskInfo)
