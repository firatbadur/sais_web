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
