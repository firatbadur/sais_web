from django.contrib import admin

from .models import EnvisoftChannel


@admin.register(EnvisoftChannel)
class EnvisoftChannelAdmin(admin.ModelAdmin):
    list_display = ("id", "parameter", "envi_channel_id")
    search_fields = ("parameter__parameter_name", "envi_channel_id")
    autocomplete_fields = ("parameter",)
    ordering = ("envi_channel_id",)
