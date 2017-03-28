# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('layers', '24_to_26'),
    ]

    operations = [
        migrations.CreateModel(
            name='Analysis',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('user_title', models.CharField(help_text=b'Title to assign after analysis is generated.', max_length=255, null=True, verbose_name=b'User defined title for analysis', blank=True)),
                ('impact_function_id', models.CharField(help_text=b'The ID of Impact Function used in the analysis.', max_length=100, verbose_name=b'ID of Impact Function')),
                ('extent_option', models.IntegerField(default=2, help_text=b'Extent option for analysis.', verbose_name=b'Analysis extent', choices=[(1, b'Use intersection of hazard, exposure, and current view extent'), (2, b'Use intersection of hazard and exposure')])),
                ('task_id', models.CharField(help_text=b'Task UUID that runs analysis', max_length=40, null=True, verbose_name=b'Task UUID', blank=True)),
                ('task_state', models.CharField(help_text=b'Task State recorded in the model', max_length=10, null=True, verbose_name=b'Task State', blank=True)),
                ('keep', models.BooleanField(default=False, help_text=b'True if the impact will be kept', verbose_name=b'Keep impact result')),
                ('report_map', models.FileField(help_text=b'The map report of the analysis', upload_to=b'analysis/report/', null=True, verbose_name=b'Report Map', blank=True)),
                ('report_table', models.FileField(help_text=b'The table report of the analysis', upload_to=b'analysis/report/', null=True, verbose_name=b'Report Table', blank=True)),
            ],
            options={
                'verbose_name_plural': 'Analyses',
            },
        ),
        migrations.CreateModel(
            name='Metadata',
            fields=[
                ('layer', models.OneToOneField(related_name='metadata', primary_key=True, serialize=False, to='layers.Layer')),
                ('layer_purpose', models.CharField(default=b'', max_length=20, null=True, verbose_name=b'Purpose of the Layer', blank=True)),
                ('category', models.CharField(default=b'', max_length=30, null=True, verbose_name=b'The category of layer purpose that describes a kind ofhazard or exposure this layer is', blank=True)),
            ],
        ),
        migrations.AddField(
            model_name='analysis',
            name='aggregation_layer',
            field=models.ForeignKey(related_name='aggregation_layer', blank=True, to='layers.Layer', help_text=b'Aggregation layer for analysis.', null=True, verbose_name=b'Aggregation Layer'),
        ),
        migrations.AddField(
            model_name='analysis',
            name='exposure_layer',
            field=models.ForeignKey(related_name='exposure_layer', verbose_name=b'Exposure Layer', to='layers.Layer', help_text=b'Exposure layer for analysis.'),
        ),
        migrations.AddField(
            model_name='analysis',
            name='hazard_layer',
            field=models.ForeignKey(related_name='hazard_layer', verbose_name=b'Hazard Layer', to='layers.Layer', help_text=b'Hazard layer for analysis.'),
        ),
        migrations.AddField(
            model_name='analysis',
            name='impact_layer',
            field=models.ForeignKey(related_name='impact_layer', blank=True, to='layers.Layer', help_text=b'Impact layer from this analysis.', null=True, verbose_name=b'Impact Layer'),
        ),
        migrations.AddField(
            model_name='analysis',
            name='user',
            field=models.ForeignKey(blank=True, to=settings.AUTH_USER_MODEL, help_text=b'The author of the analysis', null=True, verbose_name=b'Author'),
        ),
    ]
