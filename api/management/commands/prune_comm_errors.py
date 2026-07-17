"""İletişim hata teşhis kayıtlarından (CommErrorEvent) retention'ı geçenleri siler.

`CommErrorEvent` yalnız teşhis amaçlı biriktirilir; uzun süre saklamaya gerek yok.

Kullanım:
    python manage.py prune_comm_errors                 # settings.COMM_ERROR_RETENTION_DAYS (default 30)
    python manage.py prune_comm_errors --days=7        # özel periyot
    python manage.py prune_comm_errors --dry-run       # silmeden sayım
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import CommErrorEvent


class Command(BaseCommand):
    help = "İletişim hata teşhis kayıtlarından (CommErrorEvent) retention'ı geçenleri siler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=None,
            help="Saklama süresi (gün). Verilmezse settings.COMM_ERROR_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Sayımı yapar ama silmez.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = int(getattr(settings, "COMM_ERROR_RETENTION_DAYS", 30))
        if days <= 0:
            self.stderr.write(self.style.ERROR("--days pozitif tamsayı olmalı."))
            return

        cutoff = timezone.now() - timedelta(days=days)
        qs = CommErrorEvent.objects.filter(time_iso__lt=cutoff)

        count = qs.count()
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                f"[dry-run] Silinecek: {count} kayıt (cutoff={cutoff.isoformat()})"
            ))
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Silinen: {deleted} kayıt (cutoff={cutoff.isoformat()})"
        ))
