"""
Reading aggregation komutu — raw `Reading` satırlarından
`ReadingFiveMin`, `ReadingFifteenMin`, `ReadingHourly`, `ReadingDaily`
tablolarını doldurur.

Kullanım:
    python manage.py aggregate_readings                 # son 24 saat, tüm bucket'lar
    python manage.py aggregate_readings --hours=72      # son 72 saat
    python manage.py aggregate_readings --bucket=5m     # sadece 5 dakikalık
    python manage.py aggregate_readings --bucket=15m    # sadece 15 dakikalık
    python manage.py aggregate_readings --bucket=hour   # sadece saatlik
    python manage.py aggregate_readings --bucket=day    # sadece günlük

Cron örneği (her dakika 5dk bucket; her 5 dakikada 15dk; saat başı saatlik; gece 01:00 günlük):
    *   * * * *  python manage.py aggregate_readings --bucket=5m   --hours=2
    */5 * * * *  python manage.py aggregate_readings --bucket=15m  --hours=2
    5   * * * *  python manage.py aggregate_readings --bucket=hour --hours=6
    0   1 * * *  python manage.py aggregate_readings --bucket=day  --hours=48

İdempotenttir: aynı bucket için tekrar çalıştırılırsa upsert yapar
(önce siler, sonra yeniden yazar).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Iterable

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from api.models import (
    Reading,
    ReadingDaily,
    ReadingFifteenMin,
    ReadingFiveMin,
    ReadingHourly,
)


BUCKETS = ("5m", "15m", "hour", "day")

# avg/min/max hesabına GİRMEYEN kalite düzeyleri. Yıkama/bakım overlay'i
# quality="uncertain", geçersiz/alarm okumaları quality="bad" yazar
# (scada_io.persistence.persist_reading) — sensör suyla temas etmediği veya
# değer güvenilmez olduğu için bu satırlar bucket istatistiğini çarpıtır
# (ör. yıkama sırasında ortalamanın aniden yükselmesi). count/bad_count'ta
# yine sayılırlar; bucket'ta hiç geçerli okuma yoksa avg/min/max None kalır.
EXCLUDED_QUALITIES = frozenset({"bad", "uncertain"})


def floor_5m(dt: datetime) -> datetime:
    """En yakın 5 dakikalık bucket'a yuvarla (aşağı)."""
    minute = (dt.minute // 5) * 5
    return dt.replace(minute=minute, second=0, microsecond=0)


def floor_15m(dt: datetime) -> datetime:
    """En yakın 15 dakikalık bucket'a yuvarla (aşağı)."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def floor_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


BUCKET_FLOORS = {
    "5m": floor_5m,
    "15m": floor_15m,
    "hour": floor_hour,
    "day": floor_day,
}

BUCKET_MODELS = {
    "5m": ReadingFiveMin,
    "15m": ReadingFifteenMin,
    "hour": ReadingHourly,
    "day": ReadingDaily,
}


class Command(BaseCommand):
    help = "Raw Reading satırlarından 15dk/saat/gün aggregate tablolarını doldurur."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours", type=int, default=24,
            help="Geriye dönük kaç saatlik aralık işlensin (default: 24).",
        )
        parser.add_argument(
            "--bucket", choices=list(BUCKETS) + ["all"], default="all",
            help="Hangi bucket(s): 15m / hour / day / all (default: all).",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours <= 0:
            self.stderr.write(self.style.ERROR("--hours pozitif olmalı."))
            return

        bucket_choice = options["bucket"]
        targets: Iterable[str] = BUCKETS if bucket_choice == "all" else (bucket_choice,)

        end = timezone.now()
        start = end - timedelta(hours=hours)

        # Reading'leri tek seferde çek (tarih aralığı + sensör bazlı)
        readings = list(
            Reading.objects
            .filter(time_iso__gte=start, time_iso__lt=end, time_iso__isnull=False)
            .values("sensor_id", "value", "quality", "time_iso")
        )

        if not readings:
            self.stdout.write(self.style.WARNING(
                f"Aralıkta okuma yok ({start.isoformat()} → {end.isoformat()})."
            ))
            return

        for bucket in targets:
            self._aggregate_bucket(bucket, readings)

    def _aggregate_bucket(self, bucket: str, readings: list[dict]) -> None:
        floor = BUCKET_FLOORS[bucket]
        Model = BUCKET_MODELS[bucket]

        # Gruplama: (sensor_id, bucket_start) → liste
        grouped: dict[tuple[int, datetime], list[dict]] = defaultdict(list)
        for row in readings:
            ts = row["time_iso"]
            if ts is None:
                continue
            key = (row["sensor_id"], floor(ts))
            grouped[key].append(row)

        if not grouped:
            self.stdout.write(f"  {bucket}: gruplanacak veri yok.")
            return

        # Etkilenen bucket'lardaki eski kayıtları sil; idempotent upsert.
        sensor_ids = {sid for sid, _ in grouped.keys()}
        bucket_starts = {bs for _, bs in grouped.keys()}

        with transaction.atomic():
            Model.objects.filter(
                sensor_id__in=sensor_ids,
                bucket_start__in=bucket_starts,
            ).delete()

            new_rows = []
            for (sensor_id, bucket_start), rows in grouped.items():
                values = [
                    r["value"] for r in rows
                    if r["value"] is not None
                    and r.get("quality") not in EXCLUDED_QUALITIES
                ]
                bad_count = sum(1 for r in rows if r.get("quality") == "bad")
                if not values:
                    avg_v = min_v = max_v = None
                else:
                    avg_v = sum(values) / len(values)
                    min_v = min(values)
                    max_v = max(values)
                new_rows.append(Model(
                    sensor_id=sensor_id,
                    bucket_start=bucket_start,
                    avg_value=avg_v,
                    min_value=min_v,
                    max_value=max_v,
                    count=len(rows),
                    bad_count=bad_count,
                ))
            Model.objects.bulk_create(new_rows, batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f"  {bucket}: {len(grouped)} bucket güncellendi "
            f"({len(sensor_ids)} sensör)."
        ))
