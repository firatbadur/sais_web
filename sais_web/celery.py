"""Celery uygulama instance'ı.

Beat scheduler `django_celery_beat.DatabaseScheduler` kullanır — periyodik
task tanımları DB'de tutulur ve `seed_periodic_tasks` komutu ile yazılır.
Worker ve beat ayrı process'ler olarak çalışır (Docker'da iki ayrı servis).
"""
import logging
import os

from celery import Celery
from celery.signals import worker_shutdown


logger = logging.getLogger(__name__)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sais_web.settings")

app = Celery("sais_web")

# settings.py içindeki CELERY_* anahtarlarını yükle
app.config_from_object("django.conf:settings", namespace="CELERY")

# Dashboard "Açık Bağlantıları Kapat" düğmesi prefork child process'lerini geri
# dönüştürerek (pool_restart) açık socket'leri kapatır — bu özellik default
# kapalıdır, açıkça etkinleştirilmeli.
app.conf.worker_pool_restarts = True

# Yüklü tüm app'lerin tasks.py modülünü otomatik keşfet
app.autodiscover_tasks()


@worker_shutdown.connect
def _close_scada_connection_pool(**kwargs):
    """Worker durdurulurken persistent TCP/serial socket'leri temiz kapat."""
    try:
        from scada_io.connection_pool import close_all
        close_all()
    except Exception:  # noqa: BLE001
        logger.exception("worker_shutdown: scada_io.connection_pool.close_all hatası")


@app.task(bind=True)
def debug_task(self):
    """Diagnostic — `from sais_web.celery import debug_task; debug_task.delay()`."""
    print(f"Request: {self.request!r}")
