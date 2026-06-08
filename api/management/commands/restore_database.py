"""
Bir `.bak` yedeğinden MSSQL veritabanını geri yükler (RESTORE) — sürüm-bilinçli.

Geri yükleme YIKICI bir işlemdir: hedef DB SINGLE_USER moda alınır (açık tüm
bağlantılar düşürülür), üzerine yedek yazılır, sonra MULTI_USER'a döndürülür.
Kısa bir kesinti yaratır.

Sürüm uyumu (`api.db_admin.compare_schema`):
  - exact   → RESTORE; migrate yok.
  - forward → RESTORE → migrate (eski şemayı çalışan koda ileri taşı).
  - block   → reddedilir (yedek koddan yeni). `--force-unsafe` ile zorlanabilir.

Kullanım:
    python manage.py restore_database --backup-id=12 --yes
    python manage.py restore_database --backup-id=12 --yes --no-migrate
    python manage.py restore_database --file=envisoft__manual__20260608_120000.bak --yes
"""
from __future__ import annotations

import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from api.db_admin import compare_schema, database_name, master_connection
from api.models import DatabaseBackup, DatabaseRestore


class Command(BaseCommand):
    help = "Bir .bak yedeğinden veritabanını geri yükler (sürüm-bilinçli)."

    def add_arguments(self, parser):
        parser.add_argument("--backup-id", type=int, default=None,
                            help="DatabaseBackup id'si.")
        parser.add_argument("--file", type=str, default=None,
                            help="BACKUP_DIR altındaki .bak dosya adı (backup-id alternatifi).")
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

        restore = DatabaseRestore.objects.create(
            source_backup=backup,
            source_filename=os.path.basename(path),
            status="running",
            compatibility=compatibility,
            ran_migrate=False,
            app_version_at_backup=app_version_at_backup,
            triggered_by_id=options["user_id"],
        )

        db = database_name()
        try:
            self._run_restore(db, path)
            # Django'nun (SINGLE_USER ile düşürülmüş) bağlantılarını kapat → taze reconnect.
            connections.close_all()
            if run_migrate:
                self.stdout.write("Şema migrate ediliyor (forward)...")
                call_command("migrate", "--noinput")
                restore.ran_migrate = True
            restore.status = "success"
            restore.finished_at = timezone.now()
            restore.save(update_fields=["status", "finished_at", "ran_migrate"])
            self.stdout.write(self.style.SUCCESS(
                f"Geri yükleme tamamlandı: {restore.source_filename} "
                f"(uyumluluk={compatibility}, migrate={'evet' if restore.ran_migrate else 'hayır'})."
            ))
            self.stdout.write(self.style.WARNING(
                "Not: Uzun-ömürlü Celery worker/beat bağlantıları için container'ları "
                "yeniden başlatmanız önerilir."
            ))
        except Exception as exc:  # noqa: BLE001
            restore.status = "failed"
            restore.error = str(exc)[:4000]
            restore.finished_at = timezone.now()
            restore.save(update_fields=["status", "error", "finished_at"])
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
        """Tek master bağlantısında: SINGLE_USER → RESTORE → MULTI_USER."""
        conn = master_connection()
        try:
            cur = conn.cursor()
            # Açık bağlantıları düşür (kendi master bağlantımız hariç).
            cur.execute(f"ALTER DATABASE [{db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            while cur.nextset():
                pass
            try:
                cur.execute(
                    f"RESTORE DATABASE [{db}] FROM DISK = ? WITH REPLACE, RECOVERY, STATS = 10",
                    path,
                )
                while cur.nextset():
                    pass
            finally:
                # Ne olursa olsun DB'yi tekrar çok-kullanıcılı yap.
                cur.execute(f"ALTER DATABASE [{db}] SET MULTI_USER")
                while cur.nextset():
                    pass
        finally:
            conn.close()
