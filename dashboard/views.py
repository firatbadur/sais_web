"""Dashboard view'ları — auth, home, raporlar, operatör, yönetim, admin, ayarlar.

Taslak mod: Çoğu rapor/yönetim sayfası `TemplateView` ile şablon render'lar;
içerik (filter form'ları, tablo verileri) adım adım eklenecek.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from api.events import EventType, log_event
from api.models import (
    ApiLog,
    Calibration,
    Command,
    Connection,
    NotificationLog,
    Parameter,
    PowerOff,
    Reading,
    ReadingDaily,
    ReadingFifteenMin,
    ReadingHourly,
    ScanGroup,
    Sensor,
    SetupState,
    Station,
    StationType,
    StatusCode,
    SystemLog,
)

from .forms import (
    AdminUserCreateForm,
    AdminUserUpdateForm,
    CertUploadForm,
    ChangePasswordForm,
    DashboardLoginForm,
    DocumentUploadForm,
    NotificationSettingsForm,
    PreferencesForm,
    ProfileForm,
    SensorConfigForm,
    WebSettingsForm,
)
from .models import Document
from .permissions import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_USER,
    AdminRequiredMixin,
    OperatorRequiredMixin,
    RoleRequiredMixin,
    can_manage_target_user,
    can_view_admin_events,
    user_has_role,
)


User = get_user_model()


def default_station_id():
    """Rapor/form sayfalarında ön seçili gelecek varsayılan istasyon.

    Önce pk=1 (seed'deki varsayılan istasyon) aktifse onu; yoksa ilk aktif
    istasyonu; hiç yoksa None döner.
    """
    if Station.objects.filter(pk=1, active=True).exists():
        return 1
    s = Station.objects.filter(active=True).order_by("id").first()
    return s.id if s else None


REPORT_DATE_FORMAT = "%d.%m.%Y %H:%M:%S"


def parse_report_dt(raw):
    """Rapor filtre formundaki 'gg.aa.yyyy SS:DD:SS' metnini TZ-aware datetime'a çevirir."""
    if not raw:
        return None
    try:
        return timezone.make_aware(datetime.strptime(raw.strip(), REPORT_DATE_FORMAT))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

class DashboardLoginView(LoginView):
    template_name = "dashboard/auth/login.html"
    authentication_form = DashboardLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        # Oturum süresi kullanıcının kalıcı tercihinden (varsayılan 8 saat).
        # 0 = tarayıcı kapanınca; diğerleri dakika cinsinden.
        minutes = getattr(self.request.user, "session_timeout_minutes", 480)
        self.request.session.set_expiry(0 if minutes == 0 else minutes * 60)
        return response


class DashboardLogoutView(LogoutView):
    next_page = reverse_lazy("dashboard:login")


class ForgotPasswordView(TemplateView):
    """E-posta yapılandırması yok — sadece info sayfası.

    Yönetici e-posta(ları) hardcode değil; aktif rol=1 (Sistem Yöneticisi)
    kullanıcılarının e-postalarından dinamik gösterilir.
    """
    template_name = "dashboard/auth/forgot_password.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        emails = list(
            User.objects.filter(rol=ROLE_ADMIN, is_active=True)
            .exclude(email="")
            .order_by("email")
            .values_list("email", flat=True)
            .distinct()
        )
        ctx["admin_emails"] = emails
        return ctx


# --------------------------------------------------------------------------- #
# Home — 4 widget'lı ana sayfa
# --------------------------------------------------------------------------- #

class HomeView(RoleRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"

    def dispatch(self, request, *args, **kwargs):
        # İlk kurulumda (sihirbaz hiç tamamlanmadıysa) yöneticiyi otomatik
        # olarak kurulum sihirbazına yönlendir. Operatör/kullanıcı yönlendirilmez
        # (yapılandırma yetkileri yok) — onlar normal anasayfayı görür.
        if request.user.is_authenticated and user_has_role(request.user, ROLE_ADMIN):
            from api.models import SetupState
            if not SetupState.load().completed:
                return redirect("dashboard:setup_wizard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Dijital output Start/Stop + sıralama düzenleme yetkisi: rol 1 (admin)
        # + rol 2 (operatör). Client-side bayrak; sunucu endpoint'lerde
        # _require_operator ile ayrıca doğruluyor.
        ctx["can_control"] = user_has_role(self.request.user, 1, 2)
        return ctx


# Bakanlık SAIS kabin kaydı gerektiren tesis tipleri (StationType.code). Tesis
# tipi bunlardan biriyse kurulum sihirbazı SAIS kabin adımını gösterir.
SAIS_CABINET_STATION_TYPES = {"wastewater_monitoring", "emission_monitoring"}


class SetupWizardView(AdminRequiredMixin, TemplateView):
    """İlk kurulum sihirbazı (yalnız yönetici).

    Adımlar:
      1) Tesis bilgileri (ad/tip/adres/kurum).
      2) Tesis tipi SAIS (sürekli atıksu izleme veya sürekli emisyon ölçüm)
         ise Bakanlık SAIS kabin kaydı (SIM ID, kod, kullanıcı/şifre, periyot).
      3) Tamamlandı — Web Erişim Ayarları + Sensör Ayarları sayfalarına geçiş.

    Kayıt `api_views.setup_save` ile yapılır; varsayılan tesisi (id=1) günceller
    ve (gerekiyorsa) tek bir `SaisCabinet` oluşturur/günceller, ardından
    `SetupState`'i tamamlandı olarak damgalar.

    `?preview=1` ile yönetici sihirbazı kurulum tamamlandıktan sonra da yeniden
    açabilir (header butonu bu modu kullanır).
    """
    template_name = "dashboard/setup/wizard.html"

    def get_context_data(self, **kwargs):
        from sais_domain.models import SaisCabinet
        ctx = super().get_context_data(**kwargs)

        station = None
        sid = default_station_id()
        if sid:
            station = Station.objects.filter(pk=sid).select_related("station_type").first()
        ctx["station"] = station

        cabinet = None
        if station:
            cabinet = SaisCabinet.objects.filter(station=station).order_by("created_at").first()
        ctx["cabinet"] = cabinet

        ctx["station_types"] = StationType.objects.order_by("name")
        ctx["sais_type_codes"] = sorted(SAIS_CABINET_STATION_TYPES)
        # Tesis tipi ön seçimi: mevcut tesisin tipi, yoksa "Genel SCADA İzleme"
        # (scada_general) varsayılan gelir.
        ctx["default_type_code"] = (
            station.station_type.code
            if station and station.station_type_id
            else "scada_general"
        )
        ctx["setup_state"] = SetupState.load()
        ctx["is_preview"] = self.request.GET.get("preview") == "1"
        return ctx


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
        # İstasyon seçilmemişse varsayılan istasyon (genelde id=1) ön seçili gelsin.
        if station_id is None:
            station_id = default_station_id()

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


class AggregatesReportView(RoleRequiredMixin, ListView):
    """Aggregate Raporu — istasyon/sensör/seviye/tarih filtreli bucket tablosu."""
    template_name = "dashboard/reports/aggregates.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 50000
    LEVEL_CHOICES = (("15min", _("15 Dakika")), ("hourly", _("Saatlik")), ("daily", _("Günlük")))

    def _filters(self):
        gp = self.request.GET
        now = timezone.now()
        start = parse_report_dt(gp.get("start")) or (now - timedelta(days=7))
        end = parse_report_dt(gp.get("end")) or now
        if end < start:
            start, end = end, start
        try:
            station_id = int(gp.get("station") or 0) or None
        except (TypeError, ValueError):
            station_id = None
        if station_id is None:
            station_id = default_station_id()
        param_ids = []
        for raw in gp.getlist("parameter"):
            try:
                param_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        level = gp.get("level") or "hourly"
        if level not in dict(self.LEVEL_CHOICES):
            level = "hourly"
        return {
            "submitted": bool(gp), "station_id": station_id, "parameter_ids": param_ids,
            "level": level, "start": start, "end": end,
        }

    def get_queryset(self):
        f = self._filters()
        if not f["submitted"] or not f["station_id"]:
            return ReadingHourly.objects.none()
        model = {"15min": ReadingFifteenMin, "hourly": ReadingHourly, "daily": ReadingDaily}[f["level"]]
        qs = (model.objects
              .select_related("sensor__parameter")
              .filter(bucket_start__gte=f["start"], bucket_start__lte=f["end"],
                      sensor__connection__station_id=f["station_id"]))
        if f["parameter_ids"]:
            qs = qs.filter(sensor__parameter_id__in=f["parameter_ids"])
        return qs.order_by("-bucket_start", "sensor_id")[: self.MAX_ROWS]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        f = self._filters()
        ctx["filters"] = f
        ctx["level_choices"] = self.LEVEL_CHOICES
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        if f["station_id"]:
            param_qs = (Parameter.objects
                        .filter(sensors__connection__station_id=f["station_id"])
                        .distinct().order_by("parameter_name").prefetch_related("sensors"))
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


class CalibrationsReportView(RoleRequiredMixin, ListView):
    """Kalibrasyon Geçmişi — istasyon/tip/geçerlilik/tarih filtreli, DataTables rapor."""
    template_name = "dashboard/reports/calibrations.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 5000
    DEFAULT_LIMIT = 1000

    def _filters(self):
        gp = self.request.GET
        try:
            station_id = int(gp.get("station") or 0) or None
        except (TypeError, ValueError):
            station_id = None
        cal_type = gp.get("type") or ""
        if cal_type not in dict(Calibration._meta.get_field("type").choices):
            cal_type = ""
        valid = gp.get("valid") or ""
        return {
            "submitted": bool(gp), "station_id": station_id, "type": cal_type, "valid": valid,
            "start": parse_report_dt(gp.get("start")), "end": parse_report_dt(gp.get("end")),
        }

    def get_queryset(self):
        f = self._filters()
        qs = (Calibration.objects
              .select_related("sensor__parameter", "user")
              .order_by("-time_iso"))
        if f["station_id"]:
            qs = qs.filter(sensor__connection__station_id=f["station_id"])
        if f["type"]:
            qs = qs.filter(type=f["type"])
        if f["valid"] in ("0", "1"):
            qs = qs.filter(is_valid=(f["valid"] == "1"))
        if f["start"]:
            qs = qs.filter(time_iso__gte=f["start"])
        if f["end"]:
            qs = qs.filter(time_iso__lte=f["end"])
        return qs[: (self.MAX_ROWS if f["submitted"] else self.DEFAULT_LIMIT)]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filters"] = self._filters()
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["type_choices"] = Calibration._meta.get_field("type").choices
        ctx["default_limit"] = self.DEFAULT_LIMIT
        return ctx


class PowerOffsReportView(RoleRequiredMixin, ListView):
    """Kapanma Geçmişi — istasyon/durum/tarih filtreli, DataTables rapor."""
    template_name = "dashboard/reports/power_offs.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 5000
    DEFAULT_LIMIT = 1000

    def _filters(self):
        gp = self.request.GET
        try:
            station_id = int(gp.get("station") or 0) or None
        except (TypeError, ValueError):
            station_id = None
        status = gp.get("status") or ""
        if status not in ("ongoing", "closed"):
            status = ""
        return {
            "submitted": bool(gp), "station_id": station_id, "status": status,
            "start": parse_report_dt(gp.get("start")), "end": parse_report_dt(gp.get("end")),
        }

    def get_queryset(self):
        f = self._filters()
        qs = (PowerOff.objects
              .select_related("station")
              .order_by("-start_date", "-time_iso"))
        if f["station_id"]:
            qs = qs.filter(station_id=f["station_id"])
        if f["status"] == "ongoing":
            qs = qs.filter(end_date__isnull=True)
        elif f["status"] == "closed":
            qs = qs.filter(end_date__isnull=False)
        if f["start"]:
            qs = qs.filter(start_date__gte=f["start"])
        if f["end"]:
            qs = qs.filter(start_date__lte=f["end"])
        rows = list(qs[: (self.MAX_ROWS if f["submitted"] else self.DEFAULT_LIMIT)])
        for r in rows:
            if r.start_date and r.end_date:
                r.duration_seconds = int((r.end_date - r.start_date).total_seconds())
            else:
                r.duration_seconds = None
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filters"] = self._filters()
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["default_limit"] = self.DEFAULT_LIMIT
        return ctx


class CommandsReportView(OperatorRequiredMixin, ListView):
    """Komut Geçmişi — durum/kaynak/tarih filtreli, DataTables rapor."""
    template_name = "dashboard/reports/commands.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 5000
    DEFAULT_LIMIT = 1000

    def _filters(self):
        gp = self.request.GET
        status = gp.get("status") or ""
        source = gp.get("source") or ""
        if status not in dict(Command._meta.get_field("status").choices):
            status = ""
        if source not in dict(Command._meta.get_field("source").choices):
            source = ""
        return {
            "submitted": bool(gp), "status": status, "source": source,
            "start": parse_report_dt(gp.get("start")), "end": parse_report_dt(gp.get("end")),
        }

    def get_queryset(self):
        f = self._filters()
        qs = (Command.objects
              .select_related("sensor__parameter", "requested_by")
              .order_by("-created_at"))
        if f["status"]:
            qs = qs.filter(status=f["status"])
        if f["source"]:
            qs = qs.filter(source=f["source"])
        if f["start"]:
            qs = qs.filter(created_at__gte=f["start"])
        if f["end"]:
            qs = qs.filter(created_at__lte=f["end"])
        # Operatör sistem yöneticisinin manuel komutlarını (hareketlerini) göremez.
        if not can_view_admin_events(self.request.user):
            qs = qs.exclude(requested_by__rol=ROLE_ADMIN).exclude(requested_by__is_superuser=True)
        return qs[: (self.MAX_ROWS if f["submitted"] else self.DEFAULT_LIMIT)]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filters"] = self._filters()
        ctx["status_choices"] = Command._meta.get_field("status").choices
        ctx["source_choices"] = Command._meta.get_field("source").choices
        ctx["default_limit"] = self.DEFAULT_LIMIT
        return ctx


class SystemLogsReportView(OperatorRequiredMixin, ListView):
    """Olaylar — tüm SCADA olaylarının (giriş/çıkış, komut, dijital G/Ç, manuel
    işlem, sistem) tek raporu. Tip/önem/kullanıcı/istasyon/tarih filtreli,
    DataTables tabanlı."""
    template_name = "dashboard/reports/system_logs.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 5000
    DEFAULT_LIMIT = 1000

    def _filters(self):
        gp = self.request.GET
        try:
            type_id = int(gp.get("type") or 0) or None
        except (TypeError, ValueError):
            type_id = None
        try:
            station_id = int(gp.get("station") or 0) or None
        except (TypeError, ValueError):
            station_id = None
        try:
            user_id = int(gp.get("user") or 0) or None
        except (TypeError, ValueError):
            user_id = None
        severity = gp.get("severity") or None
        if severity not in {s for s, _l in SystemLog.SEVERITY_CHOICES}:
            severity = None
        return {
            "submitted": bool(gp), "type_id": type_id, "station_id": station_id,
            "user_id": user_id, "severity": severity,
            "start": parse_report_dt(gp.get("start")), "end": parse_report_dt(gp.get("end")),
        }

    def get_queryset(self):
        f = self._filters()
        qs = SystemLog.objects.select_related("type", "station", "user").order_by("-time_iso")
        if f["type_id"]:
            qs = qs.filter(type_id=f["type_id"])
        if f["station_id"]:
            qs = qs.filter(station_id=f["station_id"])
        if f["user_id"]:
            qs = qs.filter(user_id=f["user_id"])
        if f["severity"]:
            qs = qs.filter(severity=f["severity"])
        if f["start"]:
            qs = qs.filter(time_iso__gte=f["start"])
        if f["end"]:
            qs = qs.filter(time_iso__lte=f["end"])
        # Operatör/normal kullanıcı sistem yöneticisinin hareketlerini göremez.
        if not can_view_admin_events(self.request.user):
            qs = qs.exclude(user__rol=ROLE_ADMIN).exclude(user__is_superuser=True)
        return qs[: (self.MAX_ROWS if f["submitted"] else self.DEFAULT_LIMIT)]

    def get_context_data(self, **kwargs):
        from api.models import LogType
        ctx = super().get_context_data(**kwargs)
        ctx["filters"] = self._filters()
        ctx["log_types"] = LogType.objects.order_by("name")
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["severity_choices"] = SystemLog.SEVERITY_CHOICES
        event_users = User.objects.filter(system_logs__isnull=False).distinct()
        if not can_view_admin_events(self.request.user):
            event_users = event_users.exclude(rol=ROLE_ADMIN).exclude(is_superuser=True)
        ctx["event_users"] = event_users.order_by("username")
        ctx["default_limit"] = self.DEFAULT_LIMIT
        return ctx


class AlarmReportsView(OperatorRequiredMixin, ListView):
    """Alarm Raporları — gönderilen SMS/e-posta bildirimleri (NotificationLog).

    Kullanıcı (alıcı), kanal, durum, tür ve tarih filtreli. Filtre uygulanmadan
    son `DEFAULT_LIMIT` (100) kayıt gösterilir; filtreyle `MAX_ROWS`'a kadar.
    DataTables client-side; grafik yok.
    """
    template_name = "dashboard/reports/alarm_reports.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 5000
    DEFAULT_LIMIT = 100
    DATE_FORMAT = "%d.%m.%Y %H:%M:%S"

    CHANNEL_CHOICES = (("", _("Hepsi")), ("sms", "SMS"), ("email", _("E-posta")))
    STATUS_CHOICES = (("", _("Hepsi")), ("ok", _("Başarılı")), ("fail", _("Hatalı")))
    KIND_CHOICES = (("", _("Hepsi")), ("alarm", _("Alarm")), ("test", _("Test")))

    def _parse_dt(self, raw: str):
        if not raw:
            return None
        try:
            return timezone.make_aware(datetime.strptime(raw.strip(), self.DATE_FORMAT))
        except (ValueError, TypeError):
            return None

    def _filters(self):
        gp = self.request.GET
        try:
            user_id = int(gp.get("user") or 0) or None
        except (TypeError, ValueError):
            user_id = None
        channel = gp.get("channel") or ""
        status = gp.get("status") or ""
        kind = gp.get("kind") or ""
        if channel not in dict(self.CHANNEL_CHOICES):
            channel = ""
        if status not in dict(self.STATUS_CHOICES):
            status = ""
        if kind not in dict(self.KIND_CHOICES):
            kind = ""
        return {
            "submitted": bool(gp),
            "user_id": user_id,
            "channel": channel,
            "status": status,
            "kind": kind,
            "start": self._parse_dt(gp.get("start")),
            "end": self._parse_dt(gp.get("end")),
        }

    def get_queryset(self):
        f = self._filters()
        qs = NotificationLog.objects.select_related("sent_by").order_by("-created_at")

        if f["user_id"]:
            user = User.objects.filter(pk=f["user_id"]).first()
            targets = [t for t in ((user.phone_number or "").strip(),
                                   (user.email or "").strip()) if t] if user else []
            qs = qs.filter(recipient__in=targets) if targets else qs.none()
        if f["channel"]:
            qs = qs.filter(channel=f["channel"])
        if f["status"]:
            qs = qs.filter(status=f["status"])
        if f["kind"]:
            qs = qs.filter(kind=f["kind"])
        if f["start"]:
            qs = qs.filter(created_at__gte=f["start"])
        if f["end"]:
            qs = qs.filter(created_at__lte=f["end"])

        limit = self.MAX_ROWS if f["submitted"] else self.DEFAULT_LIMIT
        return qs[:limit]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        f = self._filters()
        ctx["filters"] = f
        ctx["users"] = User.objects.filter(is_active=True).order_by("first_name", "username")
        ctx["channel_choices"] = self.CHANNEL_CHOICES
        ctx["status_choices"] = self.STATUS_CHOICES
        ctx["kind_choices"] = self.KIND_CHOICES
        ctx["default_limit"] = self.DEFAULT_LIMIT
        return ctx


# --------------------------------------------------------------------------- #
# Operator pages
# --------------------------------------------------------------------------- #

class ScenarioBuilderView(OperatorRequiredMixin, TemplateView):
    """Operatör/Yönetici → Numune Senaryosu (4 sekme: Senaryolar / Kurucu /
    Durum / Geçmiş).

    No-code senaryo kütüphanesi: tetik kombinasyonu + adım + aksiyon kurucu;
    canlı durum izleme + Bakanlık talep tetiği. Yürütme `run_scenarios` task'ı
    tarafından yapılır.
    """
    template_name = "dashboard/operator/scenario_builder.html"

    def get_context_data(self, **kwargs):
        from sais_domain.models import Scenario
        ctx = super().get_context_data(**kwargs)
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["default_station_id"] = default_station_id()
        ctx["kind_choices"] = Scenario.KIND_CHOICES
        ctx["window_choices"] = Scenario.WINDOW_CHOICES
        ctx["trigger_choices"] = Scenario.TRIGGER_CHOICES
        ctx["action_types"] = [
            ("notify", _("Bildirim Gönder")),
            ("sampler_on", _("Numune Alıcıyı Aç")),
            ("sampler_off", _("Numune Alıcıyı Kapat")),
            ("ministry_get_code", _("Bakanlık'tan Numune Kodu Al")),
            ("sim_sample_start", _("SIM: Numune Başladı")),
            ("sim_sample_complete", _("SIM: Numune Tamamlandı")),
            ("sim_sample_error", _("SIM: Numune Hatası")),
            ("send_diagnostic", _("Diagnostik Gönder (701/702)")),
        ]
        return ctx


class ScenarioDesignerView(OperatorRequiredMixin, TemplateView):
    """Operatör/Yönetici → Senaryo Tasarımcı (Demo).

    Node-graph (PLC-benzeri) görsel senaryo tasarımcısı: sürükle-bırak sensör
    giriş / AND-OR-NOT mantık / gecikme / çıkış-aksiyon node'ları, aralarında
    tel bağlantıları. Bu fazda yalnız tasarlanıp graph JSON olarak saklanır
    (yürütme motoru + per-senaryo beat sonraki faz).
    """
    template_name = "dashboard/operator/scenario_designer.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["default_station_id"] = default_station_id()
        return ctx


class AlarmsView(OperatorRequiredMixin, TemplateView):
    """Operatör/Yönetici → Alarm Yönetimi (2 sekme: Ölçüm + Diagnostik).

    Alarm tanımları oluşturma/listeleme/silme; tetikleme + bildirim gönderimi
    periyodik `api.tasks.run_alarms` task'ı tarafından yapılır.
    """
    template_name = "dashboard/operator/alarms.html"

    def get_context_data(self, **kwargs):
        from api.models import AlarmRule, MessageTemplate
        ctx = super().get_context_data(**kwargs)
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["default_station_id"] = default_station_id()
        ctx["period_choices"] = AlarmRule.PERIOD_CHOICES
        ctx["cond_choices"] = AlarmRule.COND_CHOICES
        ctx["message_templates"] = MessageTemplate.objects.all()[:200]
        rules = (
            AlarmRule.objects
            .select_related("station", "parameter", "sensor")
            .order_by("-created_at")
        )
        ctx["analog_rules"] = [r for r in rules if r.rule_type == AlarmRule.RULE_ANALOG]
        ctx["diag_rules"] = [r for r in rules if r.rule_type != AlarmRule.RULE_ANALOG]
        return ctx


class RemindersView(RoleRequiredMixin, TemplateView):
    """Takvim Hatırlatıcı — paylaşımlı (tüm roller görür ve yönetir).

    Aylık takvim ızgarasından gün seçilir, hatırlatıcı kurulur. Vadesi geldiğinde
    header'daki çan ikonu + anasayfa widget'ı (yalnız dashboard içi) bildirir.
    CRUD `dashboard/api_views.py` `reminder_*` AJAX endpoint'leriyle yapılır.
    """
    template_name = "dashboard/reminders/index.html"

    def get_context_data(self, **kwargs):
        from api.models import Reminder
        ctx = super().get_context_data(**kwargs)
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["priority_choices"] = Reminder.PRIORITY_CHOICES
        return ctx


class CalibrationWizardView(OperatorRequiredMixin, TemplateView):
    """Operatör/Yönetici → İnteraktif Kalibrasyon.

    3 adımlı sihirbaz: (1) Kal. Adımları — istasyon/parametre/tip/referans/süre
    seçimi, (2) Ölçüm — sensörü solüsyona daldırma animasyonu + canlı değer
    izleme + ±%25 tolerans bandında otomatik algılama + süre boyunca örnekleme,
    (3) Rapor — ortalama/sapma/durum tablosu + kaydet + Bakanlık SIM'e gönder.

    Canlı değer `SensorLatest` snapshot'ından `calibration_live` ile çekilir;
    kayıt `Calibration` tablosuna yazılır; SIM gönderimi `SaisSimClient
    .send_calibration` ile yapılır.
    """
    template_name = "dashboard/operator/calibration.html"

    DURATION_CHOICES = (30, 60, 120, 180, 300)
    DEFAULT_TOLERANCE = 5  # ± yüzde

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["stations"] = Station.objects.filter(active=True).order_by("name")
        ctx["default_station_id"] = default_station_id()
        ctx["type_choices"] = Calibration._meta.get_field("type").choices
        ctx["duration_choices"] = self.DURATION_CHOICES
        ctx["tolerance_pct"] = self.DEFAULT_TOLERANCE
        return ctx


class MimicDashboardView(AdminRequiredMixin, ListView):
    """Yönetici → Mimik Tasarımları (galeri).

    Kullanıcının oluşturduğu SCADA/HMI mimik ekranlarının galeri görünümü.
    Her kart bir tasarımın önizlemesini (`thumbnail`) + ad/tarih bilgisini ve
    aksiyonları (Düzenle / Görüntüle / İndir / Sil) gösterir. "Yeni Mimik" ve
    tasarım kartları **yeni sekmede** standalone editör/görüntüleyiciyi açar
    (`MimicEditorView` / `MimicViewerView` — dashboard iskeleti olmadan, tam
    ekran HMI tasarım ortamı).

    Editör SCADA'ya bağlı değildir (bu faz); tasarımlar `MimicScreen` olarak
    saklanır, simüle edilebilir, JSON/PNG/SVG olarak dışa aktarılabilir.
    """
    template_name = "dashboard/admin_pages/mimic.html"
    context_object_name = "screens"

    def get_queryset(self):
        from .models import MimicScreen
        return MimicScreen.objects.select_related("created_by").order_by("-updated_at")


class MimicEditorView(AdminRequiredMixin, TemplateView):
    """Standalone tam-ekran mimik tasarım editörü (Fabric.js).

    Dashboard iskeleti (header/sidebar) **olmadan** kendi tasarım ortamı
    chrome'uyla render eder; galeriden yeni sekmede açılır. `pk` verilirse o
    mimik düzenleme modunda yüklenir, yoksa boş tuval.
    """
    template_name = "dashboard/mimic/editor.html"

    def get_context_data(self, **kwargs):
        from .models import MimicScreen
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        screen = MimicScreen.objects.filter(pk=pk).first() if pk else None
        ctx["screen"] = screen
        return ctx


class MimicViewerView(RoleRequiredMixin, TemplateView):
    """Standalone tam-ekran mimik görüntüleyici / simülatör (salt-okunur).

    Kaydedilmiş bir mimiği render edip simülasyon modunda animasyonları
    canlandırır; düzenleme araçları yoktur. Yeni sekmede açılır; her rol
    görüntüleyebilir.
    """
    template_name = "dashboard/mimic/viewer.html"

    def get_context_data(self, **kwargs):
        from django.http import Http404
        from .models import MimicScreen
        ctx = super().get_context_data(**kwargs)
        screen = MimicScreen.objects.filter(pk=self.kwargs.get("pk")).first()
        if screen is None:
            raise Http404("Mimik bulunamadı.")
        ctx["screen"] = screen
        return ctx


# --------------------------------------------------------------------------- #
# Sensör Ayarları (operatör + yönetici): Scan Grubu + Sensör CRUD + Canlı Test
# --------------------------------------------------------------------------- #

class ConnectionConfigListView(OperatorRequiredMixin, ListView):
    """Sensör Ayarları landing — bağlantılar listesi (düzenlenebilir).

    Hiyerarşi: Bağlantı → Scan Grupları → Sensörler. Bu sayfa giriş noktası;
    her satır sihirbaza (bağlantı bazlı) gider.
    """
    model = Connection
    template_name = "dashboard/sensor_config/connection_list.html"
    context_object_name = "connections"
    paginate_by = 50

    def get_queryset(self):
        return (
            super().get_queryset()
            .select_related("station")
            .annotate(scangroup_total=Count("scan_groups", distinct=True),
                      sensor_total=Count("sensors", distinct=True))
            .order_by("station__name", "name")
        )


class ScanGroupWizardView(OperatorRequiredMixin, TemplateView):
    """Üç adımlı kurulum sihirbazı: Bağlantı → Scan Grupları → Sensörler.

    Step 1: bağlantı oluştur/düzenle (AJAX `connection_save`/`connection_detail`).
    Step 2: o bağlantının scan gruplarını yönet (AJAX `scangroup_*`).
    Step 3: seçili gruba sensör ekle + her sensörde canlı **test** butonu.
    Düzenleme modunda `pk` = Connection.id; sihirbaz o bağlantı ile açılır.
    """
    template_name = "dashboard/sensor_config/scangroup_wizard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        connection = None
        if pk:
            connection = (
                Connection.objects.select_related("station").filter(pk=pk).first()
            )
        ctx["connection"] = connection
        ctx["parameters"] = Parameter.objects.order_by("parameter_txt", "parameter_name", "id")
        ctx["function_choices"] = ScanGroup.READ_FUNCTIONS
        ctx["sensor_type_choices"] = Sensor.SENSOR_TYPE
        ctx["data_type_choices"] = Sensor.DATA_TYPES
        ctx["byte_order_choices"] = Sensor.BYTE_ORDER
        # Step 1 (Bağlantı) seçenekleri
        ctx["stations"] = Station.objects.order_by("name")
        ctx["protocol_choices"] = Connection.PROTOCOL_CHOICES
        ctx["transport_choices"] = Connection.TRANSPORT_CHOICES
        ctx["baudrate_choices"] = Connection.BAUDRATES
        ctx["parity_choices"] = Connection.PARITY
        ctx["stop_bits_choices"] = Connection.STOP_BITS
        ctx["byte_size_choices"] = Connection.BYTE_SIZE
        return ctx


class ConnectionConfigDeleteView(OperatorRequiredMixin, DeleteView):
    model = Connection
    template_name = "dashboard/sensor_config/connection_confirm_delete.html"
    success_url = reverse_lazy("dashboard:sensorcfg_connections")
    context_object_name = "connection"

    def form_valid(self, form):
        label = str(self.object)
        response = super().form_valid(form)
        messages.success(self.request, _("Bağlantı silindi."))
        log_event(
            EventType.CONFIG,
            f"Bağlantı silindi: {label}",
            severity="warning", request=self.request,
        )
        return response


class SensorConfigListView(OperatorRequiredMixin, ListView):
    model = Sensor
    template_name = "dashboard/sensor_config/sensor_list.html"
    context_object_name = "sensors"
    paginate_by = 50

    def get_queryset(self):
        return super().get_queryset().select_related("parameter", "connection", "scan_group")


class SensorConfigCreateView(OperatorRequiredMixin, CreateView):
    model = Sensor
    form_class = SensorConfigForm
    template_name = "dashboard/sensor_config/sensor_form.html"
    success_url = reverse_lazy("dashboard:sensorcfg_sensors")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Sensör oluşturuldu."))
        log_event(
            EventType.CONFIG,
            f"Sensör oluşturuldu: {self.object} (bağlantı={self.object.connection})",
            severity="warning", request=self.request,
        )
        return response


class SensorConfigUpdateView(OperatorRequiredMixin, UpdateView):
    model = Sensor
    form_class = SensorConfigForm
    template_name = "dashboard/sensor_config/sensor_form.html"
    success_url = reverse_lazy("dashboard:sensorcfg_sensors")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Sensör güncellendi."))
        log_event(
            EventType.CONFIG,
            f"Sensör güncellendi: {self.object} (bağlantı={self.object.connection})",
            severity="warning", request=self.request,
        )
        return response


class SensorConfigDeleteView(OperatorRequiredMixin, DeleteView):
    model = Sensor
    template_name = "dashboard/sensor_config/sensor_confirm_delete.html"
    success_url = reverse_lazy("dashboard:sensorcfg_sensors")
    context_object_name = "sensor"

    def form_valid(self, form):
        label = str(self.object)
        response = super().form_valid(form)
        messages.success(self.request, _("Sensör silindi."))
        log_event(
            EventType.CONFIG,
            f"Sensör silindi: {label}",
            severity="warning", request=self.request,
        )
        return response


class SensorTestView(OperatorRequiredMixin, TemplateView):
    """Canlı sensör/scan grubu test konsolu.

    Sahadaki personel kayıtlı bir sensörü veya scan grubunu anlık cihazdan
    okuyup decode edilmiş değeri + ham register'ları + kalite/hata bilgisini
    görür; girdiği ayarın doğru olup olmadığını anında doğrular. Asıl okuma
    `api_views.sensor_test_run` / `scangroup_test_run` AJAX endpoint'lerinde.
    """
    template_name = "dashboard/sensor_config/sensor_test.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["connections"] = (
            Connection.objects.select_related("station").order_by("station", "name")
        )
        return ctx


# --------------------------------------------------------------------------- #
# Admin pages (rol=1 only)
# --------------------------------------------------------------------------- #

def _sync_ministry_token(user):
    """Bakanlık (rol=4) kullanıcısına DRF API token'ı garanti eder.

    Rol 4 panele giremediği için API erişimini Token ile yapar. Rol 4'ten
    başka bir role değiştirilirse token silinir (artık gerekmez).
    """
    from rest_framework.authtoken.models import Token
    from dashboard.permissions import ROLE_MINISTRY

    if getattr(user, "rol", None) == ROLE_MINISTRY:
        Token.objects.get_or_create(user=user)
    else:
        Token.objects.filter(user=user).delete()


class UserListView(OperatorRequiredMixin, ListView):
    model = User
    template_name = "dashboard/admin_pages/user_list.html"
    context_object_name = "users"
    paginate_by = 50

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Her satır için: operatör admin'in eklediği kullanıcıya dokunamaz.
        for u in ctx.get("users", []):
            u.can_manage = can_manage_target_user(self.request.user, u)
        return ctx


class UserCreateView(OperatorRequiredMixin, CreateView):
    model = User
    form_class = AdminUserCreateForm
    template_name = "dashboard/admin_pages/user_form.html"
    success_url = reverse_lazy("dashboard:admin_users")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["acting_user"] = self.request.user  # operatör rol=1 atayamaz
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        # Ekleyeni damgala (kim ekledi → düzenleme yetkisi bununla belirlenir).
        if self.object.added_by != self.request.user.pk:
            self.object.added_by = self.request.user.pk
            self.object.save(update_fields=["added_by"])
        _sync_ministry_token(self.object)
        messages.success(self.request, _("Kullanıcı oluşturuldu."))
        log_event(
            EventType.USER_MGMT,
            f"Kullanıcı oluşturuldu: {self.object.get_username()} (rol={getattr(self.object, 'rol', '?')})",
            severity="warning", request=self.request,
        )
        return response


class UserUpdateView(OperatorRequiredMixin, UpdateView):
    model = User
    form_class = AdminUserUpdateForm
    template_name = "dashboard/admin_pages/user_form.html"
    success_url = reverse_lazy("dashboard:admin_users")

    def dispatch(self, request, *args, **kwargs):
        # Operatör, Sistem Yöneticisi'nin eklediği kullanıcıya müdahale edemez.
        if request.user.is_authenticated:
            target = self.get_object()
            if not can_manage_target_user(request.user, target):
                messages.error(
                    request,
                    _("Bu kullanıcı Sistem Yöneticisi tarafından eklenmiş; "
                      "düzenleme yetkiniz yok."),
                )
                return redirect("dashboard:admin_users")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["acting_user"] = self.request.user  # operatör rol=1 atayamaz
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        _sync_ministry_token(self.object)
        messages.success(self.request, _("Kullanıcı güncellendi."))
        log_event(
            EventType.USER_MGMT,
            f"Kullanıcı güncellendi: {self.object.get_username()}",
            severity="warning", request=self.request,
        )
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Bakanlık (rol=4) kullanıcısının API token'ı — yalnız rol=1 admin görür.
        from dashboard.permissions import ROLE_MINISTRY
        if (
            getattr(self.object, "rol", None) == ROLE_MINISTRY
            and user_has_role(self.request.user, ROLE_ADMIN)
        ):
            from rest_framework.authtoken.models import Token
            token = Token.objects.filter(user=self.object).first()
            ctx["ministry_token"] = token.key if token else None
        return ctx


class UserResetPasswordView(OperatorRequiredMixin, TemplateView):
    """Admin, user'a rastgele geçici şifre atar — UI'da gösterilir."""
    template_name = "dashboard/admin_pages/user_reset_password.html"

    def post(self, request, pk):
        target = User.objects.get(pk=pk)
        if not can_manage_target_user(request.user, target):
            messages.error(
                request,
                _("Bu kullanıcı Sistem Yöneticisi tarafından eklenmiş; "
                  "şifre sıfırlama yetkiniz yok."),
            )
            return redirect("dashboard:admin_users")
        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(12))
        target.set_password(new_password)
        target.save()
        log_event(
            EventType.USER_MGMT,
            f"Kullanıcı şifresi sıfırlandı: {target.get_username()}",
            severity="warning", request=request,
        )
        messages.success(
            request,
            _(
                "Yeni geçici şifre: %(pw)s — kullanıcıya elden iletin; "
                "ilk girişte değiştirmesi önerilir."
            ) % {"pw": new_password},
        )
        return redirect("dashboard:admin_user_edit", pk=target.pk)


class UserTokenRefreshView(AdminRequiredMixin, View):
    """Bakanlık (rol=4) kullanıcısının API token'ını yeniler (eski geçersiz olur).

    Yalnız rol=1 admin; POST + JSON döner.
    """

    def post(self, request, pk):
        from django.http import JsonResponse
        from rest_framework.authtoken.models import Token
        from dashboard.permissions import ROLE_MINISTRY

        target = User.objects.filter(pk=pk).first()
        if target is None:
            return JsonResponse({"ok": False, "error": _("Kullanıcı bulunamadı.")}, status=404)
        if getattr(target, "rol", None) != ROLE_MINISTRY:
            return JsonResponse(
                {"ok": False, "error": _("Token yalnızca Bakanlık kullanıcısı için üretilir.")},
                status=400,
            )
        Token.objects.filter(user=target).delete()
        token = Token.objects.create(user=target)
        log_event(
            EventType.USER_MGMT,
            f"API token yenilendi: {target.get_username()}",
            severity="warning", request=request,
        )
        return JsonResponse({"ok": True, "token": token.key})


class ApiLogsView(AdminRequiredMixin, ListView):
    """API Logları — gelen (inbound) + giden (outbound) HTTP trafiğinin standart
    rapor formatındaki görünümü. Yön/method/durum/arama/tarih filtreli,
    DataTables client-side. Filtre uygulanmadan son `DEFAULT_LIMIT` kayıt
    gösterilir; filtreyle `MAX_ROWS`'a kadar."""
    template_name = "dashboard/admin_pages/api_logs.html"
    context_object_name = "rows"
    paginate_by = None
    MAX_ROWS = 5000
    DEFAULT_LIMIT = 200

    DIRECTION_CHOICES = (
        ("in", _("Gelen (IN)")),
        ("out", _("Giden (OUT)")),
    )
    STATUS_CLASS_CHOICES = (
        ("2xx", _("2xx Başarılı")),
        ("3xx", _("3xx Yönlendirme")),
        ("4xx", _("4xx İstemci Hatası")),
        ("5xx", _("5xx Sunucu Hatası")),
        ("err", _("Hata (mesajlı)")),
    )

    def _filters(self):
        gp = self.request.GET
        direction = gp.get("direction") or ""
        if direction not in dict(self.DIRECTION_CHOICES):
            direction = ""
        method = (gp.get("method") or "").upper().strip()
        status_class = gp.get("status_class") or ""
        if status_class not in dict(self.STATUS_CLASS_CHOICES):
            status_class = ""
        return {
            "submitted": bool(gp), "direction": direction, "method": method,
            "status_class": status_class, "search": (gp.get("q") or "").strip(),
            "start": parse_report_dt(gp.get("start")), "end": parse_report_dt(gp.get("end")),
        }

    def get_queryset(self):
        f = self._filters()
        qs = ApiLog.objects.select_related("user").order_by("-created_at")
        if f["direction"]:
            qs = qs.filter(direction=f["direction"])
        if f["method"]:
            qs = qs.filter(method=f["method"])
        sc = f["status_class"]
        if sc == "2xx":
            qs = qs.filter(response_status__gte=200, response_status__lt=300)
        elif sc == "3xx":
            qs = qs.filter(response_status__gte=300, response_status__lt=400)
        elif sc == "4xx":
            qs = qs.filter(response_status__gte=400, response_status__lt=500)
        elif sc == "5xx":
            qs = qs.filter(response_status__gte=500)
        elif sc == "err":
            qs = qs.exclude(error_message="")
        if f["search"]:
            qs = qs.filter(
                Q(url__icontains=f["search"])
                | Q(target_host__icontains=f["search"])
                | Q(source_component__icontains=f["search"])
                | Q(remote_ip__icontains=f["search"])
            )
        if f["start"]:
            qs = qs.filter(created_at__gte=f["start"])
        if f["end"]:
            qs = qs.filter(created_at__lte=f["end"])
        return qs[: (self.MAX_ROWS if f["submitted"] else self.DEFAULT_LIMIT)]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filters"] = self._filters()
        ctx["direction_choices"] = self.DIRECTION_CHOICES
        ctx["status_class_choices"] = self.STATUS_CLASS_CHOICES
        ctx["methods"] = list(
            ApiLog.objects.exclude(method="")
            .values_list("method", flat=True).distinct().order_by("method")
        )
        ctx["default_limit"] = self.DEFAULT_LIMIT
        return ctx


class SystemControlView(OperatorRequiredMixin, TemplateView):
    """Yönetici → Sistem Kontrol.

    Üç global aç/kapa (SIM, Envisoft, Polling) + manuel/haftalık yıkama +
    manuel bakım modu (istasyon/tesis bakımda) tetikleme + Celery durum
    widget'ı. POST handler `action` parametresine göre işlem yapar:
    save_switches, start_manual_wash, start_weekly_wash, start_station_maint,
    start_facility_maint, stop_wash.
    """
    template_name = "dashboard/admin_pages/system_control.html"

    # Override süre clamp'i — UI input max'iyle senkron.
    MANUAL_WASH_MAX_MIN = 60
    WEEKLY_WASH_MAX_MIN = 180
    STATION_MAINT_MAX_MIN = 1440   # 24 saat
    FACILITY_MAINT_MAX_MIN = 1440  # 24 saat

    def get_context_data(self, **kwargs):
        from django.conf import settings

        from api.models import StatusCode
        from sais_domain.models import SimStatusPolicy, SystemSwitch
        ctx = super().get_context_data(**kwargs)
        switch = SystemSwitch.load()
        ctx["switch"] = switch
        ctx["celery_status"] = _celery_status()
        ctx["wash_remaining_seconds"] = switch.wash_remaining_seconds()
        ctx["wash_active_status_code"] = switch.active_wash_status_code()
        ctx["app_version"] = getattr(settings, "APP_VERSION", "dev")

        # SCADA bağlantıları — TCP/IP aç-kapa toggle'ı için (sol kart).
        from api.models import Connection
        ctx["connections"] = (
            Connection.objects.select_related("station")
            .order_by("station__name", "name")
        )

        # --- SIM status filtresi ---
        policy = SimStatusPolicy.load()
        blocked = (
            set(policy.blocked_statuses.values_list("code", flat=True))
            if policy.configured else None
        )
        status_codes = list(StatusCode.objects.order_by("code"))
        rows = []
        for sc in status_codes:
            if policy.configured:
                send = sc.code not in blocked
            else:  # öneri: yalnız operasyonel statuslar gönderilsin
                send = sc.code in SimStatusPolicy.OPERATIONAL_CODES
            rows.append({
                "code": sc.code,
                "name": sc.name,
                "send": send,
                "operational": sc.code in SimStatusPolicy.OPERATIONAL_CODES,
            })
        ctx["sim_policy"] = policy
        ctx["sim_status_rows"] = rows
        ctx["sim_fallback_choices"] = status_codes
        return ctx

    def post(self, request, *args, **kwargs):
        from sais_domain.models import SystemSwitch
        switch = SystemSwitch.load()
        action = request.POST.get("action", "save_switches")

        if action == "save_sim_status_policy":
            self._save_sim_status_policy(request)
            return redirect("dashboard:admin_system_control")

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

        elif action == "start_station_maint":
            minutes = self._parse_minutes(
                request.POST.get("station_maint_minutes"),
                default=switch.station_maint_duration_minutes,
                lo=1, hi=self.STATION_MAINT_MAX_MIN,
            )
            self._start_wash(switch, request.user, kind="station_maint", minutes=minutes)
            switch.station_maint_duration_minutes = minutes
            switch.save()
            messages.success(request,
                _("İstasyon bakım modu başlatıldı: %(m)d dk.") % {"m": minutes})

        elif action == "start_facility_maint":
            minutes = self._parse_minutes(
                request.POST.get("facility_maint_minutes"),
                default=switch.facility_maint_duration_minutes,
                lo=1, hi=self.FACILITY_MAINT_MAX_MIN,
            )
            self._start_wash(switch, request.user, kind="facility_maint", minutes=minutes)
            switch.facility_maint_duration_minutes = minutes
            switch.save()
            messages.success(request,
                _("Tesis bakım modu başlatıldı: %(m)d dk.") % {"m": minutes})

        elif action == "stop_wash":
            switch.wash_active_kind = None
            switch.wash_started_at = None
            switch.wash_ends_at = None
            switch.wash_started_by = None
            switch.updated_by = request.user
            switch.save()
            messages.success(request, _("Aktif manuel mod durduruldu."))

        elif action == "close_connections":
            self._close_scada_connections(request)

        else:  # save_switches — mevcut davranış
            switch.sim_enabled = request.POST.get("sim_enabled") == "on"
            switch.envisoft_enabled = request.POST.get("envisoft_enabled") == "on"
            switch.polling_enabled = request.POST.get("polling_enabled") == "on"
            switch.updated_by = request.user
            switch.save()
            messages.success(request, _("Sistem kontrol ayarları kaydedildi."))

        return redirect("dashboard:admin_system_control")

    def _close_scada_connections(self, request) -> None:
        """Worker'ların açık tuttuğu persistent TCP/serial socket'leri kapatır.

        Sistem kontrol sayfasından "Sensör Okuması" anahtarını kapatmak yalnız
        yeni okuma görevlerinin kuyruğa alınmasını durdurur; worker process'inin
        belleğinde (`scada_io.connection_pool._POOL`) açık olan socket gateway'e
        bağlı kalır. Bu socket'leri kapatmanın tek yolu worker process'inin
        içinden geçer:

          - solo pool (dev): `close_scada_pool` control command ana process'teki
            pool'u doğrudan kapatır.
          - prefork pool (prod, --concurrency=4): açık socket'ler child
            process'lerin belleğinde; `pool_restart` ile child'lar geri
            dönüştürülür (process ölünce OS socket'i kapatır).

        İki yöntem de broker üzerinden tüm worker node'larına broadcast edilir;
        biri ilgisiz pool tipinde no-op olur, zararsızdır.
        """
        from scada_io.pool_control import broadcast_close_pools

        summary = broadcast_close_pools(reason=request.user.username or "manual")
        if summary["error"]:
            messages.error(
                request,
                _("Bağlantı kapatma isteği gönderilemedi: %(e)s")
                % {"e": summary["error"]},
            )
            return

        workers = summary["workers"]
        closed = summary["closed"]
        if workers == 0:
            messages.warning(
                request,
                _("Hiçbir worker yanıt vermedi — worker çalışmıyor olabilir. "
                  "Gerekirse worker'ı yeniden başlatın."),
            )
        else:
            messages.success(
                request,
                _("Açık bağlantılar kapatıldı: %(w)d worker, %(c)d bağlantı "
                  "kapatıldı; worker process'leri tazelendi.")
                % {"w": workers, "c": closed},
            )

    def _save_sim_status_policy(self, request) -> None:
        """SIM status filtresini kaydeder: işaretli ('SIM'e Gönder') statuslar
        haricindeki tüm statuslar engellenir (fallback ile değiştirilir)."""
        from api.models import StatusCode
        from sais_domain.models import SimStatusPolicy

        sent_codes = set()
        for raw in request.POST.getlist("send_status"):
            try:
                sent_codes.add(int(raw))
            except (TypeError, ValueError):
                continue

        try:
            fallback = int(request.POST.get("fallback_status_code") or 1)
        except (TypeError, ValueError):
            fallback = 1

        all_codes = set(StatusCode.objects.values_list("code", flat=True))
        blocked_codes = all_codes - sent_codes
        blocked_qs = StatusCode.objects.filter(code__in=blocked_codes)

        policy = SimStatusPolicy.load()
        policy.fallback_status_code = fallback
        policy.configured = True
        policy.updated_by = request.user
        policy.save()
        policy.blocked_statuses.set(blocked_qs)

        messages.success(
            request,
            _("SIM status filtresi kaydedildi: %(n)d status engellendi.")
            % {"n": len(blocked_codes)},
        )

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

class BackupRestoreView(OperatorRequiredMixin, TemplateView):
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
            log_event(
                EventType.BACKUP, f"Manuel yedekleme başlatıldı (tier={tier})",
                severity="warning", request=request,
            )
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
                    log_event(
                        EventType.BACKUP,
                        f"Veritabanı geri yükleme başlatıldı (yedek#{backup.id}, sürüm={backup.app_version})",
                        severity="critical", request=request,
                    )
                    messages.warning(request, _(
                        "Geri yükleme başlatıldı. Sistem kısa süre kesintiye uğrayabilir; "
                        "işlem bitince sayfayı yenileyin."
                    ))
        else:
            messages.error(request, _("Geçersiz işlem."))

        return redirect("dashboard:admin_backups")


# --------------------------------------------------------------------------- #
# Lisanslama: kilit ekranı + admin durum sayfası
# --------------------------------------------------------------------------- #

def _is_admin(user) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "rol", None) == ROLE_ADMIN)


def _handle_license_action(request) -> None:
    """Lisans 'Yenile' / 'Token Uygula' POST aksiyonlarını işler (mesajlarla)."""
    import json

    from api.licensing import LicenseError, apply_token, fetch_and_refresh

    action = request.POST.get("action")
    if action == "refresh":
        try:
            lic = fetch_and_refresh()
            if lic.last_check_ok:
                log_event(EventType.LICENSE, "Lisans yenilendi (manuel)", request=request)
                messages.success(request, _("Lisans yenilendi."))
            else:
                messages.warning(request, _("Lisans çekilemedi: %(e)s") % {"e": lic.last_error})
        except LicenseError as exc:
            messages.error(request, _("Lisans geçersiz: %(e)s") % {"e": exc})
        except Exception as exc:  # noqa: BLE001
            messages.error(request, _("Lisans çekilemedi: %(e)s") % {"e": exc})
    elif action == "apply_token":
        if not _is_admin(request.user):
            messages.error(request, _("Bu işlem için yetkiniz yok."))
            return
        raw = (request.POST.get("token") or "").strip()
        try:
            token = json.loads(raw)
            apply_token(token, source="manual-ui")
            log_event(
                EventType.LICENSE, "Lisans token'ı elle uygulandı",
                severity="warning", request=request,
            )
            messages.success(request, _("Lisans uygulandı."))
        except json.JSONDecodeError:
            messages.error(request, _("Token JSON ayrıştırılamadı."))
        except (LicenseError, KeyError, TypeError) as exc:
            messages.error(request, _("Token geçersiz: %(e)s") % {"e": exc})


class LicenseExpiredView(LoginRequiredMixin, TemplateView):
    """Lisans bitince gösterilen tam-kilit ekranı (menüsüz)."""
    template_name = "dashboard/license_expired.html"
    login_url = reverse_lazy("dashboard:login")

    def dispatch(self, request, *args, **kwargs):
        from api.licensing import license_active
        if request.user.is_authenticated and license_active():
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from api.licensing import license_status_dict
        ctx = super().get_context_data(**kwargs)
        ctx["lic"] = license_status_dict()
        ctx["is_admin"] = _is_admin(self.request.user)
        return ctx

    def post(self, request, *args, **kwargs):
        from api.licensing import license_active
        _handle_license_action(request)
        if license_active():
            return redirect("dashboard:home")
        return redirect("dashboard:license_expired")


class LicenseStatusView(AdminRequiredMixin, TemplateView):
    """Yönetici → Lisans (aktifken proaktif yönetim: durum + yenile + token uygula)."""
    template_name = "dashboard/admin_pages/license.html"

    def get_context_data(self, **kwargs):
        from api.licensing import license_status_dict
        from api.models import License
        ctx = super().get_context_data(**kwargs)
        ctx["lic"] = license_status_dict()
        ctx["raw_token"] = License.load().raw_token
        return ctx

    def post(self, request, *args, **kwargs):
        _handle_license_action(request)
        return redirect("dashboard:admin_license")


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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("prefs_form", PreferencesForm(instance=self.request.user))
        return ctx

    def post(self, request, *args, **kwargs):
        form = PreferencesForm(request.POST, instance=request.user)
        if form.is_valid():
            user = form.save()
            # Değişiklik anında geçerli olsun — mevcut oturuma da uygula.
            minutes = user.session_timeout_minutes
            request.session.set_expiry(0 if minutes == 0 else minutes * 60)
            messages.success(request, _("Tercihler kaydedildi."))
            return redirect("dashboard:preferences")
        ctx = self.get_context_data(prefs_form=form)
        return self.render_to_response(ctx)


# --------------------------------------------------------------------------- #
# Admin: Web Erişim Ayarları (domain + SSL → Caddy)
# --------------------------------------------------------------------------- #

class WebSettingsView(AdminRequiredMixin, TemplateView):
    """Yönetici → Web Erişim Ayarları.

    Domain + TLS modu (letsencrypt/manual/internal) yönetilir. Kayıtta
    `api.web_proxy.apply()` Caddyfile'ı yeniden üretir; Caddy `--watch` ile
    reload eder. Manuel modda PEM/PFX sertifika yüklenir (PFX→PEM çevrilir).

    POST `action`: save_settings, upload_cert, rerender.
    """
    template_name = "dashboard/admin_pages/web_settings.html"

    def get_context_data(self, **kwargs):
        from api.models import WebSettings
        ws = WebSettings.load()
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("settings_form", WebSettingsForm(instance=ws))
        ctx.setdefault("cert_form", CertUploadForm())
        ctx["ws"] = ws
        ctx["caddy_config_path"] = settings.CADDY_CONFIG_PATH
        ctx["cert_days_remaining"] = ws.cert_days_remaining()
        return ctx

    def post(self, request, *args, **kwargs):
        from api import web_proxy
        from api.models import WebSettings
        ws = WebSettings.load()
        action = request.POST.get("action", "save_settings")

        if action == "upload_cert":
            return self._handle_upload(request, ws)

        if action == "rerender":
            ok, error = web_proxy.apply(ws)
            if ok:
                messages.success(request, _("Caddyfile yeniden üretildi."))
            else:
                messages.error(request, _("Üretim hatası: %(e)s") % {"e": error})
            return redirect("dashboard:admin_web_settings")

        # save_settings
        form = WebSettingsForm(request.POST, instance=ws)
        if not form.is_valid():
            ctx = self.get_context_data(settings_form=form)
            return self.render_to_response(ctx)
        ws = form.save(commit=False)
        ws.updated_by = request.user
        ws.save()
        ok, error = web_proxy.apply(ws)
        if ok:
            log_event(
                EventType.CONFIG,
                f"Web erişim ayarları değiştirildi (domain={ws.domain or '—'}, tls={ws.tls_mode})",
                severity="warning", request=request,
            )
            messages.success(request, _("Web erişim ayarları kaydedildi ve uygulandı."))
            if ws.tls_mode == ws.TLS_LETSENCRYPT and ws.enabled:
                messages.info(request, _(
                    "Let's Encrypt sertifikası için DNS A kaydı ve 443 yönlendirmesi "
                    "gerekir; sertifika alımı birkaç dakika sürebilir."
                ))
        else:
            messages.error(request, _("Kaydedildi ama üretim hatası: %(e)s") % {"e": error})
        return redirect("dashboard:admin_web_settings")

    def _handle_upload(self, request, ws):
        from api import web_proxy
        form = CertUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            ctx = self.get_context_data(cert_form=form)
            return self.render_to_response(ctx)

        fmt = form.cleaned_data["cert_format"]
        cert_file = form.cleaned_data["cert_file"]
        try:
            if fmt == "pfx":
                cert_pem, key_pem = web_proxy.pfx_to_pem(
                    cert_file.read(), form.cleaned_data.get("pfx_password") or None,
                )
            else:
                cert_pem = cert_file.read().decode("utf-8")
                key_pem = form.cleaned_data["key_file"].read().decode("utf-8")
                web_proxy.validate_pem(cert_pem, key_pem)
        except web_proxy.CertError as exc:
            messages.error(request, _("Sertifika hatası: %(e)s") % {"e": str(exc)})
            return redirect("dashboard:admin_web_settings")
        except UnicodeDecodeError:
            messages.error(request, _("PEM dosyaları metin (UTF-8) olmalıdır."))
            return redirect("dashboard:admin_web_settings")

        cn, not_after = web_proxy.cert_metadata(cert_pem)
        ws.manual_cert_pem = cert_pem
        ws.manual_key_pem = key_pem
        ws.manual_cert_subject = cn
        ws.manual_cert_not_after = not_after
        ws.manual_cert_uploaded_at = timezone.now()
        ws.tls_mode = ws.TLS_MANUAL
        ws.updated_by = request.user
        ws.save()
        ok, error = web_proxy.apply(ws)
        if ok:
            messages.success(request, _(
                "Sertifika yüklendi (%(cn)s) ve manuel TLS modu uygulandı."
            ) % {"cn": cn or "—"})
        else:
            messages.error(request, _("Yüklendi ama üretim hatası: %(e)s") % {"e": error})
        return redirect("dashboard:admin_web_settings")


class NotificationCenterView(OperatorRequiredMixin, TemplateView):
    """Yönetici → Bildirim Merkezi (SMS/E-posta).

    Sekme 1: SMS (NetGSM) + e-posta (SMTP) ayarları.
    Sekme 2: seçili kullanıcılara toplu SMS/mail test + hazır mesaj kartları.
    Test + hazır mesaj işlemleri AJAX (api_views); ayar kaydı form POST.
    """
    template_name = "dashboard/admin_pages/notifications.html"

    def get_context_data(self, **kwargs):
        from api.models import MessageTemplate, NotificationSettings
        ns = NotificationSettings.load()
        ctx = super().get_context_data(**kwargs)
        can_edit = user_has_role(self.request.user, ROLE_ADMIN)
        form = ctx.get("settings_form") or NotificationSettingsForm(instance=ns)
        if not can_edit:
            # Operatör salt görüntüleme: tüm ayar alanları kilitli. Django'da
            # disabled=True hem render'da disabled attribute basar hem POST
            # verisini yok sayar (sadece görünür, yazılamaz).
            for field in form.fields.values():
                field.disabled = True
        ctx["settings_form"] = form
        ctx["can_edit"] = can_edit
        ctx["ns"] = ns
        ctx["templates"] = MessageTemplate.objects.all()[:100]
        return ctx

    def post(self, request, *args, **kwargs):
        from api.models import NotificationSettings
        # Ayar kaydı yalnızca yöneticide; operatör salt görüntüler.
        if not user_has_role(request.user, ROLE_ADMIN):
            messages.error(request, _("SMS/E-posta ayarlarını değiştirme yetkiniz yok (salt görüntüleme)."))
            return redirect("dashboard:admin_notifications")
        ns = NotificationSettings.load()
        form = NotificationSettingsForm(request.POST, instance=ns)
        if not form.is_valid():
            ctx = self.get_context_data(settings_form=form)
            return self.render_to_response(ctx)
        ns = form.save(commit=False)
        ns.updated_by = request.user
        ns.save()
        messages.success(request, _("Bildirim ayarları kaydedildi."))
        return redirect("dashboard:admin_notifications")


# ---------------------------------------------------------------------------
# SIM Ayarları (SAIS kabin kayıtları + Bakanlık servisleri)
# ---------------------------------------------------------------------------
class SimSettingsView(OperatorRequiredMixin, TemplateView):
    """Yönetici/Operatör → SIM Ayarları.

    Üç sekme:
    - **Kabin Kayıtları**: ``SaisCabinet`` (Bakanlık SIM ID + erişim bilgileri)
      listeleme + oluştur/güncelle/sil (AJAX modal).
    - **Bakanlık İstasyon Bilgisi**: seçili kabin için Bakanlık
      ``GetStationInformation`` canlı sorgusu.
    - **Şifre Değiştir**: seçili kabin için Bakanlık ``ChangePassword`` —
      başarılı olursa yerel ``auth_secret`` da güncellenir.

    Kabin kayıtları Bakanlık kullanıcı adı/şifresi içerir; bu yüzden şifre
    alanları listede maskelenir, yalnız rol=1/2 erişebilir (OperatorRequiredMixin).
    """
    template_name = "dashboard/admin_pages/sim_settings.html"

    def get_context_data(self, **kwargs):
        from api.models import Station, WebSettings
        from sais_domain.models import SaisCabinet
        ctx = super().get_context_data(**kwargs)
        ctx["cabinets"] = (
            SaisCabinet.objects.select_related("station").order_by("created_at")
        )
        ctx["stations"] = Station.objects.order_by("name")
        # SendHostChanged ön-dolgusu: dış erişim host (WebSettings.domain) + Caddy 443.
        ctx["web_domain"] = (WebSettings.load().domain or "").strip()
        ctx["web_port"] = 443
        return ctx


# ---------------------------------------------------------------------------
# Doküman Yönetimi
# ---------------------------------------------------------------------------
class DocumentListView(RoleRequiredMixin, ListView):
    """Doküman yönetimi — liste + filtre (rapor tarzı).

    Tüm roller listeler ve indirir; yükleme/silme yalnızca operatör (rol=2) ve
    yönetici (rol=1) için (template + ilgili view'lar `OperatorRequiredMixin`).
    """

    model = Document
    template_name = "dashboard/documents/document_list.html"
    context_object_name = "documents"
    paginate_by = None
    MAX_ROWS = 5000

    def get_queryset(self):
        qs = Document.objects.select_related("uploaded_by").all()
        doc_type = self.request.GET.get("doc_type")
        if doc_type:
            qs = qs.filter(doc_type=doc_type)
        return qs.order_by("-created_at")[: self.MAX_ROWS]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["doc_types"] = Document.DocType.choices
        ctx["selected_type"] = self.request.GET.get("doc_type", "")
        ctx["can_manage"] = user_has_role(self.request.user, ROLE_ADMIN, ROLE_OPERATOR)
        ctx["upload_form"] = DocumentUploadForm()
        return ctx


class DocumentUploadView(OperatorRequiredMixin, FormView):
    """Doküman yükleme (operatör + yönetici). Modal form POST hedefi."""

    form_class = DocumentUploadForm
    template_name = "dashboard/documents/document_list.html"
    success_url = reverse_lazy("dashboard:documents")

    def form_valid(self, form):
        doc = form.save(commit=False)
        upload = form.cleaned_data["file"]
        doc.uploaded_by = self.request.user
        doc.original_name = upload.name
        doc.file_size = upload.size
        doc.content_type = getattr(upload, "content_type", "") or ""
        doc.save()
        messages.success(self.request, _("Doküman yüklendi: %(t)s") % {"t": doc.title})
        return super().form_valid(form)

    def form_invalid(self, form):
        # Modal validasyonu — hataları mesaj olarak göster, listeye dön.
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(self.request, err)
        return redirect("dashboard:documents")


class DocumentDownloadView(RoleRequiredMixin, View):
    """Yetki kontrollü dosya indirme (attachment). Tüm roller indirebilir."""

    def get(self, request, pk):
        from django.http import FileResponse, Http404

        doc = Document.objects.filter(pk=pk).first()
        if not doc or not doc.file:
            raise Http404(_("Doküman bulunamadı."))
        try:
            handle = doc.file.open("rb")
        except FileNotFoundError as exc:
            raise Http404(_("Dosya diskte bulunamadı.")) from exc
        filename = doc.original_name or doc.file.name.split("/")[-1]
        response = FileResponse(handle, as_attachment=True, filename=filename)
        if doc.content_type:
            response["Content-Type"] = doc.content_type
        return response


class DocumentDeleteView(OperatorRequiredMixin, View):
    """Doküman silme (operatör + yönetici). POST ile."""

    def post(self, request, pk):
        doc = Document.objects.filter(pk=pk).first()
        if doc:
            title = doc.title
            doc.delete()  # model.delete() diskteki dosyayı da siler
            messages.success(request, _("Doküman silindi: %(t)s") % {"t": title})
        else:
            messages.error(request, _("Doküman bulunamadı."))
        return redirect("dashboard:documents")
