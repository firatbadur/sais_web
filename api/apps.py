from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        # Sensör eklenince otomatik alarm tanımı oluştur (post_save signal).
        from . import alarm_autocreate
        alarm_autocreate.connect()
        # Connection değişince seri köprü config'ini yeniden üret.
        from . import serial_bridge
        serial_bridge.connect_signals()
