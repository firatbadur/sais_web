import json

from django import forms
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    AlarmRule,
    ApiLog,
    Calibration,
    Command,
    Connection,
    LogType,
    MessageTemplate,
    NotificationLog,
    NotificationSettings,
    Parameter,
    PowerOff,
    Reading,
    ReadingDaily,
    ReadingFifteenMin,
    ReadingHourly,
    Reminder,
    RequestType,
    ScanGroup,
    Sensor,
    SensorLatest,
    Station,
    StationAuthority,
    StationType,
    StatusCode,
    SystemLog,
)


@admin.register(StationType)
class StationTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "description")
    search_fields = ("code", "name")
    ordering = ("code",)


class StationAuthorityInline(admin.TabularInline):
    model = StationAuthority
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "station_type", "company", "domain", "port", "active", "user", "created_at")
    list_filter = ("station_type", "active", "company")
    search_fields = ("name", "address", "company", "domain")
    list_editable = ("active",)
    readonly_fields = ("created_at",)
    autocomplete_fields = ("station_type", "user")
    inlines = (StationAuthorityInline,)
    ordering = ("-created_at",)


@admin.register(StationAuthority)
class StationAuthorityAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "user", "notify", "created_at")
    list_filter = ("notify", "station")
    search_fields = ("station__name", "user__username")
    autocomplete_fields = ("station", "user")
    readonly_fields = ("created_at",)


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "station", "name", "protocol", "transport",
        "host", "port", "serial_port", "baudrate",
        "poll_interval_sec", "save_interval_sec",
        "is_enabled", "last_polled_at", "last_connected_at", "created_date",
    )
    list_filter = ("station", "protocol", "transport", "is_enabled")
    search_fields = ("name", "description", "host", "serial_port")
    list_editable = ("is_enabled",)
    readonly_fields = ("created_date", "last_polled_at", "last_connected_at",
                       "last_error_at", "last_error_message")
    autocomplete_fields = ("station",)
    ordering = ("station", "name")
    fieldsets = (
        ("Kimlik", {
            "fields": ("station", "name", "description", "is_enabled"),
        }),
        ("Protokol", {
            "fields": ("protocol", "transport"),
        }),
        ("Network (TCP)", {
            "fields": ("host", "port"),
        }),
        ("Serial", {
            "classes": ("collapse",),
            "fields": (
                "serial_port", "baudrate", "parity", "stop_bits", "byte_size",
                "xonxoff", "rtscts", "dsrdtr",
            ),
        }),
        ("Polling / Kayıt / Güvenilirlik", {
            "fields": (
                "poll_interval_sec", "save_interval_sec",
                "timeout_ms", "retry_count",
                "auto_reconnect", "reconnect_delay_sec",
            ),
        }),
        ("Runtime Durumu", {
            "classes": ("collapse",),
            "fields": ("last_polled_at", "last_connected_at", "last_error_at", "last_error_message"),
        }),
        ("Meta", {
            "classes": ("collapse",),
            "fields": ("created_date",),
        }),
    )


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


class _SensorAdminForm(forms.ModelForm):
    """Hem inline (ScanGroup içi) hem ana Sensor admin formu için ortak form.

    `sensor_type`'ı zorunlu yapar (model `default=0, blank=True` olduğu için
    Django default form'da required=False çıkıyordu). Yönetici tipi açıkça
    seçmek zorunda — boş seçenek "— Sensör tipi seçin —" olarak gösterilir
    ve gönderilirse validation hatası verir.
    """
    sensor_type = forms.TypedChoiceField(
        choices=[("", "— Sensör tipi seçin —")] + list(Sensor.SENSOR_TYPE),
        coerce=int,
        required=True,
        label="Sensör Tipi",
    )

    class Meta:
        model = Sensor
        fields = "__all__"


class _ScanGroupSensorInline(admin.TabularInline):
    """ScanGroup detay sayfasında o grubun sensörlerini inline göster."""
    model = Sensor
    form = _SensorAdminForm
    fk_name = "scan_group"
    extra = 0
    fields = (
        "parameter", "sensor_type", "address", "data_type",
        "byte_order", "word_order", "bit_position",
        "scale", "offset", "decimals", "is_active",
    )
    autocomplete_fields = ("parameter",)
    show_change_link = True


@admin.register(ScanGroup)
class ScanGroupAdmin(admin.ModelAdmin):
    list_display = (
        "id", "connection", "name", "slave_id", "function",
        "start_address", "quantity", "end_address_display",
        "sensor_count", "is_active",
    )
    list_filter = ("connection", "function", "is_active")
    search_fields = ("name", "connection__name")
    list_editable = ("is_active",)
    autocomplete_fields = ("connection",)
    ordering = ("connection", "slave_id", "start_address")
    inlines = [_ScanGroupSensorInline]

    @admin.display(description="Bitiş adresi", ordering="start_address")
    def end_address_display(self, obj):
        return obj.end_address

    @admin.display(description="Sensör sayısı")
    def sensor_count(self, obj):
        return obj.sensors.count()


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    form = _SensorAdminForm
    list_display = (
        "id", "parameter", "connection", "scan_group", "sensor_type", "brand", "model",
        "slave_id", "address", "function", "data_type", "scale", "offset", "decimals",
        "is_active", "dashboard_hidden", "is_simulated", "report_status",
    )
    list_filter = ("sensor_type", "signal_type", "function", "data_type", "is_active",
                   "dashboard_hidden", "is_simulated", "report_status", "connection",
                   "scan_group")
    search_fields = ("brand", "model", "serial_number", "ascii_code", "ascii_request")
    list_editable = ("is_active", "dashboard_hidden")
    autocomplete_fields = ("parameter", "connection", "scan_group")
    fieldsets = (
        ("Kimlik", {
            "fields": ("parameter", "brand", "model", "serial_number", "sensor_type",
                       "signal_type", "is_active", "dashboard_hidden", "is_simulated",
                       "sim_min", "sim_max", "report_status"),
        }),
        ("Bağlantı", {
            "fields": ("connection", "scan_group"),
        }),
        ("Modbus", {
            "fields": (
                "slave_id", "function", "address", "quantity",
                "byte_order", "word_order", "bit_position",
            ),
            "description": (
                "Bir scan_group seçildiyse slave_id ve function o gruba uymalı; "
                "address grubun aralığı içinde olmalı. Grubun dışındaki sensörler "
                "(scan_group=None) her polling'de tek tek okunur (legacy)."
            ),
        }),
        ("Veri Tipi & Ölçekleme", {
            "fields": ("data_type", "scale", "offset", "decimals", "digital_inverse"),
        }),
        ("ASCII Protokolü", {
            "classes": ("collapse",),
            "fields": (
                "ascii_code", "ascii_request", "ascii_response_regex",
                "ascii_line_terminator",
            ),
        }),
        ("Polling / Zamanlama", {
            "classes": ("collapse",),
            "fields": ("poll_interval_sec", "timeout_ms", "retry_count"),
        }),
        ("Kayıt / Değişimde Kaydet (COV)", {
            "classes": ("collapse",),
            "fields": ("save_on_change", "deadband", "cov_heartbeat_sec"),
            "description": (
                "save_on_change açıkken Reading sadece değer deadband'i aşacak "
                "kadar değiştiğinde, status değiştiğinde veya heartbeat (sn) "
                "dolduğunda yazılır. Connection.save_interval_sec yine minimum "
                "aralık olarak uygulanır. Snapshot (HMI) her okumada güncellenir."
            ),
        }),
    )


@admin.register(SensorLatest)
class SensorLatestAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "value", "status", "quality", "readtime",
                    "last_change_at", "last_saved_at", "last_saved_value", "update_count")
    list_filter = ("status", "quality")
    search_fields = ("sensor__parameter__parameter_name",)
    readonly_fields = ("readtime", "last_change_at", "last_saved_at",
                       "last_saved_value", "last_saved_status", "update_count")
    autocomplete_fields = ("sensor",)


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "value", "status", "quality", "origin", "time_iso")
    list_filter = ("status", "quality", "origin", "sensor__connection")
    search_fields = ("sensor__parameter__parameter_name",)
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    ordering = ("-time_iso",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sensor__parameter", "status")


class _ReadingAggregateAdminMixin(admin.ModelAdmin):
    """Üç aggregate admin'i için ortak ayar."""
    list_display = ("id", "sensor", "bucket_start", "avg_value", "min_value",
                    "max_value", "count", "bad_count", "computed_at")
    list_filter = ("sensor__connection",)
    search_fields = ("sensor__parameter__parameter_name",)
    date_hierarchy = "bucket_start"
    readonly_fields = ("computed_at",)
    autocomplete_fields = ("sensor",)
    ordering = ("-bucket_start",)

    def has_add_permission(self, request):
        return False  # aggregate'ler komutla doldurulur


@admin.register(ReadingFifteenMin)
class ReadingFifteenMinAdmin(_ReadingAggregateAdminMixin):
    pass


@admin.register(ReadingHourly)
class ReadingHourlyAdmin(_ReadingAggregateAdminMixin):
    pass


@admin.register(ReadingDaily)
class ReadingDailyAdmin(_ReadingAggregateAdminMixin):
    pass


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
    list_display = ("id", "time_iso", "severity", "type", "user_label", "ip_address",
                    "station", "short_description")
    list_filter = ("severity", "type", "station")
    search_fields = ("description", "username", "ip_address")
    date_hierarchy = "time_iso"
    readonly_fields = ("time_iso",)
    autocomplete_fields = ("station", "user")
    ordering = ("-time_iso",)

    @admin.display(description="Kullanıcı")
    def user_label(self, obj):
        if obj.user_id:
            return obj.user.get_username()
        return obj.username or "—"

    @admin.display(description="Açıklama")
    def short_description(self, obj):
        if not obj.description:
            return "—"
        return obj.description if len(obj.description) <= 80 else obj.description[:77] + "…"


class _NotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = NotificationSettings
        fields = "__all__"
        widgets = {
            "smtp_password": forms.PasswordInput(render_value=True),
            "netgsm_password": forms.PasswordInput(render_value=True),
        }


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    form = _NotificationSettingsForm
    list_display = ("__str__", "email_enabled", "sms_enabled", "updated_at", "updated_by")
    readonly_fields = ("updated_at", "updated_by")
    fieldsets = (
        ("E-posta (SMTP)", {
            "fields": ("email_enabled", "smtp_host", "smtp_port", "smtp_use_tls",
                       "smtp_user", "smtp_password", "mail_from", "mail_subject"),
        }),
        ("SMS (NetGSM)", {
            "fields": ("sms_enabled", "netgsm_usercode", "netgsm_password",
                       "netgsm_header", "netgsm_api_url"),
        }),
        ("Audit", {"fields": ("updated_at", "updated_by")}),
    )

    def has_add_permission(self, request):
        return not NotificationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "channel", "category", "created_by", "created_at")
    list_filter = ("channel", "category")
    search_fields = ("title", "body")
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "recipient", "status", "kind", "sent_by", "created_at")
    list_filter = ("channel", "status", "kind")
    search_fields = ("recipient", "message")
    date_hierarchy = "created_at"
    readonly_fields = ("channel", "recipient", "message", "status", "error", "kind",
                       "created_at", "sent_by")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(AlarmRule)
class AlarmRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "rule_type", "type_label", "period_minutes",
                    "enabled", "send_sms", "send_email", "last_triggered_at")
    list_filter = ("rule_type", "enabled", "station", "send_sms", "send_email")
    search_fields = ("message", "station__name")
    list_editable = ("enabled",)
    autocomplete_fields = ("station", "parameter", "sensor", "created_by")
    readonly_fields = ("created_at", "last_triggered_at")
    fieldsets = (
        ("Genel", {"fields": ("station", "rule_type", "period_minutes", "message", "enabled")}),
        ("Ölçüm (Analog)", {"fields": ("parameter", "condition", "min_value", "max_value")}),
        ("Dijital / Offline", {"fields": ("sensor", "trigger_state", "offline_seconds")}),
        ("Bildirim", {"fields": ("notify_all", "send_sms", "send_email")}),
        ("Audit", {"fields": ("created_by", "created_at", "last_triggered_at")}),
    )


@admin.register(ApiLog)
class ApiLogAdmin(admin.ModelAdmin):
    list_display = (
        "id", "created_at", "direction", "method", "url",
        "response_status", "duration_ms", "remote_ip", "target_host", "user",
    )
    list_filter = ("direction", "method", "response_status", "source_component")
    search_fields = (
        "url", "query_string", "remote_ip", "target_host",
        "source_component", "user__username", "error_message",
    )
    date_hierarchy = "created_at"
    autocomplete_fields = ("user",)
    ordering = ("-created_at",)
    # Tüm alanlar middleware/helper tarafından doldurulur; manuel girilmez.
    readonly_fields = (
        "direction", "method", "url", "query_string",
        "request_headers", "request_body",
        "response_status", "response_body", "duration_ms", "error_message",
        "remote_ip", "user", "user_agent",
        "target_host", "source_component", "retry_count",
        "created_at",
    )
    fieldsets = (
        ("Özet", {
            "fields": ("created_at", "direction", "method", "url",
                       "response_status", "duration_ms", "error_message"),
        }),
        ("İstek", {
            "fields": ("query_string", "request_headers", "request_body"),
        }),
        ("Yanıt", {
            "fields": ("response_body",),
        }),
        ("Inbound Meta", {
            "classes": ("collapse",),
            "fields": ("remote_ip", "user", "user_agent"),
        }),
        ("Outbound Meta", {
            "classes": ("collapse",),
            "fields": ("target_host", "source_component", "retry_count"),
        }),
    )

    def has_add_permission(self, request):
        return False  # log kayıtları manuel oluşturulmaz

    def has_change_permission(self, request, obj=None):
        return False  # readonly


@admin.register(RequestType)
class RequestTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(Command)
class CommandAdmin(admin.ModelAdmin):
    list_display = (
        "id", "sensor", "value_type", "value", "value_text",
        "status", "priority", "source", "request_type",
        "attempt_count", "scheduled_at", "expires_at", "created_at",
    )
    list_filter = ("status", "source", "value_type", "request_type", "priority")
    search_fields = ("correlation_id", "idempotency_key", "error_message", "value_text")
    date_hierarchy = "created_at"
    readonly_fields = (
        "created_at", "executed_at", "completed_at", "attempt_count",
        "error_message", "response_data",
    )
    autocomplete_fields = ("sensor", "request_type", "requested_by")
    ordering = ("-priority", "-created_at")
    fieldsets = (
        ("Hedef", {
            "fields": ("sensor",),
        }),
        ("Değer", {
            "fields": ("value_type", "value", "value_text"),
        }),
        ("Durum", {
            "fields": ("status", "priority", "attempt_count", "max_attempts"),
        }),
        ("Yaşam Döngüsü", {
            "fields": ("scheduled_at", "expires_at", "executed_at", "completed_at", "created_at"),
        }),
        ("Denetim", {
            "fields": ("source", "request_type", "requested_by", "correlation_id", "idempotency_key"),
        }),
        ("Sonuç", {
            "classes": ("collapse",),
            "fields": ("error_message", "response_data"),
        }),
    )


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "remind_at", "priority", "station", "is_done", "created_by")
    list_filter = ("is_done", "priority", "station")
    search_fields = ("title", "note")
    ordering = ("-remind_at",)
    date_hierarchy = "remind_at"
    autocomplete_fields = ("station",)
    readonly_fields = ("created_at", "updated_at", "done_at")


# ---------------------------------------------------------------------------
# Celery task sonuçları (django-celery-results.TaskResult)
#
# Varsayılan TaskResultAdmin ham bir tablo gösterir: hangi task'ın çalıştığı,
# ne kadar sürdüğü, başarısızsa neyle patladığı tek bakışta görünmez. Default
# admin'i kaldırıp aşağıdaki zengin sürümü kaydediyoruz. (Task adı/worker/args
# alanlarının dolması için ayrıca settings.CELERY_RESULT_EXTENDED=True gerekir.)
# ---------------------------------------------------------------------------
from django_celery_results.models import GroupResult, TaskResult  # noqa: E402

# Default kayıtları kaldır (zaten import'ta register edilmişler).
for _model in (TaskResult, GroupResult):
    try:
        admin.site.unregister(_model)
    except admin.sites.NotRegistered:
        pass


_STATUS_COLORS = {
    "SUCCESS": "#50cd89",   # yeşil
    "FAILURE": "#f1416c",   # kırmızı
    "STARTED": "#7239ea",   # mor
    "RETRY": "#ffc700",     # sarı
    "REVOKED": "#a1a5b7",   # gri
    "PENDING": "#009ef7",   # mavi
    "RECEIVED": "#009ef7",
}


def _short_task_name(name):
    """`sais_domain.tasks.publish_minute_data` → `publish_minute_data`."""
    if not name:
        return ""
    return name.rsplit(".", 1)[-1]


@admin.register(TaskResult)
class TaskResultAdmin(admin.ModelAdmin):
    """Celery task sonuçları — hangi task çalıştı, sonucu ne, hata varsa ne."""

    date_hierarchy = "date_done"
    list_display = (
        "task_id_short", "task_label", "status_badge", "worker",
        "date_done", "duration_display", "result_summary",
    )
    list_filter = ("status", "task_name", "periodic_task_name", "worker", "date_done")
    search_fields = ("task_id", "task_name", "periodic_task_name", "worker",
                     "result", "traceback", "task_args", "task_kwargs")
    ordering = ("-date_done",)
    list_per_page = 50

    readonly_fields = (
        "task_id", "task_name", "periodic_task_name", "status_badge", "worker",
        "date_created", "date_started", "date_done", "duration_display",
        "task_args", "task_kwargs", "result_pretty", "traceback_pretty",
        "content_type", "content_encoding", "meta",
    )
    fieldsets = (
        ("Özet", {
            "fields": ("task_id", "task_name", "periodic_task_name",
                       "status_badge", "worker"),
        }),
        ("Zamanlama", {
            "fields": ("date_created", "date_started", "date_done", "duration_display"),
        }),
        ("Parametreler", {
            "classes": ("collapse",),
            "fields": ("task_args", "task_kwargs"),
        }),
        ("Sonuç", {
            "fields": ("result_pretty",),
        }),
        ("Hata (Traceback)", {
            "fields": ("traceback_pretty",),
        }),
        ("Meta", {
            "classes": ("collapse",),
            "fields": ("content_type", "content_encoding", "meta"),
        }),
    )

    def has_add_permission(self, request):
        return False  # sonuçlar worker tarafından yazılır

    def has_change_permission(self, request, obj=None):
        return False  # salt görüntüleme

    @admin.display(description="Task ID", ordering="task_id")
    def task_id_short(self, obj):
        return (obj.task_id or "")[:8]

    @admin.display(description="Task", ordering="task_name")
    def task_label(self, obj):
        name = _short_task_name(obj.task_name)
        if obj.periodic_task_name:
            return format_html(
                "{}<br><span style='color:#a1a5b7;font-size:11px'>{}</span>",
                name or "—", obj.periodic_task_name,
            )
        return name or "—"

    @admin.display(description="Durum", ordering="status")
    def status_badge(self, obj):
        color = _STATUS_COLORS.get(obj.status, "#a1a5b7")
        return format_html(
            "<span style='display:inline-block;padding:2px 9px;border-radius:6px;"
            "background:{};color:#fff;font-weight:600;font-size:11px'>{}</span>",
            color, obj.status,
        )

    @admin.display(description="Süre")
    def duration_display(self, obj):
        if obj.date_started and obj.date_done:
            secs = (obj.date_done - obj.date_started).total_seconds()
            if secs < 0:
                return "—"
            if secs < 1:
                return f"{secs * 1000:.0f} ms"
            return f"{secs:.2f} sn"
        return "—"

    @admin.display(description="Sonuç / Hata")
    def result_summary(self, obj):
        if obj.status == "FAILURE":
            exc = self._decode(obj.result)
            if isinstance(exc, dict):
                msg = exc.get("exc_message") or exc.get("exc_type") or ""
                if isinstance(msg, (list, tuple)):
                    msg = ", ".join(str(m) for m in msg)
                text = f"{exc.get('exc_type', 'Error')}: {msg}"
            else:
                text = str(exc)
            text = text.replace("\n", " ")
            short = text if len(text) <= 80 else text[:77] + "…"
            return format_html("<span style='color:#f1416c'>{}</span>", short)
        raw = (obj.result or "").strip()
        if not raw or raw == "null":
            return "—"
        short = raw if len(raw) <= 80 else raw[:77] + "…"
        return short

    @admin.display(description="Sonuç verisi")
    def result_pretty(self, obj):
        return self._pretty_block(obj.result, empty="(boş)")

    @admin.display(description="Traceback")
    def traceback_pretty(self, obj):
        if not obj.traceback:
            return "—"
        return format_html(
            "<pre style='white-space:pre-wrap;max-height:480px;overflow:auto;"
            "background:#1e1e2d;color:#f1416c;padding:12px;border-radius:6px;"
            "font-size:12px;line-height:1.5'>{}</pre>",
            obj.traceback,
        )

    # -- yardımcılar -------------------------------------------------------
    @staticmethod
    def _decode(raw):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    def _pretty_block(self, raw, empty="—"):
        if not raw or (isinstance(raw, str) and raw.strip() in ("", "null")):
            return empty
        decoded = self._decode(raw)
        try:
            text = json.dumps(decoded, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(decoded)
        return format_html(
            "<pre style='white-space:pre-wrap;max-height:480px;overflow:auto;"
            "background:#f5f8fa;padding:12px;border-radius:6px;font-size:12px'>{}</pre>",
            text,
        )


@admin.register(GroupResult)
class GroupResultAdmin(admin.ModelAdmin):
    """Celery group sonuçları — salt görüntüleme."""

    date_hierarchy = "date_done"
    list_display = ("group_id", "date_created", "date_done")
    search_fields = ("group_id",)
    ordering = ("-date_done",)
    readonly_fields = ("group_id", "date_created", "date_done",
                       "content_type", "content_encoding", "result")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
