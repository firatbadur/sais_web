"""
Connection tablosundan seri köprü config'ini (serial-bridge.json) üretir.

Container startup (web command zinciri) çağırır; Connection kayıtları değişince
sinyal zaten üretir — bu komut stack kapalıyken değişen/bayatlayan dosyayı boot'ta
tazeler. Idempotent — DB → dosya senkronu.

Kullanım:
    python manage.py render_serial_bridge
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from api import serial_bridge


class Command(BaseCommand):
    help = "Aktif seri Connection'lardan serial-bridge.json üretir."

    def handle(self, *args, **options):
        ok, error = serial_bridge.apply()
        if ok and error == "skipped":
            self.stdout.write("Seri köprü dizini yok — atlandı (SERIAL_BRIDGE_DIR).")
        elif ok:
            self.stdout.write(self.style.SUCCESS("serial-bridge.json üretildi."))
        else:
            self.stdout.write(self.style.WARNING(
                f"serial-bridge.json üretilemedi: {error}"
            ))
