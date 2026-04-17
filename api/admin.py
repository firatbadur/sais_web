from django.contrib import admin

from .models import (
    Api_Log,
    Calibration,
    Connections,
    Log_Types,
    Out_Requests,
    Parameters,
    Poweroff,
    Reads,
    SensorInstants,
    Sensors,
    SimInformation,
    StationInfo,
    Status_Codes,
    Sys_Log,
)


@admin.register(StationInfo)
class StationInfoAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "station_type", "company", "domain", "port", "active", "user", "created_at")
    list_filter = ("station_type", "active", "company")
    search_fields = ("name", "address", "company", "domain")
    list_editable = ("active",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(SimInformation)
class SimInformationAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "sim_id", "code", "name", "data_period", "username", "created_at")
    list_filter = ("station", "data_period")
    search_fields = ("sim_id", "code", "name", "username", "station__name")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("station",)
    ordering = ("-created_at",)


@admin.register(Connections)
class ConnectionsAdmin(admin.ModelAdmin):
    list_display = (
        "id", "con_name", "communication_type", "con_type", "con_mode",
        "con_address", "port", "baudrate", "status", "created_date",
    )
    list_filter = ("communication_type", "con_type", "con_mode", "status")
    search_fields = ("con_name", "con_address")
    list_editable = ("status",)
    readonly_fields = ("created_date",)


@admin.register(Status_Codes)
class StatusCodesAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(Parameters)
class ParametersAdmin(admin.ModelAdmin):
    list_display = (
        "id", "station", "parameter_name", "parameter_txt",
        "envi_channel", "channel_number", "unit_txt",
        "gec_min", "gec_max", "olcum_min", "olcum_max",
    )
    list_filter = ("station", "unit_txt")
    search_fields = ("parameter_name", "parameter_txt", "sim_channel")
    autocomplete_fields = ("station",)
    ordering = ("station", "envi_channel")


@admin.register(Sensors)
class SensorsAdmin(admin.ModelAdmin):
    list_display = (
        "id", "parameters", "con", "sensor_type", "brand", "model",
        "slave_id", "address", "function", "decode", "is_active",
    )
    list_filter = ("sensor_type", "signal_type", "function", "is_active", "con")
    search_fields = ("brand", "model", "serial_number", "ascii_code")
    list_editable = ("is_active",)
    autocomplete_fields = ("parameters", "con")


@admin.register(SensorInstants)
class SensorInstantsAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "instant", "status", "readtime", "factorA", "factorB", "send_status", "is_random")
    list_filter = ("status", "send_status", "is_random")
    search_fields = ("channel__parameters__parameter_name",)
    readonly_fields = ("readtime",)


@admin.register(Reads)
class ReadsAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "value", "status", "time_iso")
    list_filter = ("status", "channel__con")
    search_fields = ("channel__parameters__parameter_name",)
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    ordering = ("-time_iso",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("channel__parameters", "status")


@admin.register(Poweroff)
class PoweroffAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "start_date", "end_date", "time_iso")
    list_filter = ("station",)
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    autocomplete_fields = ("station",)
    ordering = ("-time_iso",)


@admin.register(Calibration)
class CalibrationAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "type", "period", "cal_ref", "cal_average", "cal_std", "is_valid", "user", "time_iso")
    list_filter = ("type", "is_valid")
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    autocomplete_fields = ("user",)
    ordering = ("-time_iso",)


@admin.register(Log_Types)
class LogTypesAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(Sys_Log)
class SysLogAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "type", "short_description", "time_iso")
    list_filter = ("type", "station")
    search_fields = ("description",)
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    autocomplete_fields = ("station",)
    ordering = ("-time_iso",)

    @admin.display(description="Açıklama")
    def short_description(self, obj):
        if not obj.description:
            return "—"
        return obj.description if len(obj.description) <= 80 else obj.description[:77] + "…"


@admin.register(Api_Log)
class ApiLogAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "url", "status", "time_iso")
    list_filter = ("type", "status")
    search_fields = ("url", "token", "data", "response")
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    ordering = ("-time_iso",)


@admin.register(Out_Requests)
class OutRequestsAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "value", "alarm_level", "is_completed", "request_code", "created_at")
    list_filter = ("alarm_level", "is_completed")
    search_fields = ("request_code",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)
    autocomplete_fields = ("sensor",)
    list_editable = ("is_completed",)
    ordering = ("-created_at",)
