"""
NaN / ±Infinity değerlerini temizle (tek seferlik onarım).

Cihaz float32 için 0xFFFF... döndürdüğünde eski sürümler değeri NaN olarak
kaydediyordu; JsonResponse bunu geçersiz `NaN` token'ı olarak yazdığı için
anasayfa analog tablosu / trend grafiği yüklenmiyordu. Yeni okumalar
`persist_reading` tarafından zaten süzülür — bu komut geçmişte kalanları
NULL'a çeker (Reading'de quality=bad + status=4 Geçersiz Veri).

Kullanım:
    python manage.py fix_nonfinite_values --dry-run
    python manage.py fix_nonfinite_values
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from api.models import (
    Reading,
    ReadingDaily,
    ReadingFifteenMin,
    ReadingFiveMin,
    ReadingHourly,
    SensorLatest,
    StatusCode,
)

NONFINITE = [float("nan"), float("inf"), float("-inf")]


class Command(BaseCommand):
    help = "NaN/±Infinity sensör değerlerini NULL'a çeker (Reading, SensorLatest, aggregate'ler)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Yalnız say, değiştirme.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        invalid = StatusCode.objects.filter(code=4).first()

        jobs = [
            ("Reading", Reading.objects.filter(value__in=NONFINITE),
             {"value": None, "quality": "bad", "status": invalid}),
            ("SensorLatest.value", SensorLatest.objects.filter(value__in=NONFINITE),
             {"value": None, "quality": "bad", "status": invalid}),
            ("SensorLatest.last_saved_value",
             SensorLatest.objects.filter(last_saved_value__in=NONFINITE),
             {"last_saved_value": None}),
        ]
        for model in (ReadingFiveMin, ReadingFifteenMin, ReadingHourly, ReadingDaily):
            for field in ("avg_value", "min_value", "max_value"):
                jobs.append((
                    f"{model.__name__}.{field}",
                    model.objects.filter(**{f"{field}__in": NONFINITE}),
                    {field: None},
                ))

        total = 0
        with transaction.atomic():
            for label, qs, updates in jobs:
                n = qs.count() if dry else qs.update(**updates)
                total += n
                if n:
                    self.stdout.write(f"  {label}: {n}")

        verb = "bulundu (dry-run, değişiklik yok)" if dry else "temizlendi"
        self.stdout.write(self.style.SUCCESS(f"Toplam {total} NaN/Inf değer {verb}."))
