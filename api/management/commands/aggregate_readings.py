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
    python manage.py aggregate_readings --start "2026-10-01 00:00" --end "2026-10-03 00:00"
                                                        # belirli aralık (bucket sınırlarına genişletilir)

Cron örneği (her dakika 5dk bucket; her 5 dakikada 15dk; saat başı saatlik; gece 01:00 günlük):
    *   * * * *  python manage.py aggregate_readings --bucket=5m   --hours=2
    */5 * * * *  python manage.py aggregate_readings --bucket=15m  --hours=2
    5   * * * *  python manage.py aggregate_readings --bucket=hour --hours=6
    0   1 * * *  python manage.py aggregate_readings --bucket=day  --hours=48

İdempotenttir: aynı bucket için tekrar çalıştırılırsa upsert yapar
(önce siler, sonra yeniden yazar).
"""
from __future__ import annotations

import math
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


BUCKET_SIZES = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
}


def aggregate_rows(bucket: str, readings: list[dict]) -> tuple[int, int]:
    """Verilen raw satırlardan ``bucket`` aggregate'lerini yeniden yazar.

    Etkilenen (sensör, bucket_start) çiftlerinin eski kayıtları silinip
    yeniden oluşturulur (idempotent). **Satırlar her bucket'ı TAM kapsamalı** —
    kısmi veriyle çağrılırsa bucket eksik okumalardan hesaplanır; aralık
    tabanlı çağrı için :func:`recompute_range` kullanın.

    Dönen: (güncellenen bucket sayısı, sensör sayısı).
    """
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
        return 0, 0

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
                and math.isfinite(r["value"])  # eski NaN/±Inf kayıtları
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

    return len(grouped), len(sensor_ids)


def recompute_range(start: datetime, end: datetime, *, sensor_ids=None,
                    buckets: Iterable[str] = BUCKETS) -> dict[str, int]:
    """``[start, end)`` aralığına dokunan bucket'ları geçmişe dönük yeniden hesaplar.

    Geçmişe veri eklendiğinde (ör. manuel veri girişi) kullanılır. Aralık her
    bucket için sınırlara **genişletilir** (başlangıç aşağı, bitiş yukarı) — aksi
    halde günlük bucket yalnız eklenen dakikalardan hesaplanıp o günün geri
    kalanını silerdi. ``sensor_ids`` verilirse yalnız o sensörler işlenir.

    Dönen: bucket → güncellenen bucket sayısı.
    """
    # DB okumaları UTC döner ve periyodik aggregate bucket'ları UTC'de floor'lar;
    # sınırlar aynı saat diliminde hesaplanmalı (yerel gün ≠ UTC gün).
    start = start.astimezone(dt_timezone.utc)
    end = end.astimezone(dt_timezone.utc)
    result: dict[str, int] = {}
    for bucket in buckets:
        floor = BUCKET_FLOORS[bucket]
        b_start = floor(start)
        b_end = floor(end - timedelta(microseconds=1)) + BUCKET_SIZES[bucket]
        qs = Reading.objects.filter(
            time_iso__gte=b_start, time_iso__lt=b_end, time_iso__isnull=False,
        )
        if sensor_ids is not None:
            qs = qs.filter(sensor_id__in=list(sensor_ids))
        readings = list(qs.values("sensor_id", "value", "quality", "time_iso"))
        result[bucket], _ = aggregate_rows(bucket, readings)
    return result


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
        parser.add_argument(
            "--start", default=None,
            help="Aralık başlangıcı (YYYY-MM-DD HH:MM, yerel saat). --end ile birlikte "
                 "verilirse --hours yok sayılır ve aralık bucket sınırlarına genişletilir.",
        )
        parser.add_argument("--end", default=None, help="Aralık bitişi (YYYY-MM-DD HH:MM).")

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours <= 0:
            self.stderr.write(self.style.ERROR("--hours pozitif olmalı."))
            return

        bucket_choice = options["bucket"]
        targets: Iterable[str] = BUCKETS if bucket_choice == "all" else (bucket_choice,)

        if options.get("start") or options.get("end"):
            try:
                r_start = timezone.make_aware(
                    datetime.strptime(options["start"], "%Y-%m-%d %H:%M"))
                r_end = timezone.make_aware(
                    datetime.strptime(options["end"], "%Y-%m-%d %H:%M"))
            except (TypeError, ValueError):
                self.stderr.write(self.style.ERROR(
                    "--start ve --end birlikte, 'YYYY-MM-DD HH:MM' biçiminde verilmeli."))
                return
            if r_end <= r_start:
                self.stderr.write(self.style.ERROR("--end, --start'tan sonra olmalı."))
                return
            for bucket, n in recompute_range(r_start, r_end, buckets=targets).items():
                self.stdout.write(self.style.SUCCESS(f"  {bucket}: {n} bucket güncellendi."))
            return

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
        n_buckets, n_sensors = aggregate_rows(bucket, readings)
        if not n_buckets:
            self.stdout.write(f"  {bucket}: gruplanacak veri yok.")
            return
        self.stdout.write(self.style.SUCCESS(
            f"  {bucket}: {n_buckets} bucket güncellendi ({n_sensors} sensör)."
        ))
