"""Üretilen rapor (GeneratedReport) retention'ı — eski kayıt + dosyaları siler.

Kullanım:
    python manage.py prune_generated_reports [--days N] [--dry-run]

`settings.REPORT_RETENTION_DAYS` (default 90) günden eski kayıtlar silinir;
`GeneratedReport.delete()` override'ı PDF/Excel dosyalarını da diskten kaldırır.
Beat: `api.tasks.prune_generated_reports_task` (gecelik).
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "REPORT_RETENTION_DAYS'den eski üretilen rapor kayıtlarını + dosyalarını siler."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None,
                            help="Retention gün sayısı (default: settings.REPORT_RETENTION_DAYS)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Silme yapma; yalnız sayıyı raporla.")

    def handle(self, *args, **options):
        from api.models import GeneratedReport

        days = options["days"] or getattr(settings, "REPORT_RETENTION_DAYS", 90)
        cutoff = timezone.now() - timedelta(days=days)
        qs = GeneratedReport.objects.filter(started_at__lt=cutoff)
        count = qs.count()

        if options["dry_run"]:
            self.stdout.write(f"[dry-run] {days} günden eski {count} rapor kaydı silinecekti.")
            return

        deleted = 0
        # Tek tek delete() — dosya temizliği model override'ında.
        for rep in qs.iterator(chunk_size=200):
            rep.delete()
            deleted += 1

        self.stdout.write(self.style.SUCCESS(
            f"{days} günden eski {deleted} rapor kaydı + dosyaları silindi."
        ))
