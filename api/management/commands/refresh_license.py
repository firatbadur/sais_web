"""
Lisans manifest'ini uzaktan (LICENSE_URL) çekip doğrular ve uygular.

Beat (her 6 saat), container startup ve dashboard "Şimdi Yenile" butonu bunu çağırır.

Kullanım:
    python manage.py refresh_license
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from api.licensing import LicenseError, fetch_and_refresh


class Command(BaseCommand):
    help = "Lisans manifest'ini uzaktan çekip doğrular ve uygular."

    def handle(self, *args, **options):
        try:
            lic = fetch_and_refresh()
        except LicenseError as exc:
            self.stderr.write(self.style.ERROR(f"Lisans geçersiz: {exc}"))
            return
        if lic.last_check_ok:
            self.stdout.write(self.style.SUCCESS(
                f"Lisans güncellendi: key={lic.license_key} status={lic.status} "
                f"valid_until={lic.valid_until}"
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Lisans çekilemedi ({lic.last_error}); mevcut token korunuyor "
                f"(status={lic.status}, valid_until={lic.valid_until})."
            ))
