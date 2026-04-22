"""
Tek bir Connection'ı manuel olarak polla — debug/test için.

Worker/beat'i ayağa kaldırmadan reader davranışını gözlemlemenin en
hızlı yolu. Reading + SensorLatest'i normal yolla günceller.

Kullanım:
    python manage.py poll_once <connection_id>
    python manage.py poll_once --name "test_smoke"
"""
from django.core.management.base import BaseCommand, CommandError

from api.models import Connection
from scada_io import connection_pool
from scada_io.tasks import poll_connection


class Command(BaseCommand):
    help = "Tek bir Connection için poll_connection task'ını sync çalıştırır."

    def add_arguments(self, parser):
        parser.add_argument("conn_id", nargs="?", type=int, default=None,
                            help="Connection.id")
        parser.add_argument("--name", help="Connection.name (id alternatifi)")

    def handle(self, *args, **options):
        conn = None
        if options.get("conn_id"):
            conn = Connection.objects.filter(pk=options["conn_id"]).first()
        elif options.get("name"):
            conn = Connection.objects.filter(name=options["name"]).first()
        if conn is None:
            raise CommandError("Connection bulunamadı (id veya --name verin).")

        self.stdout.write(f"Polling {conn} (id={conn.pk}, protocol={conn.protocol})...")
        try:
            # Sync çağrı: Celery'ye enqueue etmek yerine direkt çalıştır.
            poll_connection(conn.pk)
            conn.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(
                f"Tamamlandı. last_polled_at={conn.last_polled_at}, "
                f"last_connected_at={conn.last_connected_at}, "
                f"last_error_message={conn.last_error_message or '-'}"
            ))
        finally:
            # Debug komutu çıkarken persistent pool'u temiz kapat (process exit'te
            # OS zaten kapatır ama explicit clean teardown daha sağlıklı).
            connection_pool.close_all()
