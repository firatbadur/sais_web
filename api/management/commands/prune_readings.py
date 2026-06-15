"""
Reading ve aggregate tablolarından retention politikasına göre eski kayıtları siler.

Retention politikası (settings'ten okunur; .env ile override edilebilir):

| Tablo              | Default gün | Env                               |
|--------------------|-------------|-----------------------------------|
| Reading (raw)      | 90          | READING_RETENTION_RAW_DAYS        |
| ReadingFifteenMin  | 365         | READING_RETENTION_15M_DAYS        |
| ReadingHourly      | 1825 (5yr)  | READING_RETENTION_HOURLY_DAYS     |
| ReadingDaily       | 99999       | READING_RETENTION_DAILY_DAYS      |

Raw `Reading` hızlı büyür (1000 tag × save_interval=60sn → 1.4M satır/gün).
15dk/saat aggregate tabloları çok daha küçük (per-sensor per-bucket 1 satır).
Günlük aggregate pratikte sonsuza kadar tutulabilir — yıllık 365 satır/sensör.

Kullanım:
    python manage.py prune_readings                  # tüm seviyeler, settings default
    python manage.py prune_readings --level=raw      # sadece Reading
    python manage.py prune_readings --level=15m
    python manage.py prune_readings --level=hour
    python manage.py prune_readings --level=day
    python manage.py prune_readings --days=30        # tek-seviye modunda retention override
    python manage.py prune_readings --dry-run        # silmeden sayım

Büyük silmelerde tek-transaction şişmesini / uzun kilit süresini önlemek için
batch delete yapar (default 10000).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Type

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone

from api.models import Reading, ReadingDaily, ReadingFifteenMin, ReadingHourly


@dataclass
class RetentionTarget:
    level: str                    # CLI argümanı için isim
    model: Type[models.Model]
    timestamp_field: str          # filter için kullanılacak alan
    default_days: int
    settings_key: str             # settings'ten okunan env anahtarı


TARGETS: list[RetentionTarget] = [
    RetentionTarget("raw", Reading, "time_iso", 90, "READING_RETENTION_RAW_DAYS"),
    RetentionTarget("15m", ReadingFifteenMin, "bucket_start", 365, "READING_RETENTION_15M_DAYS"),
    RetentionTarget("hour", ReadingHourly, "bucket_start", 1825, "READING_RETENTION_HOURLY_DAYS"),
    RetentionTarget("day", ReadingDaily, "bucket_start", 99999, "READING_RETENTION_DAILY_DAYS"),
]

LEVEL_CHOICES = [t.level for t in TARGETS] + ["all"]
BATCH_SIZE = 10000


class Command(BaseCommand):
    help = "Reading ve aggregate tablolarından retention politikasına göre eski kayıtları siler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--level", choices=LEVEL_CHOICES, default="all",
            help="Hangi tabloyu temizleyecek: raw / 15m / hour / day / all (default: all).",
        )
        parser.add_argument(
            "--days", type=int, default=None,
            help=(
                "Retention gün sayısı override. Sadece tek-level mode'da anlamlı "
                "(level=all'da her tablo kendi default'unu kullanır)."
            ),
        )
        parser.add_argument(
            "--batch-size", type=int, default=BATCH_SIZE,
            help=f"Her iterasyonda silinecek satır sayısı (default: {BATCH_SIZE}).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Silmeden sayım yapar.",
        )

    def handle(self, *args, **options):
        level = options["level"]
        days_override = options["days"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        if days_override is not None and days_override <= 0:
            raise CommandError("--days pozitif olmalı.")

        if level == "all" and days_override is not None:
            raise CommandError("--days sadece tek-level modda geçerli (level=all ile kullanılamaz).")

        if level == "all":
            targets = TARGETS
        else:
            targets = [t for t in TARGETS if t.level == level]

        now = timezone.now()
        total_deleted = 0

        for target in targets:
            days = days_override if days_override is not None else self._retention_days(target)
            cutoff = now - timedelta(days=days)

            deleted = self._prune(target, cutoff, batch_size=batch_size, dry_run=dry_run)
            total_deleted += deleted

            verb = "silinecek" if dry_run else "silindi"
            self.stdout.write(self.style.SUCCESS(
                f"[{target.level}] {target.model.__name__}: {deleted} satır {verb} "
                f"(cutoff={cutoff.isoformat()}, days={days})"
            ))

        verb = "silinecek" if dry_run else "silindi"
        self.stdout.write(self.style.SUCCESS(
            f"\nToplam: {total_deleted} satır {verb}."
        ))

    @staticmethod
    def _retention_days(target: RetentionTarget) -> int:
        return int(getattr(settings, target.settings_key, target.default_days))

    @staticmethod
    def _prune(target: RetentionTarget, cutoff, *, batch_size: int, dry_run: bool) -> int:
        """Batch'ler halinde sil; tek büyük transaction / uzun kilitten kaçın."""
        filter_kwargs = {f"{target.timestamp_field}__lt": cutoff}
        qs = target.model.objects.filter(**filter_kwargs)

        if dry_run:
            return qs.count()

        total = 0
        while True:
            with transaction.atomic():
                # İlgili satırların pk'larını al, onları sil. .delete()'i kuyrukta
                # topluca atmaktansa ID-bazlı küçük batch'ler daha sağlıklı.
                ids = list(qs.values_list("pk", flat=True)[:batch_size])
                if not ids:
                    break
                deleted, _ = target.model.objects.filter(pk__in=ids).delete()
                total += deleted
            if len(ids) < batch_size:
                break
        return total
