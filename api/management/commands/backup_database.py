"""
PostgreSQL veritabanının tam yedeğini (.dump) alır ve retention politikasını
uygular.

Yedek dosyaları `settings.BACKUP_DIR` altına yazılır (app + celery_worker
container'larına mount edilen volume). `pg_dump -Fc` (custom format) ile alınır;
sıkıştırma + selective restore içerir. Her yedek alındığı `APP_VERSION` +
migration durumuyla damgalanır — geri yüklemede şema uyumu için.

Kullanım:
    python manage.py backup_database --tier=daily        # politika kapalıysa atlar
    python manage.py backup_database --tier=manual --force
    python manage.py backup_database --tier=daily --no-prune
    python manage.py backup_database --tier=daily --dry-run   # ne yapılacağını gösterir

Tier'lar: daily / weekly / monthly / yearly / manual. Otomatik (beat) çağrılar
`force` olmadan gelir; `BackupPolicy.enabled=False` ise yedek alınmaz.
"""
from __future__ import annotations

import os
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.db_admin import current_migration_state, database_name, pg_env
from api.models import BackupPolicy, DatabaseBackup


class Command(BaseCommand):
    help = "PostgreSQL veritabanının tam yedeğini alır ve retention uygular."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tier", required=True,
            choices=[t for t, _ in DatabaseBackup._meta.get_field("tier").choices],
            help="Yedek periyodu: daily / weekly / monthly / yearly / manual.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="BackupPolicy.enabled=False olsa bile yedek al (manuel tetik).",
        )
        parser.add_argument(
            "--no-prune", action="store_true",
            help="Retention temizliğini atla.",
        )
        parser.add_argument(
            "--user-id", type=int, default=None,
            help="Tetikleyen kullanıcı id (manuel tetik audit'i için).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Yedek almadan ne yapılacağını yazdırır.",
        )

    def handle(self, *args, **options):
        tier = options["tier"]
        force = options["force"]
        dry_run = options["dry_run"]
        user_id = options["user_id"]

        BackupPolicy.ensure_defaults()
        policy = BackupPolicy.objects.get(tier=tier)

        if not force and not policy.enabled:
            self.stdout.write(self.style.WARNING(
                f"[{tier}] politika kapalı (enabled=False) — yedek atlandı."
            ))
            return

        backup_dir = settings.BACKUP_DIR
        if not dry_run and not os.path.isdir(backup_dir):
            # Volume mount edilmemiş olabilir. Net hata ver.
            raise CommandError(
                f"BACKUP_DIR erişilemez: {backup_dir}. pg_backups volume mount "
                f"gerekiyor (app/celery_worker container'larına)."
            )

        db = database_name()
        now = timezone.localtime()
        filename = f"{db}__{tier}__{now.strftime('%Y%m%d_%H%M%S')}.dump"
        path = os.path.join(backup_dir, filename)

        if dry_run:
            self.stdout.write(
                f"[DRY-RUN] {db} → {path} (tier={tier}, retention={policy.retention})"
            )
            return

        record = DatabaseBackup.objects.create(
            tier=tier, filename=filename, path=path, status="running",
            db_name=db, app_version=getattr(settings, "APP_VERSION", "dev"),
            migration_state=current_migration_state(),
            trigger="manual" if (force or user_id) else "auto",
            triggered_by_id=user_id,
        )

        try:
            self._run_backup(db, path)
            self._verify(path)
            record.size_bytes = os.path.getsize(path) if os.path.exists(path) else 0
            record.status = "success"
            record.finished_at = timezone.now()
            record.save(update_fields=["size_bytes", "status", "finished_at"])
            self.stdout.write(self.style.SUCCESS(
                f"[{tier}] Yedek alındı: {filename} ({record.size_bytes} byte)"
            ))
        except Exception as exc:  # noqa: BLE001 — hata DB'ye kaydedilip yeniden fırlatılır
            record.status = "failed"
            record.error = str(exc)[:4000]
            record.finished_at = timezone.now()
            record.save(update_fields=["status", "error", "finished_at"])
            raise CommandError(f"Yedek başarısız: {exc}")

        if not options["no_prune"]:
            self._prune(tier, policy.retention)

    def _run_backup(self, db: str, path: str):
        conn = settings.DATABASES["default"]
        # -Fc: custom format (sıkıştırma + selective/clean restore + blok CRC).
        # -Z 6: orta seviye sıkıştırma. --no-owner/--no-privileges: restore'da
        # rol/yetki çakışmasını önler (yeni DB'de aynı roller olmayabilir).
        cmd = [
            "pg_dump",
            "-h", str(conn.get("HOST") or "localhost"),
            "-p", str(conn.get("PORT") or "5432"),
            "-U", str(conn.get("USER") or ""),
            "-d", db,
            "-Fc", "-Z", "6",
            "--no-owner", "--no-privileges",
            "-f", path,
        ]
        proc = subprocess.run(cmd, env=pg_env(), capture_output=True, text=True)
        if proc.returncode != 0:
            raise CommandError(f"pg_dump başarısız: {proc.stderr.strip()}")

    def _verify(self, path: str):
        # Custom-format dump'ın içerik listesini (TOC) oku — bozuk/yarım dosyayı
        # yakalar. pg_restore --list dosyaya yazmadan yalnızca okur.
        proc = subprocess.run(
            ["pg_restore", "--list", path],
            env=pg_env(), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise CommandError(f"Yedek doğrulanamadı: {proc.stderr.strip()}")

    def _prune(self, tier: str, retention: int):
        """Bu tier'da `retention` adetten fazla başarılı yedeğin dosyasını sil."""
        kept = (
            DatabaseBackup.objects
            .filter(tier=tier, status="success", pruned=False)
            .order_by("-started_at")
        )
        to_prune = list(kept[retention:])
        removed = 0
        for rec in to_prune:
            try:
                if rec.path and os.path.exists(rec.path):
                    os.remove(rec.path)
            except OSError as exc:
                self.stderr.write(f"  ! {rec.filename} silinemedi: {exc}")
                continue
            rec.pruned = True
            rec.save(update_fields=["pruned"])
            removed += 1
        if removed:
            self.stdout.write(self.style.SUCCESS(
                f"[{tier}] Retention: {removed} eski yedek dosyası silindi "
                f"(saklanan: {retention})."
            ))
