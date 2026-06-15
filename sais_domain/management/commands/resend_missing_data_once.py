"""Eksik veri yeniden gönderim job'ını bir kez senkron çalıştırır (debug).

Kullanım:
    python manage.py resend_missing_data_once

Beat task'ı (`sais_domain.tasks.resend_missing_data`) ile aynı akışı çağırır:
her aktif kabin için Bakanlık GetMissingDates → Reading historian'dan backfill
→ SendData. Lisans + SIM açık/kapalı gate'leri task içinde uygulanır (task
gövdesi doğrudan çağrıldığı için gerçek davranışla birebir).
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Eksik veri yeniden gönderim job'ını bir kez çalıştırır (senkron, debug)."

    def handle(self, *args, **options):
        from sais_domain.tasks import resend_missing_data

        summary = resend_missing_data()
        self.stdout.write(self.style.SUCCESS(f"Eksik veri job çalıştı: {summary}"))
