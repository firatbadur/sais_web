"""
WebSettings kaydından paylaşılan volume'deki Caddyfile'ı üretir.

Container startup (web command zinciri) ve dashboard Web Erişim Ayarları kaydı
bunu çağırır. Idempotent — DB → dosya senkronu. Caddy `--watch` ile reload eder.

Kullanım:
    python manage.py render_caddyfile
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from api import web_proxy
from api.models import WebSettings


class Command(BaseCommand):
    help = "WebSettings'ten Caddyfile (+ manuel cert dosyaları) üretir."

    def handle(self, *args, **options):
        ws = WebSettings.load()
        ok, error = web_proxy.apply(ws)
        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"Caddyfile üretildi (mode={ws.tls_mode}, domain={ws.domain or '—'}, "
                f"enabled={ws.enabled})."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Caddyfile üretilemedi: {error}"
            ))
