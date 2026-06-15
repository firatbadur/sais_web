"""
Bir `.dump` yedeğinden PostgreSQL veritabanını geri yükler (pg_restore) —
sürüm-bilinçli.

Geri yükleme YIKICI bir işlemdir: hedef DB'ye açık tüm bağlantılar düşürülür
(`pg_terminate_backend`), DB drop + create edilir, sonra `pg_restore` ile yedek
yazılır. Kısa bir kesinti yaratır.

Sürüm uyumu (`api.db_admin.compare_schema`):
  - exact   → restore; migrate yok.
  - forward → restore → migrate (eski şemayı çalışan koda ileri taşı).
  - block   → reddedilir (yedek koddan yeni). `--force-unsafe` ile zorlanabilir.

Kullanım:
    python manage.py restore_database --backup-id=12 --yes
    python manage.py restore_database --backup-id=12 --yes --no-migrate
    python manage.py restore_database --file=envisoft__manual__20260608_120000.dump --yes
"""
from __future__ import annotations

import os
import subprocess

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from api.db_admin import compare_schema, database_name, maintenance_connection, pg_env
from api.models import DatabaseBackup, DatabaseRestore


class Command(BaseCommand):
    help = "Bir .dump yedeğinden veritabanını geri yükler (sürüm-bilinçli)."

    def add_arguments(self, parser):
        parser.add_argument("--backup-id", type=int, default=None,
                            help="DatabaseBackup id'si.")
        parser.add_argument("--file", type=str, default=None,
                            help="BACKUP_DIR altındaki .dump dosya adı (backup-id alternatifi).")
        parser.add_argument("--no-migrate", action="store_true",
                            help="forward durumunda restore sonrası migrate'i atla.")
        parser.add_argument("--yes", action="store_true",
                            help="Onay sormadan geri yükle (yıkıcı işlem).")
        parser.add_argument("--force-unsafe", action="store_true",
                            help="block verdict'ini görmezden gel (önerilmez).")
        parser.add_argument("--user-id", type=int, default=None,
                            help="Tetikleyen kullanıcı id (audit).")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Onay gerekli: yıkıcı işlem. --yes ekleyin.")

        backup, path, backup_state, app_version_at_backup = self._resolve(options)

        verdict = compare_schema(backup_state)
        self.stdout.write(f"Uyumluluk: {verdict['verdict']} — {verdict['message']}")

        if verdict["verdict"] == "block" and not options["force_unsafe"]:
            raise CommandError(verdict["message"])

        run_migrate = (verdict["verdict"] == "forward") and not options["no_migrate"]
        compatibility = "forced" if (verdict["verdict"] == "block") else verdict["verdict"]

        # 'running' kaydı: restore DB'yi DEĞİŞTİRMEDEN önce başarısız olursa
        # (dosya yok / terminate hatası) audit'i yakalar. Başarılı restore tüm
        # DB'yi yedeğin içeriğiyle değiştirir → bu satır kaybolur; o yüzden
        # başarı durumunda restore SONRASI taze bir kayıt INSERT ederiz.
        filename = os.path.basename(path)
        restore = DatabaseRestore.objects.create(
            source_backup=backup,
            source_filename=filename,
            status="running",
            compatibility=compatibility,
            ran_migrate=False,
            app_version_at_backup=app_version_at_backup,
            triggered_by_id=options["user_id"],
        )
        restore_pk = restore.pk

        db = database_name()
        try:
            self._run_restore(db, path)
            # Django'nun (drop edilmiş DB'ye ait, artık geçersiz) bağlantılarını
            # kapat → yeni DB'ye taze reconnect.
            connections.close_all()
            ran_migrate = False
            if run_migrate:
                self.stdout.write("Şema migrate ediliyor (forward)...")
                call_command("migrate", "--noinput")
                ran_migrate = True
            # DB artık yedeğin içeriği — 'running' kaydı yok. Audit'i taze yaz.
            # FK yalnız yedek satırı geri yüklenen DB'de mevcutsa bağlanır.
            backup_fk = (
                backup if backup and DatabaseBackup.objects.filter(pk=backup.pk).exists()
                else None
            )
            DatabaseRestore.objects.create(
                source_backup=backup_fk,
                source_filename=filename,
                status="success",
                compatibility=compatibility,
                ran_migrate=ran_migrate,
                app_version_at_backup=app_version_at_backup,
                triggered_by_id=options["user_id"],
                finished_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS(
                f"Geri yükleme tamamlandı: {filename} "
                f"(uyumluluk={compatibility}, migrate={'evet' if ran_migrate else 'hayır'})."
            ))
            self.stdout.write(self.style.WARNING(
                "Not: Uzun-ömürlü Celery worker/beat bağlantıları için container'ları "
                "yeniden başlatmanız önerilir."
            ))
        except Exception as exc:  # noqa: BLE001
            # 'running' satırı hâlâ duruyorsa (restore DB'yi değiştirmeden önce
            # patladıysa) güncelle; satır yoksa update() no-op'tur (save'in
            # "did not affect any rows" hatasından kaçınır).
            DatabaseRestore.objects.filter(pk=restore_pk).update(
                status="failed", error=str(exc)[:4000], finished_at=timezone.now(),
            )
            raise CommandError(f"Geri yükleme başarısız: {exc}")

    def _resolve(self, options):
        """backup-id veya --file'dan (path, migration_state, app_version) çöz."""
        backup = None
        if options["backup_id"]:
            try:
                backup = DatabaseBackup.objects.get(pk=options["backup_id"])
            except DatabaseBackup.DoesNotExist:
                raise CommandError(f"DatabaseBackup id={options['backup_id']} bulunamadı.")
            if backup.pruned:
                raise CommandError(f"Bu yedeğin dosyası retention ile silinmiş ({backup.filename}).")
            path = backup.path
            state = backup.migration_state
            version = backup.app_version
        elif options["file"]:
            # Güvenlik: sadece basename, BACKUP_DIR altında.
            name = os.path.basename(options["file"])
            path = os.path.join(settings.BACKUP_DIR, name)
            # Kayıt varsa damgayı oradan al.
            backup = DatabaseBackup.objects.filter(filename=name, status="success").first()
            state = backup.migration_state if backup else None
            version = backup.app_version if backup else ""
        else:
            raise CommandError("--backup-id veya --file gerekli.")

        if not path or not os.path.exists(path):
            raise CommandError(f"Yedek dosyası bulunamadı: {path}")
        return backup, path, state, version

    def _run_restore(self, db: str, path: str):
        """Bakım DB'si üzerinden: bağlantıları kes → DROP → CREATE → pg_restore.

        Uygulamanın bağlı olduğu DB'yi geri yükleyebilmek için `postgres` bakım
        DB'sine bağlanılır; hedef DB'ye yeni bağlantı engellenir (datallowconn),
        açık oturumlar düşürülür, DB drop+create edilir. MSSQL'deki
        `SINGLE_USER WITH ROLLBACK IMMEDIATE`'in PostgreSQL karşılığı budur.
        """
        if not db or not db.replace("_", "").isalnum():
            raise CommandError(f"Güvensiz veritabanı adı, reddedildi: {db!r}")

        conn = maintenance_connection()
        try:
            cur = conn.cursor()
            # Yeni bağlantıları engelle + açık oturumları düşür (DROP penceresinde
            # gunicorn/worker/beat pool'ları reconnect edip DROP'u bloke etmesin).
            cur.execute(
                "UPDATE pg_database SET datallowconn = false WHERE datname = %s", (db,)
            )
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db,),
            )
            try:
                cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
                # Yeni DB datallowconn=true ile gelir; ayrıca CONNECT yetkisi açık.
                cur.execute(f'CREATE DATABASE "{db}"')
            except Exception:
                # DROP başarısızsa eski DB'ye erişimi geri aç (kilitli kalmasın).
                cur.execute(
                    "UPDATE pg_database SET datallowconn = true WHERE datname = %s",
                    (db,),
                )
                raise
        finally:
            conn.close()

        # Yeni boş DB'ye custom-format dump'ı geri yükle.
        proc = subprocess.run(
            [
                "pg_restore",
                "-h", str(settings.DATABASES["default"].get("HOST") or "localhost"),
                "-p", str(settings.DATABASES["default"].get("PORT") or "5432"),
                "-U", str(settings.DATABASES["default"].get("USER") or ""),
                "-d", db,
                "--no-owner", "--no-privileges",
                path,
            ],
            env=pg_env(), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise CommandError(f"pg_restore başarısız: {proc.stderr.strip()}")
