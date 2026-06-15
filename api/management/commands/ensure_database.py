"""
PostgreSQL'de uygulama veritabanini (varsayilan 'envisoft') migrate'ten ONCE
olustur.

Bundled `postgres` container POSTGRES_DB env'i ile veritabanini ilk acilista
kendisi olusturur; ancak harici/elle kurulan PostgreSQL'de veya db adi env'den
sonra degistiyse garanti yoktur. Bu komut `postgres` bakim DB'sine baglanip
hedef veritabani yoksa `CREATE DATABASE` yapar. Idempotent; PostgreSQL hazir
olana dek tekrar dener (container ilk acilista yavas kalkabilir).

Generic SCADA altyapisi -> api/. Web container'i acilista `migrate`'ten once
bunu cagirir (docker-compose web command zinciri).
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.db_admin import maintenance_connection


class Command(BaseCommand):
    help = "Uygulama veritabanini yoksa olusturur (migrate'ten once)."

    def add_arguments(self, parser):
        parser.add_argument("--retries", type=int, default=40,
                            help="PostgreSQL'e baglanma deneme sayisi (default 40).")
        parser.add_argument("--delay", type=int, default=3,
                            help="Denemeler arasi bekleme saniyesi (default 3).")

    def handle(self, *args, **options):
        name = settings.DATABASES["default"]["NAME"]

        # CREATE DATABASE adi parametrelenemez -> string'e gomulur. Guvenlik icin
        # adi alfanumerik (+ alt cizgi) ile sinirla.
        if not name or not name.replace("_", "").isalnum():
            raise CommandError(f"Guvensiz veritabani adi, reddedildi: {name!r}")

        retries = options["retries"]
        delay = options["delay"]
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                conn = maintenance_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
                    if cur.fetchone() is None:
                        # CREATE DATABASE transaction icinde olamaz; baglanti
                        # autocommit (maintenance_connection).
                        cur.execute(f'CREATE DATABASE "{name}"')
                finally:
                    conn.close()
                self.stdout.write(self.style.SUCCESS(f"Veritabani '{name}' hazir."))
                return
            except Exception as exc:  # psycopg.Error vb.
                last_err = exc
                self.stdout.write(f"PostgreSQL bekleniyor ({attempt}/{retries})...")
                time.sleep(delay)

        raise CommandError(f"Veritabani '{name}' olusturulamadi: {last_err}")
