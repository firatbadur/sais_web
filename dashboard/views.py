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
    """SAIS Parametre Raporu — istasyon + parametre + tarih + aralık + kalite filtreli.

    Veri aralığına (`interval`) göre kaynak tablo değişir:
      - ``1min`` → raw `Reading` (1 dk poll varsayımı)
      - ``15min`` → `ReadingFifteenMin`
      - ``hourly`` → `ReadingHourly`
      - ``daily`` → `ReadingDaily`
    Kalite filtresi sadece raw için anlamlı; aggregate tablolarında atlanır.
    """
    template_name = "dashboard/reports/sensor_readings.html"
    context_object_name = "rows"
    paginate_by = 100

    INTERVAL_CHOICES = (
        ("1min", _("1 dk.")),
        ("15min", _("15 dk.")),
        ("hourly", _("1 saat")),
        ("daily", _("1 gün")),
    )
    QUALITY_CHOICES = (
        ("all", _("Hepsi")),
        ("good", _("Geçerli")),
        ("bad", _("Hatalı")),
        ("uncertain", _("Belirsiz")),
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

        interval = gp.get("interval") or "1min"
        if interval not in dict(self.INTERVAL_CHOICES):
            interval = "1min"

        quality = gp.get("quality") or "all"
        if quality not in dict(self.QUALITY_CHOICES):
            quality = "all"

        return {
            "submitted": bool(gp),
            "station_id": station_id,
            "parameter_ids": param_ids,
            "interval": interval,
            "quality": quality,
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
        qs = (model.objects
              .select_related("sensor", "sensor__parameter")
              .filter(**{f"{time_field}__gte": f["start"], f"{time_field}__lte": f["end"],
                         "sensor__connection__station_id": f["station_id"]}))

        if f["parameter_ids"]:
            qs = qs.filter(sensor__parameter_id__in=f["parameter_ids"])

        if is_raw and f["quality"] != "all":
            qs = qs.filter(quality=f["quality"])

        return qs.order_by(f"-{time_field}", "sensor_id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        f = self._filters()
        ctx["filters"] = f
        ctx["is_raw"] = f["interval"] == "1min"
        ctx["interval_choices"] = self.INTERVAL_CHOICES
        ctx["quality_choices"] = self.QUALITY_CHOICES
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        # Parametre listesi: istasyon seçildiyse o istasyona ait + boş istasyonlu olanlar.
        param_qs = Parameter.objects.order_by("parameter_name")
        if f["station_id"]:
            param_qs = param_qs.filter(station_id=f["station_id"])
        ctx["parameters"] = param_qs
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
