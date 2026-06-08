"""
İmzalı bir lisans token'ını dosyadan/STDIN'den uygular (offline / internetsiz saha).

İnternet erişimi olmayan ya da manuel kurtarma gereken sahalarda, dashboard'dan
yapıştırılan veya elde taşınan token'ı uygulamak için.

Kullanım:
    python manage.py apply_license --file=site-abc.json
    type token.json | python manage.py apply_license --stdin
"""
from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand, CommandError

from api.licensing import LicenseError, apply_token


class Command(BaseCommand):
    help = "İmzalı lisans token'ını dosyadan/STDIN'den uygular (offline)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=None, help="Token JSON dosyası.")
        parser.add_argument("--stdin", action="store_true", help="Token'ı STDIN'den oku.")

    def handle(self, *args, **options):
        if options["stdin"]:
            raw = sys.stdin.read()
        elif options["file"]:
            with open(options["file"], encoding="utf-8") as fh:
                raw = fh.read()
        else:
            raise CommandError("--file veya --stdin gerekli.")

        try:
            token = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Token JSON ayrıştırılamadı: {exc}")

        try:
            lic = apply_token(token, source="manual")
        except LicenseError as exc:
            raise CommandError(f"Lisans uygulanamadı: {exc}")

        self.stdout.write(self.style.SUCCESS(
            f"Lisans uygulandı: key={lic.license_key} status={lic.status} "
            f"valid_until={lic.valid_until}"
        ))
