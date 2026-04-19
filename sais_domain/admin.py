from django.contrib import admin

from .models import EnvisoftChannel, SaisCabinet


@admin.register(SaisCabinet)
class SaisCabinetAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "device_id", "code", "name", "data_period", "auth_username", "created_at")
    list_filter = ("station", "data_period")
    search_fields = ("device_id", "code", "name", "auth_username", "station__name")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("station", "user")
    ordering = ("-created_at",)


@admin.register(EnvisoftChannel)
class EnvisoftChannelAdmin(admin.ModelAdmin):
    list_display = ("id", "parameter", "envi_channel_id")
    search_fields = ("parameter__parameter_name", "envi_channel_id")
    autocomplete_fields = ("parameter",)
    ordering = ("envi_channel_id",)
