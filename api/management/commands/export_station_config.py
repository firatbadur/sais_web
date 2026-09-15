"""
İstasyon taşıma — konfigürasyonu JSON dosyasına aktarır (Yedekleme sayfasının CLI karşılığı).

Kullanım:
    python manage.py export_station_config --output config.json
    python manage.py export_station_config --sections users,web --output config.json

`core` (istasyon konfigürasyonu) her zaman dahildir. Opsiyonel bölümler:
users / web / notifications / periodic_tasks (verilmezse hepsi). Lisans asla taşınmaz.
Bölüm listesi: api/config_transfer.py SECTIONS.
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder

from api.config_transfer import SECTIONS, build_export, export_filename, record_export


class Command(BaseCommand):
    help = "Konfigürasyonu (okuma geçmişi hariç) taşınabilir JSON'a aktarır."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="", help="Çıktı dosyası (varsayılan: otomatik ad).")
        parser.add_argument(
            "--sections", default="",
            help="Virgüllü opsiyonel bölümler (boş = hepsi): " + ", ".join(SECTIONS),
        )

    def handle(self, *args, **options):
        sections = [s.strip() for s in options["sections"].split(",") if s.strip()] or list(SECTIONS)
        payload = build_export(sections)
        output = options["output"] or export_filename()
        # Açıkça UTF-8 (Windows yerel kodlaması loaddata'yı bozar — export_config notu).
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder)
        record_export(payload, output)
        total = sum(payload["counts"].values())
        self.stdout.write(self.style.SUCCESS(
            f"Konfigürasyon dışa aktarıldı: {output} ({total} kayıt, "
            f"bölümler={','.join(payload['sections'])})."
        ))
