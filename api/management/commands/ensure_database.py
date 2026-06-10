"""
MSSQL'de uygulama veritabanini (varsayilan 'envisoft') migrate'ten ONCE olustur.

mssql-django veritabanini kendisi olusturmaz; container'li SQL Server yalnizca
sistem veritabanlariyla (master vs.) gelir. Bu komut `master`'a baglanip hedef
veritabani yoksa `CREATE DATABASE` yapar. Idempotent; SQL Server hazir olana
dek tekrar dener (container ilk acilista yavas kalkar).

Generic SCADA altyapisi -> api/. Web container'i acilista `migrate`'ten once
bunu cagirir (docker-compose web command zinciri).
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Uygulama veritabanini yoksa olusturur (MSSQL'de migrate'ten once gerekir)."

    def add_arguments(self, parser):
        parser.add_argument("--retries", type=int, default=40,
                            help="SQL Server'a baglanma deneme sayisi (default 40).")
        parser.add_argument("--delay", type=int, default=3,
                            help="Denemeler arasi bekleme saniyesi (default 3).")

    def handle(self, *args, **options):
        import pyodbc

        db = settings.DATABASES["default"]
        name = db["NAME"]
        db_opts = db.get("OPTIONS", {})
        driver = db_opts.get("driver", "ODBC Driver 18 for SQL Server")
        extra = db_opts.get("extra_params", "TrustServerCertificate=yes")

        # CREATE DATABASE adi parametrelenemez -> string'e gomulur. Guvenlik icin
        # adi alfanumerik (+ alt cizgi) ile sinirla.
        if not name or not name.replace("_", "").isalnum():
            raise CommandError(f"Guvensiz veritabani adi, reddedildi: {name!r}")

        conn_str = (
            f"DRIVER={{{driver}}};SERVER={db['HOST']},{db['PORT']};DATABASE=master;"
            f"UID={db['USER']};PWD={db['PASSWORD']};{extra};"
        )

        retries = options["retries"]
        delay = options["delay"]
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                conn = pyodbc.connect(conn_str, autocommit=True, timeout=5)
                cursor = conn.cursor()
                cursor.execute(f"IF DB_ID('{name}') IS NULL CREATE DATABASE [{name}]")
                conn.close()
                self.stdout.write(self.style.SUCCESS(f"Veritabani '{name}' hazir."))
                return
            except Exception as exc:  # pyodbc.Error vb.
                last_err = exc
                self.stdout.write(f"SQL Server bekleniyor ({attempt}/{retries})...")
                time.sleep(delay)

        raise CommandError(f"Veritabani '{name}' olusturulamadi: {last_err}")
