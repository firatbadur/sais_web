from django.contrib import admin

from .models import (
    ApiLog,
    Calibration,
    Connection,
    LogType,
    OutputRequest,
    Parameter,
    PowerOff,
    Reading,
    RemoteDevice,
    RequestType,
    Sensor,
    SensorLatest,
    Station,
    StationType,
    StatusCode,
    SystemLog,
)


@admin.register(StationType)
class StationTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "description")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "station_type", "company", "domain", "port", "active", "user", "created_at")
    list_filter = ("station_type", "active", "company")
    search_fields = ("name", "address", "company", "domain")
    list_editable = ("active",)
    readonly_fields = ("created_at",)
    autocomplete_fields = ("station_type", "sample_request_sensor", "user")
    ordering = ("-created_at",)


@admin.register(RemoteDevice)
class RemoteDeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "device_id", "code", "name", "data_period", "auth_username", "created_at")
    list_filter = ("station", "data_period")
    search_fields = ("device_id", "code", "name", "auth_username", "station__name")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("station",)
    ordering = ("-created_at",)


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "con_name", "communication_type", "con_type", "con_mode",
        "con_address", "port", "baudrate", "status", "created_date",
    )
    list_filter = ("communication_type", "con_type", "con_mode", "status")
    search_fields = ("con_name", "con_address")
    list_editable = ("status",)
    readonly_fields = ("created_date",)


@admin.register(StatusCode)
class StatusCodeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    list_display = (
        "id", "station", "parameter_name", "parameter_txt",
        "device_channel_id", "channel_number", "unit_txt",
        "gec_min", "gec_max", "olcum_min", "olcum_max",
    )
    list_filter = ("station", "unit_txt")
    search_fields = ("parameter_name", "parameter_txt", "device_channel_id")
    autocomplete_fields = ("station",)
    ordering = ("station", "id")


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = (
        "id", "parameter", "connection", "sensor_type", "brand", "model",
        "slave_id", "address", "function", "decode", "is_active",
    )
    list_filter = ("sensor_type", "signal_type", "function", "is_active", "connection")
    search_fields = ("brand", "model", "serial_number", "ascii_code")
    list_editable = ("is_active",)
    autocomplete_fields = ("parameter", "connection")


@admin.register(SensorLatest)
class SensorLatestAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "instant", "status", "readtime", "factorA", "factorB", "send_status", "is_random")
    list_filter = ("status", "send_status", "is_random")
    search_fields = ("sensor__parameter__parameter_name",)
    readonly_fields = ("readtime",)


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "value", "status", "time_iso")
    list_filter = ("status", "sensor__connection")
    search_fields = ("sensor__parameter__parameter_name",)
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    ordering = ("-time_iso",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sensor__parameter", "status")


@admin.register(PowerOff)
class PowerOffAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "start_date", "end_date", "time_iso")
    list_filter = ("station",)
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    autocomplete_fields = ("station",)
    ordering = ("-time_iso",)


@admin.register(Calibration)
class CalibrationAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "type", "period", "cal_ref", "cal_average", "cal_std", "is_valid", "user", "time_iso")
    list_filter = ("type", "is_valid")
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    autocomplete_fields = ("user",)
    ordering = ("-time_iso",)


@admin.register(LogType)
class LogTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
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


@admin.register(ApiLog)
class ApiLogAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "url", "status", "time_iso")
    list_filter = ("type", "status")
    search_fields = ("url", "token", "data", "response")
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    ordering = ("-time_iso",)


@admin.register(RequestType)
class RequestTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(OutputRequest)
class OutputRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "value", "request_type", "is_completed", "request_code", "created_at")
    list_filter = ("request_type", "is_completed")
    search_fields = ("request_code",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)
    autocomplete_fields = ("sensor", "request_type")
    list_editable = ("is_completed",)
    ordering = ("-created_at",)
