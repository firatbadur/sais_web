"""Mevcut sensörlere otomatik SCADA etiketi (tag) ata.

Sensor.save() yeni kayıtlarda tag üretir; bu migration eski kayıtları doldurur.
Tag, parametre kodundan (parameter_name → parameter_txt) türetilir; çakışmada
pk eklenir.
"""
from __future__ import annotations

import re

from django.db import migrations


def _slug(raw: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", raw or "").strip("_")


def backfill(apps, schema_editor):
    Sensor = apps.get_model("api", "Sensor")
    used = set(
        Sensor.objects.exclude(tag="").exclude(tag__isnull=True).values_list("tag", flat=True)
    )
    for s in Sensor.objects.filter(tag="").select_related("parameter").iterator():
        raw = ""
        if s.parameter_id and s.parameter:
            raw = s.parameter.parameter_name or s.parameter.parameter_txt or ""
        base = _slug(raw) or f"TAG{s.pk}"
        tag = base if base not in used else f"{base}_{s.pk}"
        s.tag = tag
        used.add(tag)
        s.save(update_fields=["tag"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0025_sensor_tag"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
