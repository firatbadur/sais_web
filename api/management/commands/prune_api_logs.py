"""
API log retention komutu — `created_at` belirli bir günden eskiyse siler.

Kullanım:
    python manage.py prune_api_logs                    # settings.API_LOG_RETENTION_DAYS (default 90)
    python manage.py prune_api_logs --days=30          # özel periyot
    python manage.py prune_api_logs --direction=in     # sadece inbound
    python manage.py prune_api_logs --direction=out    # sadece outbound
    python manage.py prune_api_logs --dry-run          # silmeden sayım

Cron örneği (Linux, her gece 03:00):
    0 3 * * * cd /app && python manage.py prune_api_logs >> /var/log/sais_prune.log 2>&1

Windows Task Scheduler ile de aynı komut periyodik tetiklenebilir.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import ApiLog


VALID_DIRECTIONS = ("in", "out", "all")


class Command(BaseCommand):
    help = "API log kayıtlarından retention'ı geçenleri siler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=None,
            help="Saklama süresi (gün). Verilmezse settings.API_LOG_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--direction", choices=VALID_DIRECTIONS, default="all",
            help="Sadece bir yönü hedefler: in / out / all (default: all).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Sayımı yapar ama silmez.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = int(getattr(settings, "API_LOG_RETENTION_DAYS", 90))
        if days <= 0:
            self.stderr.write(self.style.ERROR("--days pozitif tamsayı olmalı."))
            return

        direction = options["direction"]
        cutoff = timezone.now() - timedelta(days=days)

        qs = ApiLog.objects.filter(created_at__lt=cutoff)
        if direction != "all":
            qs = qs.filter(direction=direction)

        count = qs.count()
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                f"[dry-run] Silinecek: {count} kayıt "
                f"(direction={direction}, cutoff={cutoff.isoformat()})"
            ))
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Silinen: {deleted} kayıt (direction={direction}, cutoff={cutoff.isoformat()})"
        ))
