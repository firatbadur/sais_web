from django.apps import AppConfig


class SaisDomainConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sais_domain"
    verbose_name = "Envisoft WebX Alan Uzantıları"

    def ready(self):
        # SaisCabinet → Bakanlık kullanıcısı otomatik senkron sinyalini bağla.
        from . import signals  # noqa: F401
