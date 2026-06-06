from django.contrib import admin

from .models import EnvisoftChannel, SaisCabinet, SystemSwitch


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


@admin.register(SystemSwitch)
class SystemSwitchAdmin(admin.ModelAdmin):
    list_display = ("__str__", "sim_enabled", "envisoft_enabled", "polling_enabled",
                    "wash_active_kind", "wash_ends_at", "updated_at", "updated_by")
    readonly_fields = (
        "wash_active_kind", "wash_started_at", "wash_ends_at", "wash_started_by",
        "updated_at", "updated_by",
    )
    fieldsets = (
        ("Veri akışı", {
            "fields": ("sim_enabled", "envisoft_enabled", "polling_enabled"),
        }),
        ("Yıkama süreleri (default)", {
            "fields": ("manual_wash_duration_minutes", "weekly_wash_duration_minutes"),
        }),
        ("Aktif yıkama (readonly)", {
            "fields": ("wash_active_kind", "wash_started_at", "wash_ends_at", "wash_started_by"),
        }),
        ("Audit", {"fields": ("updated_at", "updated_by")}),
    )

    def has_add_permission(self, request):
        # Singleton — sadece bir kayıt; mevcut kayıt varsa yenisi eklenemez.
        return not SystemSwitch.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
