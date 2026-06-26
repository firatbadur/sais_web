"""Geçerli veri istatistiğini senkron hesapla (debug / manuel backfill).

Kullanım::

    python manage.py compute_sim_valid_stats_once                 # geçerli ay, tüm aktif kabinler
    python manage.py compute_sim_valid_stats_once --month 2026-05 # belirli ay
    python manage.py compute_sim_valid_stats_once --cabinet 3     # tek kabin

Job (``sais_domain.tasks.compute_sim_valid_stats``) ile aynı mantık; Celery
olmadan elle çalıştırmak / geçmiş ayı backfill etmek için.
"""
from django.core.management.base import BaseCommand

from sais_domain.tasks import compute_sim_valid_stats


class Command(BaseCommand):
    help = "SimValidDay istatistiğini senkron hesaplar (gün gün)."

    def add_arguments(self, parser):
        parser.add_argument("--month", default=None, help="YYYY-AA (yoksa geçerli ay)")
        parser.add_argument("--cabinet", type=int, default=None, help="Kabin ID (yoksa tümü)")

    def handle(self, *args, **options):
        result = compute_sim_valid_stats(
            cabinet_id=options.get("cabinet"),
            month=options.get("month"),
        )
        self.stdout.write(self.style.SUCCESS(f"Tamamlandı: {result}"))
