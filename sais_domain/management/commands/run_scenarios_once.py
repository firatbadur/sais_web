"""Numune senaryosu motorunu bir kez senkron çalıştırır (debug).

Kullanım:
    python manage.py run_scenarios_once

Beat task'ı (`sais_domain.tasks.run_scenarios`) ile aynı `scenario_engine.run()`'ı
çağırır — lisans gate'i olmadan. Saha/test ortamında tek bir değerlendirme
cycle'ını manuel tetiklemek için.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Numune senaryosu motorunu bir kez çalıştırır (senkron, debug)."

    def handle(self, *args, **options):
        from sais_domain.scenario_engine import run

        summary = run()
        self.stdout.write(self.style.SUCCESS(f"Senaryo motoru çalıştı: {summary}"))
