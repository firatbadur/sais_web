"""Celery uygulama instance'ı.

Beat scheduler `django_celery_beat.DatabaseScheduler` kullanır — periyodik
task tanımları DB'de tutulur ve `seed_periodic_tasks` komutu ile yazılır.
Worker ve beat ayrı process'ler olarak çalışır (Docker'da iki ayrı servis).
"""
import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sais_web.settings")

app = Celery("sais_web")

# settings.py içindeki CELERY_* anahtarlarını yükle
app.config_from_object("django.conf:settings", namespace="CELERY")

# Yüklü tüm app'lerin tasks.py modülünü otomatik keşfet
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    """Diagnostic — `from sais_web.celery import debug_task; debug_task.delay()`."""
    print(f"Request: {self.request!r}")
