"""Onaylanmış manuel veri içe aktarımını senkron (worker'sız) uygular — saha müdahalesi.

Celery worker çalışmıyorsa sayfada "içe aktarılıyor…" durumu sonsuza dek kalır;
bu komut işi doğrudan yürütür. Kuyrukta bekleyen Celery mesajı sonradan çalışsa
bile kayıt artık ``running`` olmadığı için hiçbir şey yapmaz (mükerrer yazım yok).

Kullanım:
    python manage.py apply_manual_import_once            # bekleyen tüm 'running' kayıtlar
    python manage.py apply_manual_import_once --id 12
"""
from django.core.management.base import BaseCommand

from sais_domain.models import ManualDataImport
from sais_domain.tasks import apply_manual_import


class Command(BaseCommand):
    help = "Onaylanmış (running) manuel veri içe aktarımlarını senkron uygular."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, default=None, help="Yalnız bu içe aktarım.")

    def handle(self, *args, **options):
        qs = ManualDataImport.objects.filter(status=ManualDataImport.STATUS_RUNNING)
        if options["id"]:
            qs = qs.filter(pk=options["id"])
        ids = list(qs.order_by("id").values_list("id", flat=True))
        if not ids:
            self.stdout.write(self.style.WARNING("Uygulanmayı bekleyen (running) içe aktarım yok."))
            return
        for pk in ids:
            result = apply_manual_import(pk)
            style = self.style.ERROR if "error" in result else self.style.SUCCESS
            self.stdout.write(style(f"#{pk}: {result}"))
