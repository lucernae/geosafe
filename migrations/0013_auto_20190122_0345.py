# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geosafe', '0012_auto_20190122_0342'),
    ]

    operations = [
        migrations.AlterField(
            model_name='analysistaskinfo',
            name='analysis',
            field=models.OneToOneField(related_name='task_info', to='geosafe.Analysis'),
        ),
    ]
