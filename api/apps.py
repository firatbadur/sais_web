import os
from django.apps import AppConfig
from django.utils import timezone


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    # def ready(self):
    #     # runserver autoreload sebebiyle iki kez çalışır,
    #     # sadece ana process'te çalışsın
    #     if os.environ.get("RUN_MAIN") == "true":
    #         from django.db.utils import OperationalError, ProgrammingError
    #         from .models import Poweroff, StationInfo
    #         from .signals import register_shutdown_handler
    #
    #         # Shutdown sinyallerini yakala
    #         register_shutdown_handler()
    #
    #         try:
    #             station = StationInfo.objects.first()
    #             if not station:
    #                 return
    #
    #             # Açık kayıt varsa → muhtemelen elektrik kesintisi olmuştur, kapat
    #             open_record = Poweroff.objects.filter(
    #                 station=station, end_date__isnull=True
    #             ).first()
    #             if open_record:
    #                 open_record.end_date = timezone.now()
    #                 open_record.save()
    #
    #             # Yeni kayıt başlat (servis açılışı)
    #             Poweroff.objects.create(
    #                 station=station,
    #                 start_date=timezone.now(),
    #                 end_date=None
    #             )
    #
    #         except (OperationalError, ProgrammingError):
    #             # migrate aşamasında tablo yokken hata vermesin
    #             pass
