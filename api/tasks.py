"""
api app'i için periyodik Celery wrap task'lar.

Bu task'lar mevcut management komutlarını çağırır — komutlar manuel
çalıştırma için de korunur (tek yerde mantık, iki tetikleyici).
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command


logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.aggregate_readings_15m")
def aggregate_readings_15m():
    """15 dakikalık aggregate — son 2 saatlik aralığı yeniden hesaplar."""
    call_command("aggregate_readings", bucket="15m", hours=2)


@shared_task(name="api.tasks.aggregate_readings_hourly")
def aggregate_readings_hourly():
    """Saatlik aggregate — son 6 saatlik aralığı yeniden hesaplar."""
    call_command("aggregate_readings", bucket="hour", hours=6)


@shared_task(name="api.tasks.aggregate_readings_daily")
def aggregate_readings_daily():
    """Günlük aggregate — son 48 saatlik aralığı kapsar (gün başı kayması için marj)."""
    call_command("aggregate_readings", bucket="day", hours=48)


@shared_task(name="api.tasks.prune_api_logs_task")
def prune_api_logs_task():
    """settings.API_LOG_RETENTION_DAYS'den eski ApiLog satırlarını siler."""
    call_command("prune_api_logs")


@shared_task(name="api.tasks.prune_readings_task")
def prune_readings_task():
    """Reading + aggregate tablolarından retention'ı geçenleri siler.

    Her level (raw/15m/hour/day) kendi settings değişkenini kullanır:
    READING_RETENTION_{RAW,15M,HOURLY,DAILY}_DAYS.
    """
    call_command("prune_readings")


@shared_task(name="api.tasks.backup_database_run")
def backup_database_run(tier="manual", force=False, user_id=None):
    """Bir tier için DB yedeği alır (beat + manuel tetik ortak).

    Beat çağrıları `force=False` gelir → `BackupPolicy.enabled` kapalıysa atlanır.
    Manuel (dashboard) tetik `force=True` ile gelir.
    """
    call_command("backup_database", tier=tier, force=force, user_id=user_id)


@shared_task(name="api.tasks.restore_database_run")
def restore_database_run(backup_id, run_migrate=True, user_id=None):
    """Bir yedekten DB'yi geri yükler (ağır iş — worker'da çalışır)."""
    call_command(
        "restore_database",
        backup_id=backup_id,
        no_migrate=not run_migrate,
        yes=True,
        user_id=user_id,
    )


@shared_task(name="api.tasks.license_refresh_task")
def license_refresh_task():
    """Lisans manifest'ini uzaktan çekip doğrular ve uygular (beat: her 6 saat).

    Lisans bitse bile bu task çalışmaya devam eder — yenilenince sistem kendini
    toparlasın diye.
    """
    call_command("refresh_license")
