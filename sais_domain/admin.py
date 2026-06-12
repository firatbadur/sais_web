from django.contrib import admin

from .models import (
    EnvisoftChannel,
    SaisCabinet,
    Scenario,
    ScenarioParameter,
    ScenarioRun,
    ScenarioRunLog,
    ScenarioStep,
    SimStatusPolicy,
    SystemSwitch,
)


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
        "last_sim_success_at", "last_sim_success_readtime",
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
        ("SIM son iletim (readonly)", {
            "fields": ("last_sim_success_at", "last_sim_success_readtime"),
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


@admin.register(SimStatusPolicy)
class SimStatusPolicyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "configured", "fallback_status_code", "updated_at", "updated_by")
    readonly_fields = ("updated_at", "updated_by")
    filter_horizontal = ("blocked_statuses",)

    def has_add_permission(self, request):
        return not SimStatusPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# ---- Numune senaryosu admin ------------------------------------------------

class ScenarioParameterInline(admin.TabularInline):
    model = ScenarioParameter
    extra = 0
    autocomplete_fields = ("parameter",)


class ScenarioStepInline(admin.TabularInline):
    model = ScenarioStep
    extra = 0


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "kind", "station", "is_active", "enabled",
                    "is_builtin", "avg_window", "trigger_mode", "updated_at")
    list_filter = ("kind", "is_active", "enabled", "is_builtin", "station")
    search_fields = ("name", "description", "station__name")
    autocomplete_fields = ("station", "sampler_sensor", "created_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = (ScenarioParameterInline, ScenarioStepInline)

    def has_delete_permission(self, request, obj=None):
        # Yerleşik şablonlar silinemez.
        if obj is not None and obj.is_builtin:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(ScenarioRun)
class ScenarioRunAdmin(admin.ModelAdmin):
    list_display = ("id", "scenario", "station", "run_date", "status",
                    "is_ministry", "last_step_order", "sample_code", "trigger_at", "completed_at")
    list_filter = ("status", "is_ministry", "station", "run_date")
    search_fields = ("scenario__name", "sample_code", "station__name")
    readonly_fields = [f.name for f in ScenarioRun._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ScenarioRunLog)
class ScenarioRunLogAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "station", "run", "kind", "step_order", "message")
    list_filter = ("kind", "station")
    search_fields = ("message", "skipped_reason", "station__name")
    readonly_fields = [f.name for f in ScenarioRunLog._meta.fields]

    def has_add_permission(self, request):
        return False
