"""
Konfigürasyon kayıtlarını JSON'a aktarır (MSSQL → PostgreSQL geçişi için).

Yalnızca CONFIG kayıtlarını dışa aktarır (istasyon/sensör/kabin/kullanıcı/senaryo
+ periyodik task tanımları). Historian (Reading*) ve runtime/audit tabloları
KASTEN dışarıda — geçmiş ölçüm verisi taşınmaz.

Akış (saha geçişi, tek seferlik):
    # 1) Eski (MSSQL) sistem ayaktayken:
    python manage.py export_config --output /tmp/config_export.json
    # 2) Yeni (PostgreSQL) boş DB'de: migrate → import_config → seed_* (bkz. import_config)

Doğal foreign-key (`use_natural_foreign_keys=True`) açıktır: Django'nun yerleşik
`auth.Permission` / `contenttypes.ContentType` referansları PK yerine doğal
anahtarla (codename / app_label) serialize edilir → yeni DB'de migrate bunları
yeniden ürettiğinde PK farklılıkları sorun çıkarmaz. CONFIG modellerinin hiçbiri
`natural_key()` tanımlamadığından kendi FK'leri yine sayısal PK ile (korunarak)
yazılır.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand

from api.config_migration import CONFIG_MODELS


class Command(BaseCommand):
    help = "Konfigürasyon kayıtlarını JSON'a aktarır (config-only veri göçü)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", default="config_export.json",
            help="Çıktı JSON dosyası (varsayılan: config_export.json).",
        )

    def handle(self, *args, **options):
        output = options["output"]
        # dumpdata'nın `output=` dosya yazımı Windows'ta yerel kodlamayı (cp1254
        # vb.) kullanır → loaddata UTF-8 beklediği için bozulur. Çıktıyı bellekte
        # toplayıp dosyayı AÇIKÇA UTF-8 ile yazarak platform-bağımsız yaparız.
        buf = StringIO()
        call_command(
            "dumpdata",
            *CONFIG_MODELS,
            format="json",
            indent=2,
            use_natural_foreign_keys=True,
            use_natural_primary_keys=False,
            stdout=buf,
        )
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
        self.stdout.write(self.style.SUCCESS(
            f"Konfigürasyon dışa aktarıldı: {output} ({len(CONFIG_MODELS)} model)."
        ))
        self.stdout.write(
            "Sonraki adim (yeni PostgreSQL DB'de): migrate -> "
            "import_config --file=<dosya> -> seed_initial_data/seed_sais_data/seed_periodic_tasks."
        )
