from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        # Auth olay sinyallerini bağla (giriş/çıkış/başarısız giriş → SystemLog).
        from . import signals  # noqa: F401
