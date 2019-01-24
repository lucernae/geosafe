# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import datetime


class Migration(migrations.Migration):

    dependencies = [
        ('geosafe', '0011_metadata_keywords_json'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnalysisTaskInfo',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('start', models.DateTimeField(default=datetime.datetime.now)),
                ('end', models.DateTimeField(default=datetime.datetime.now)),
                ('finished', models.BooleanField()),
                ('result', models.TextField()),
                ('exception_class', models.CharField(max_length=255, null=True, blank=True)),
                ('traceback', models.TextField(null=True, blank=True)),
            ],
        ),
        migrations.AlterField(
            model_name='analysis',
            name='end_time',
            field=models.DateTimeField(default=datetime.datetime.now),
        ),
        migrations.AlterField(
            model_name='analysis',
            name='start_time',
            field=models.DateTimeField(default=datetime.datetime.now),
        ),
        migrations.AddField(
            model_name='analysistaskinfo',
            name='analysis',
            field=models.ForeignKey(related_name='task_info', to='geosafe.Analysis'),
        ),
    ]
