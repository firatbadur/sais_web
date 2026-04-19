"""Django project package.

Celery uygulamasını proje yüklendiğinde import et — `@shared_task` decorator'ı
kayıt için gerekli.
"""
from .celery import app as celery_app


__all__ = ("celery_app",)
