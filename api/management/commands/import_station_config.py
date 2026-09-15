"""
İstasyon taşıma — `export_station_config` dosyasını bu kuruluma yükler (CLI karşılığı).

DİKKAT: seçili bölümlerin mevcut kayıtları SİLİNİP dosyadakiyle değiştirilir
(silinen sensörlere/istasyonlara bağlı okuma geçmişi de gider). Varsayılan olarak
önce tam `pg_dump` güvenlik yedeği alınır; alınamazsa hiçbir şey değişmez.

Kullanım:
    python manage.py import_station_config --file config.json --yes
    python manage.py import_station_config --file config.json --sections web --yes
    python manage.py import_station_config --file config.json --yes --skip-safety-backup
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from api.config_transfer import SECTIONS, ConfigTransferError, run_import, stage_import


class Command(BaseCommand):
    help = "Konfigürasyon aktarım dosyasını yükler (seçili bölümleri tamamen değiştirir)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument(
            "--sections", default="",
            help="Virgüllü opsiyonel bölümler (boş = dosyadaki hepsi): " + ", ".join(SECTIONS),
        )
        parser.add_argument("--yes", action="store_true", help="Onay (zorunlu).")
        parser.add_argument(
            "--skip-safety-backup", action="store_true",
            help="Öncesinde pg_dump güvenlik yedeği ALMA (önerilmez; dev/test).",
        )

    def handle(self, *args, **options):
        path = options["file"]
        if not os.path.exists(path):
            raise CommandError(f"Dosya bulunamadı: {path}")
        if not options["yes"]:
            raise CommandError("Mevcut konfigürasyon silinecek; onaylamak için --yes verin.")

        sections = [s.strip() for s in options["sections"].split(",") if s.strip()] or None
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            rec = stage_import(raw, os.path.basename(path), sections)
        except ConfigTransferError as exc:
            raise CommandError(str(exc))

        result = run_import(rec.pk, skip_safety_backup=options["skip_safety_backup"])
        if "error" in result:
            raise CommandError(result["error"])
        self.stdout.write(self.style.SUCCESS(
            f"İçe aktarıldı: bölümler={','.join(result['sections'])}, "
            f"kayıt={sum(result['loaded'].values())}, atlanan={result['skipped']}, "
            f"null'lanan referans={result['nulled']}."
        ))
