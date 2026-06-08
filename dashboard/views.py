"""Dashboard view'ları — auth, home, raporlar, operatör, yönetim, admin, ayarlar.

Taslak mod: Çoğu rapor/yönetim sayfası `TemplateView` ile şablon render'lar;
içerik (filter form'ları, tablo verileri) adım adım eklenecek.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, FormView, ListView, TemplateView, UpdateView

from api.models import (
    ApiLog,
    Calibration,
    Command,
    Connection,
    Parameter,
    PowerOff,
    Reading,
    ReadingDaily,
    ReadingFifteenMin,
    ReadingHourly,
    Sensor,
    Station,
    StatusCode,
    SystemLog,
)

from .forms import (
    AdminUserCreateForm,
    AdminUserUpdateForm,
    ChangePasswordForm,
    DashboardLoginForm,
    ProfileForm,
)
from .permissions import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_USER,
    AdminRequiredMixin,
    OperatorRequiredMixin,
    RoleRequiredMixin,
)


User = get_user_model()


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

class DashboardLoginView(LoginView):
    template_name = "dashboard/auth/login.html"
    authentication_form = DashboardLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        remember = form.cleaned_data.get("remember_me")
        if remember:
            # 30 gün — "beni hatırla"
            self.request.session.set_expiry(60 * 60 * 24 * 30)
        else:
            # Default 24 saat (settings.SESSION_COOKIE_AGE)
            self.request.session.set_expiry(None)
        return response


class DashboardLogoutView(LogoutView):
    next_page = reverse_lazy("dashboard:login")


class ForgotPasswordView(TemplateView):
    """E-posta yapılandırması yok — sadece info sayfası."""
    template_name = "dashboard/auth/forgot_password.html"


# --------------------------------------------------------------------------- #
# Home — 4 widget'lı ana sayfa
# --------------------------------------------------------------------------- #

class HomeView(RoleRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"


# --------------------------------------------------------------------------- #
# Reports (rol: herkes, bir kısmı sadece admin+operatör)
# --------------------------------------------------------------------------- #

class ReadingsReportView(RoleRequiredMixin, ListView):
    """Sensör Okumaları — istasyon + parametre + tip + tarih + aralık + status filtreli.

    Veri aralığına (`interval`) göre kaynak tablo değişir:
      - ``1min`` → raw `Reading` (1 dk poll varsayımı)
      - ``15min`` → `ReadingFifteenMin`
      - ``hourly`` → `ReadingHourly`
      - ``daily`` → `ReadingDaily`
    Status code filtresi sadece raw için anlamlı; aggregate tablolarında atlanır.
    DataTables client-side handle ettiği için server-side pagination yok;
    sertlik (DoS koruması) açısından hard limit `MAX_ROWS` ile sağlanır.
    """
    template_name = "dashboard/reports/sensor_readings.html"
    context_object_name = "rows"
    # DataTables client-side; server-side pagination KAPALI.
    paginate_by = None
    # Browser'ı bombalamamak için tek sorguda dönecek max satır.
    MAX_ROWS = 50000

    INTERVAL_CHOICES = (
        ("1min", _("1 dk.")),
        ("15min", _("15 dk.")),
        ("hourly", _("1 saat")),
        ("daily", _("1 gün")),
    )
    DATE_FORMAT = "%d.%m.%Y %H:%M:%S"

    def _parse_dt(self, raw: str):
        if not raw:
            return None
        try:
            naive = datetime.strptime(raw.strip(), self.DATE_FORMAT)
        except ValueError:
            return None
        if timezone.is_naive(naive):
            return timezone.make_aware(naive, timezone.get_current_timezone())
        return naive

    def _filters(self):
        """Submit edilmiş GET parametrelerini normalize edip döner."""
        gp = self.request.GET
        now = timezone.now()
        start = self._parse_dt(gp.get("start")) or (now - timedelta(days=1))
        end = self._parse_dt(gp.get("end")) or now
        if end < start:
            start, end = end, start

        try:
            station_id = int(gp.get("station") or 0) or None
        except (TypeError, ValueError):
            station_id = None

        param_ids = []
        for raw in gp.getlist("parameter"):
            try:
                param_ids.append(int(raw))
            except (TypeError, ValueError):
                continue

        status_ids = []
        for raw in gp.getlist("status"):
            try:
                status_ids.append(int(raw))
            except (TypeError, ValueError):
                continue

        interval = gp.get("interval") or "1min"
        if interval not in dict(self.INTERVAL_CHOICES):
            interval = "1min"

        return {
            "submitted": bool(gp),
            "station_id": station_id,
            "parameter_ids": param_ids,
            "status_ids": status_ids,
            "interval": interval,
            "start": start,
            "end": end,
            "chart": gp.get("chart") == "1",
        }

    def get_queryset(self):
        f = self._filters()
        if not f["submitted"] or not f["station_id"]:
            # Filtre gönderilmeden boş tablo göster — kullanıcı "Rapor Getir"e bassın.
            return Reading.objects.none()

        is_raw = f["interval"] == "1min"
        model = {
            "1min": Reading,
            "15min": ReadingFifteenMin,
            "hourly": ReadingHourly,
            "daily": ReadingDaily,
        }[f["interval"]]

        time_field = "time_iso" if is_raw else "bucket_start"
        select_related = ("sensor", "sensor__parameter")
        if is_raw:
            select_related = select_related + ("status",)
        qs = (model.objects
              .select_related(*select_related)
              .filter(**{f"{time_field}__gte": f["start"], f"{time_field}__lte": f["end"],
                         "sensor__connection__station_id": f["station_id"]}))

        if f["parameter_ids"]:
            qs = qs.filter(sensor__parameter_id__in=f["parameter_ids"])

        if is_raw and f["status_ids"]:
            qs = qs.filter(status_id__in=f["status_ids"])

        return qs.order_by(f"-{time_field}", "sensor_id")[: self.MAX_ROWS]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        f = self._filters()
        ctx["filters"] = f
        ctx["is_raw"] = f["interval"] == "1min"
        ctx["interval_choices"] = self.INTERVAL_CHOICES
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["status_codes"] = StatusCode.objects.order_by("code")

        # Parametre listesini sensör tipine göre Analog / Dijital olarak grupla.
        # ÖNEMLİ: Parameter.station FK'ı her zaman dolu olmayabiliyor veya
        # sensörsüz parametre kayıtları kalabiliyor. Doğru kapsam: o istasyonun
        # connection'larına bağlı sensörlerin parametreleri.
        if f["station_id"]:
            param_qs = (
                Parameter.objects
                .filter(sensors__connection__station_id=f["station_id"])
                .distinct()
                .order_by("parameter_name")
                .prefetch_related("sensors")
            )
        else:
            param_qs = Parameter.objects.none()

        analog, digital, other = [], [], []
        for p in param_qs:
            sensors = list(p.sensors.all())
            stype = sensors[0].sensor_type if sensors else None
            if stype in (0, 1):
                analog.append(p)
            elif stype in (2, 3):
                digital.append(p)
            else:
                other.append(p)
        ctx["parameters_analog"] = analog
        ctx["parameters_digital"] = digital
        ctx["parameters_other"] = other
        return ctx


class AggregatesReportView(RoleRequiredMixin, TemplateView):
    """15dk/saatlik/günlük bucket kıyaslama — chart + tablo (taslak)."""
    template_name = "dashboard/reports/aggregates.html"


class CalibrationsReportView(RoleRequiredMixin, ListView):
    model = Calibration
    template_name = "dashboard/reports/calibrations.html"
    context_object_name = "calibrations"
    paginate_by = 50
    ordering = ["-time_iso"]

    def get_queryset(self):
        return super().get_queryset().select_related("sensor", "sensor__parameter", "user")


class PowerOffsReportView(RoleRequiredMixin, ListView):
    model = PowerOff
    template_name = "dashboard/reports/power_offs.html"
    context_object_name = "power_offs"
    paginate_by = 50
    ordering = ["-time_iso"]

    def get_queryset(self):
        return super().get_queryset().select_related("station")


class CommandsReportView(OperatorRequiredMixin, ListView):
    model = Command
    template_name = "dashboard/reports/commands.html"
    context_object_name = "commands"
    paginate_by = 50
    ordering = ["-created_at"]

    def get_queryset(self):
        return super().get_queryset().select_related("sensor", "sensor__parameter", "request_type", "requested_by")


class SystemLogsReportView(OperatorRequiredMixin, ListView):
    model = SystemLog
    template_name = "dashboard/reports/system_logs.html"
    context_object_name = "logs"
    paginate_by = 50
    ordering = ["-time_iso"]

    def get_queryset(self):
        return super().get_queryset().select_related("type", "station")


# --------------------------------------------------------------------------- #
# Operator pages
# --------------------------------------------------------------------------- #

class SampleTriggerView(OperatorRequiredMixin, TemplateView):
    template_name = "dashboard/operator/sample_trigger.html"


class AlarmsView(OperatorRequiredMixin, TemplateView):
    template_name = "dashboard/operator/alarms.html"


# --------------------------------------------------------------------------- #
# Management overview (readonly)
# --------------------------------------------------------------------------- #

class StationsOverviewView(OperatorRequiredMixin, ListView):
    model = Station
    template_name = "dashboard/management/stations.html"
    context_object_name = "stations"
    paginate_by = 50


class ConnectionsOverviewView(OperatorRequiredMixin, ListView):
    model = Connection
    template_name = "dashboard/management/connections.html"
    context_object_name = "connections"
    paginate_by = 50


class SensorsOverviewView(RoleRequiredMixin, ListView):
    model = Sensor
    template_name = "dashboard/management/sensors.html"
    context_object_name = "sensors"
    paginate_by = 50

    def get_queryset(self):
        return super().get_queryset().select_related("parameter", "connection", "scan_group")


# --------------------------------------------------------------------------- #
# Admin pages (rol=1 only)
# --------------------------------------------------------------------------- #

class UserListView(AdminRequiredMixin, ListView):
    model = User
    template_name = "dashboard/admin_pages/user_list.html"
    context_object_name = "users"
    paginate_by = 50


class UserCreateView(AdminRequiredMixin, CreateView):
    model = User
    form_class = AdminUserCreateForm
    template_name = "dashboard/admin_pages/user_form.html"
    success_url = reverse_lazy("dashboard:admin_users")

    def form_valid(self, form):
        messages.success(self.request, _("Kullanıcı oluşturuldu."))
        return super().form_valid(form)


class UserUpdateView(AdminRequiredMixin, UpdateView):
    model = User
    form_class = AdminUserUpdateForm
    template_name = "dashboard/admin_pages/user_form.html"
    success_url = reverse_lazy("dashboard:admin_users")

    def form_valid(self, form):
        messages.success(self.request, _("Kullanıcı güncellendi."))
        return super().form_valid(form)


class UserResetPasswordView(AdminRequiredMixin, TemplateView):
    """Admin, user'a rastgele geçici şifre atar — UI'da gösterilir."""
    template_name = "dashboard/admin_pages/user_reset_password.html"

    def post(self, request, pk):
        target = User.objects.get(pk=pk)
        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(12))
        target.set_password(new_password)
        target.save()
        messages.success(
            request,
            _(
                "Yeni geçici şifre: %(pw)s — kullanıcıya elden iletin; "
                "ilk girişte değiştirmesi önerilir."
            ) % {"pw": new_password},
        )
        return redirect("dashboard:admin_user_edit", pk=target.pk)


class ApiLogsView(AdminRequiredMixin, ListView):
    model = ApiLog
    template_name = "dashboard/admin_pages/api_logs.html"
    context_object_name = "logs"
    paginate_by = 50
    ordering = ["-created_at"]


class SystemControlView(AdminRequiredMixin, TemplateView):
    """Yönetici → Sistem Kontrol.

    Üç global aç/kapa (SIM, Envisoft, Polling) + manuel/haftalık yıkama
    tetikleme + Celery durum widget'ı. POST handler `action` parametresine
    göre 4 farklı işlem yapar: save_switches, start_manual_wash,
    start_weekly_wash, stop_wash.
    """
    template_name = "dashboard/admin_pages/system_control.html"

    # Yıkama süre clamp'i — UI input max'iyle senkron.
    MANUAL_WASH_MAX_MIN = 60
    WEEKLY_WASH_MAX_MIN = 180

    def get_context_data(self, **kwargs):
        from sais_domain.models import SystemSwitch
        ctx = super().get_context_data(**kwargs)
        switch = SystemSwitch.load()
        ctx["switch"] = switch
        ctx["celery_status"] = _celery_status()
        ctx["wash_remaining_seconds"] = switch.wash_remaining_seconds()
        ctx["wash_active_status_code"] = switch.active_wash_status_code()
        return ctx

    def post(self, request, *args, **kwargs):
        from sais_domain.models import SystemSwitch
        switch = SystemSwitch.load()
        action = request.POST.get("action", "save_switches")

        if action == "start_manual_wash":
            minutes = self._parse_minutes(
                request.POST.get("manual_wash_minutes"),
                default=switch.manual_wash_duration_minutes,
                lo=1, hi=self.MANUAL_WASH_MAX_MIN,
            )
            self._start_wash(switch, request.user, kind="manual", minutes=minutes)
            switch.manual_wash_duration_minutes = minutes
            switch.save()
            messages.success(request,
                _("Manuel yıkama başlatıldı: %(m)d dk.") % {"m": minutes})

        elif action == "start_weekly_wash":
            minutes = self._parse_minutes(
                request.POST.get("weekly_wash_minutes"),
                default=switch.weekly_wash_duration_minutes,
                lo=1, hi=self.WEEKLY_WASH_MAX_MIN,
            )
            self._start_wash(switch, request.user, kind="weekly", minutes=minutes)
            switch.weekly_wash_duration_minutes = minutes
            switch.save()
            messages.success(request,
                _("Haftalık yıkama başlatıldı: %(m)d dk.") % {"m": minutes})

        elif action == "stop_wash":
            switch.wash_active_kind = None
            switch.wash_started_at = None
            switch.wash_ends_at = None
            switch.wash_started_by = None
            switch.updated_by = request.user
            switch.save()
            messages.success(request, _("Yıkama durduruldu."))

        else:  # save_switches — mevcut davranış
            switch.sim_enabled = request.POST.get("sim_enabled") == "on"
            switch.envisoft_enabled = request.POST.get("envisoft_enabled") == "on"
            switch.polling_enabled = request.POST.get("polling_enabled") == "on"
            switch.updated_by = request.user
            switch.save()
            messages.success(request, _("Sistem kontrol ayarları kaydedildi."))

        return redirect("dashboard:admin_system_control")

    @staticmethod
    def _parse_minutes(raw, *, default: int, lo: int, hi: int) -> int:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    @staticmethod
    def _start_wash(switch, user, *, kind: str, minutes: int) -> None:
        from datetime import timedelta
        now = timezone.now()
        switch.wash_active_kind = kind
        switch.wash_started_at = now
        switch.wash_ends_at = now + timedelta(minutes=minutes)
        switch.wash_started_by = user
        switch.updated_by = user


def _celery_status():
    """Celery worker + beat durumunu özetler.

    Worker: broker üzerinden 1 sn timeout ile `inspect.ping()`. Yanıt veren
    worker varsa OK + sayıyı döner.
    Beat: en yeni aktif PeriodicTask'ın `last_run_at`'ı son 2 dk içindeyse
    beat'in canlı çalıştığı kabul edilir.
    """
    from sais_web.celery import app
    from django_celery_beat.models import PeriodicTask

    worker_count = 0
    worker_ok = False
    try:
        replies = app.control.inspect(timeout=1).ping() or {}
        worker_count = len(replies)
        worker_ok = worker_count > 0
    except Exception:  # noqa: BLE001 — broker erişilemez veya inspect timeout
        worker_ok = False

    beat_ok = False
    beat_last_run = None
    latest = (
        PeriodicTask.objects.filter(enabled=True)
        .exclude(last_run_at__isnull=True)
        .order_by("-last_run_at")
        .first()
    )
    if latest and latest.last_run_at:
        beat_last_run = latest.last_run_at
        beat_ok = (timezone.now() - latest.last_run_at).total_seconds() < 120

    return {
        "worker_ok": worker_ok,
        "worker_count": worker_count,
        "beat_ok": beat_ok,
        "beat_last_run": beat_last_run,
    }


# --------------------------------------------------------------------------- #
# Yönetici: Veritabanı Yedekleme / Geri Yükleme
# --------------------------------------------------------------------------- #

class BackupRestoreView(AdminRequiredMixin, TemplateView):
    """Yönetici → Yedekleme.

    Tier (günlük/haftalık/aylık/yıllık/manuel) politikalarını yönetir, manuel
    yedek tetikler, geçmiş yedekleri listeler/indirir ve sürüm-bilinçli geri
    yükleme yapar. POST `action`: save_policies / run_backup / run_restore.
    """
    template_name = "dashboard/admin_pages/backups.html"
    BACKUP_LIST_LIMIT = 100
    RESTORE_LIST_LIMIT = 30

    def get_context_data(self, **kwargs):
        import json
        import os

        from django.conf import settings

        from api.db_admin import compare_schema, database_name
        from api.models import BackupPolicy, DatabaseBackup, DatabaseRestore

        ctx = super().get_context_data(**kwargs)
        BackupPolicy.ensure_defaults()

        # Uyumluluk verdict'i pahalı (MigrationLoader) — aynı migration_state için cache'le.
        verdict_cache: dict[str, dict] = {}

        def verdict_for(state):
            key = json.dumps(state or {}, sort_keys=True)
            if key not in verdict_cache:
                verdict_cache[key] = compare_schema(state)
            return verdict_cache[key]

        backups = list(
            DatabaseBackup.objects.all()[: self.BACKUP_LIST_LIMIT]
        )
        for b in backups:
            v = verdict_for(b.migration_state)
            b.verdict = v["verdict"]
            b.verdict_message = v["message"]
            b.can_restore = (b.status == "success" and not b.pruned)
            b.can_download = (b.status == "success" and not b.pruned)

        ctx["policies"] = BackupPolicy.objects.all()
        ctx["backups"] = backups
        ctx["restores"] = list(
            DatabaseRestore.objects.all()[: self.RESTORE_LIST_LIMIT]
        )
        ctx["running"] = (
            DatabaseBackup.objects.filter(status="running").exists()
            or DatabaseRestore.objects.filter(status="running").exists()
        )
        ctx["backup_dir"] = settings.BACKUP_DIR
        ctx["backup_dir_ok"] = os.path.isdir(settings.BACKUP_DIR)
        ctx["db_name"] = database_name()
        return ctx

    def post(self, request, *args, **kwargs):
        from api.db_admin import compare_schema
        from api.models import BackupPolicy, DatabaseBackup
        from api.tasks import backup_database_run, restore_database_run

        action = request.POST.get("action", "")

        if action == "save_policies":
            BackupPolicy.ensure_defaults()
            for policy in BackupPolicy.objects.all():
                policy.enabled = request.POST.get(f"enabled_{policy.tier}") == "on"
                try:
                    retention = int(request.POST.get(f"retention_{policy.tier}", policy.retention))
                    policy.retention = max(1, min(retention, 9999))
                except (TypeError, ValueError):
                    pass
                policy.save()
            messages.success(request, _("Yedekleme politikaları kaydedildi."))

        elif action == "run_backup":
            tier = request.POST.get("tier", "manual")
            valid = {t for t, _label in BackupPolicy._meta.get_field("tier").choices}
            if tier not in valid:
                tier = "manual"
            backup_database_run.delay(tier=tier, force=True, user_id=request.user.id)
            messages.success(request, _("Yedekleme başlatıldı; birkaç saniye içinde listede görünür."))

        elif action == "run_restore":
            backup_id = request.POST.get("backup_id")
            run_migrate = request.POST.get("run_migrate") == "on"
            backup = DatabaseBackup.objects.filter(pk=backup_id).first()
            if not backup or backup.status != "success" or backup.pruned:
                messages.error(request, _("Geri yüklenecek geçerli yedek bulunamadı."))
            else:
                verdict = compare_schema(backup.migration_state)
                if verdict["verdict"] == "block":
                    messages.error(request, verdict["message"])
                else:
                    restore_database_run.delay(
                        backup_id=backup.id,
                        run_migrate=run_migrate,
                        user_id=request.user.id,
                    )
                    messages.warning(request, _(
                        "Geri yükleme başlatıldı. Sistem kısa süre kesintiye uğrayabilir; "
                        "işlem bitince sayfayı yenileyin."
                    ))
        else:
            messages.error(request, _("Geçersiz işlem."))

        return redirect("dashboard:admin_backups")


# --------------------------------------------------------------------------- #
# Settings: profile, change password, preferences
# --------------------------------------------------------------------------- #

class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "dashboard/settings/profile.html"
    success_url = reverse_lazy("dashboard:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, _("Profil güncellendi."))
        return super().form_valid(form)


class ChangePasswordView(LoginRequiredMixin, FormView):
    form_class = ChangePasswordForm
    template_name = "dashboard/settings/change_password.html"
    success_url = reverse_lazy("dashboard:profile")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        update_session_auth_hash(self.request, form.user)   # login session'ı kaybetmesin
        messages.success(self.request, _("Şifreniz güncellendi."))
        return super().form_valid(form)


class PreferencesView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/settings/preferences.html"
