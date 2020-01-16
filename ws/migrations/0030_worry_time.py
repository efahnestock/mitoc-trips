# -*- coding: utf-8 -*-
# Generated by Django 1.11.27 on 2020-01-16 03:14
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ws', '0029_tripinfo_last_updated'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tripinfo',
            name='worry_time',
            field=models.CharField(
                help_text='Suggested: 7 pm, or return time +2 hours (whichever is later). If the WIMP has not heard from you after this time and is unable to make contact with any leaders or participants, the authorities will be called.',
                max_length=63,
            ),
        ),
    ]
