"""
`export_config` ile alınan konfigürasyon JSON'unu yeni (boş) PostgreSQL DB'ye
yükler — MSSQL → PostgreSQL geçişinin import adımı.

KRİTİK SIRA: bu komut taze (migrate edilmiş, HENÜZ seed çalıştırılmamış) bir DB'ye
uygulanmalıdır. Seed komutları (`seed_initial_data` vb.) `Station.get_or_create(id=1)`
gibi sabit-PK kayıtlar oluşturur; import ÖNCE çalışmazsa PK çakışması (IntegrityError)
olur. Bu yüzden komut başında boşluk kontrolü vardır.

Akış (yeni PostgreSQL DB):
    python manage.py migrate --noinput
    python manage.py import_config --file /tmp/config_export.json   # seed'lerden ÖNCE
    python manage.py seed_initial_data        # sonra — idempotent, import'u bulur/atlar
    python manage.py seed_sais_data
    python manage.py seed_periodic_tasks

PostgreSQL sequence reset (KRİTİK): kayıtlar PK korunarak yüklendiğinden, PG'nin
id sequence'leri ilerletilmez. Reset yapılmazsa import çalışmış görünür ama ilk
YENİ kayıtta (örn. yeni Station) sequence baştan başlayıp mevcut PK ile çakışır.
Bu komut import sonrası sequence'leri otomatik resetler.
"""
from __future__ import annotations

import os

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection

from api.config_migration import CONFIG_MODELS, EMPTINESS_GUARD_MODELS


class Command(BaseCommand):
    help = "Konfigürasyon JSON'unu taze PostgreSQL DB'ye yükler (config-only göç)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True,
                            help="export_config ile üretilen JSON dosyası.")
        parser.add_argument(
            "--allow-nonempty", action="store_true",
            help="Boşluk kontrolünü atla (RİSKLİ — PK çakışmasına yol açabilir).",
        )

    def handle(self, *args, **options):
        path = options["file"]
        if not os.path.exists(path):
            raise CommandError(f"Dosya bulunamadı: {path}")

        if not options["allow_nonempty"]:
            self._assert_empty()

        self.stdout.write(f"Yükleniyor: {path} ...")
        call_command("loaddata", path)

        self._reset_sequences()
        self.stdout.write(self.style.SUCCESS(
            "Konfigürasyon yüklendi + sequence'ler resetlendi. "
            "Sıradaki adım: seed_initial_data / seed_sais_data / seed_periodic_tasks."
        ))

    def _assert_empty(self):
        """Hedef DB config açısından boş mu? Değilse seed-önce-import ihlali."""
        for label in EMPTINESS_GUARD_MODELS:
            model = apps.get_model(label)
            if model.objects.exists():
                raise CommandError(
                    f"Hedef DB boş değil ({label} kayıt içeriyor). import_config "
                    "yalnız taze (migrate edilmiş, seed çalıştırılmamış) DB'ye "
                    "uygulanır. Zorlamak için --allow-nonempty (önerilmez)."
                )

    def _reset_sequences(self):
        """Yüklenen modellerin PK sequence'lerini max(id)'ye taşı (PG)."""
        models = [apps.get_model(label) for label in CONFIG_MODELS]
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        if not statements:
            return
        with connection.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)
        self.stdout.write(f"  {len(statements)} sequence resetlendi.")
