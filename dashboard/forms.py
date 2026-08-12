"""Dashboard form'ları: login, user CRUD, profil, şifre."""
from __future__ import annotations

import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .permissions import ROLE_ADMIN, ROLE_MINISTRY, user_has_role


User = get_user_model()

# Kullanıcı adı: yalnız ASCII harf/rakam + . _ - (Türkçe karakter ve boşluk yasak).
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class _UserFieldRulesMixin:
    """Admin kullanıcı oluşturma/düzenleme formlarının ortak alan kuralları.

    - Kullanıcı adı Türkçe karakter (ç, ğ, ı, ö, ş, ü...) ve boşluk içeremez.
    - E-posta ve telefon zorunludur (bildirim alıcısı çözümü için).
    """

    def _apply_user_field_rules(self):
        self.fields["email"].required = True
        self.fields["phone_number"].required = True
        # Tarayıcı tarafı ipuçları
        self.fields["username"].widget.attrs.setdefault(
            "placeholder", _("ör. ahmet.yilmaz"))
        self.fields["username"].widget.attrs.setdefault(
            "pattern", "[A-Za-z0-9._-]+")
        self.fields["email"].widget.attrs.setdefault(
            "placeholder", "kullanici@ornek.com")
        self.fields["phone_number"].widget.attrs.setdefault(
            "placeholder", "5XXXXXXXXX")

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if username and not USERNAME_RE.match(username):
            raise forms.ValidationError(
                _("Kullanıcı adı yalnızca İngilizce harf, rakam ve . _ - "
                  "karakterlerini içerebilir; Türkçe karakter veya boşluk "
                  "kullanılamaz."),
                code="invalid_username",
            )
        return username


def _restrict_rol_for_operator(form, acting_user):
    """Operatör rol atamasında 'Sistem Yöneticisi' (rol=1) seçemez.

    Yönetici (rol=1 / superuser) tüm rolleri atayabilir; operatör için `rol`
    alanının seçeneklerinden Sistem Yöneticisi çıkarılır. Choice listede
    olmadığından bypass'lı POST'lar da form validasyonunda reddedilir.
    """
    if acting_user is None or user_has_role(acting_user, ROLE_ADMIN):
        return
    rol_field = form.fields.get("rol")
    if rol_field is not None:
        rol_field.choices = [
            c for c in rol_field.choices if str(c[0]) != str(ROLE_ADMIN)
        ]


class DashboardLoginForm(AuthenticationForm):
    """Metronic stili login formu.

    Oturum süresi artık kullanıcının kalıcı tercihi
    (`CustomUser.session_timeout_minutes`) ile belirlenir; login'de
    ayrı bir 'beni hatırla' seçimi yoktur.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Bootstrap/Metronic class'larını form-control olarak ekle
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            else:
                widget.attrs.setdefault("class", "form-control form-control-lg form-control-solid")
        self.fields["username"].widget.attrs["placeholder"] = _("Kullanıcı adı")
        self.fields["password"].widget.attrs["placeholder"] = _("Şifre")

    def confirm_login_allowed(self, user):
        # Bakanlık (rol=4) kullanıcısı yalnızca API erişimi içindir; panele
        # giriş yapamaz — oturum hiç açılmaz (savunma derinliği).
        if getattr(user, "rol", None) == ROLE_MINISTRY:
            raise forms.ValidationError(
                _("Bu hesap yalnızca API erişimi içindir, panele giriş yapamaz."),
                code="ministry_no_login",
            )
        super().confirm_login_allowed(user)


class AdminUserCreateForm(_UserFieldRulesMixin, UserCreationForm):
    """Admin panelinden user yaratma (rol + is_active)."""

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "rol", "is_active",
                  "phone_number", "sms_enabled", "email_enabled")

    def __init__(self, *args, acting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxInput,)):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select form-select-solid")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")
        # Yeni kullanıcıda SMS + e-posta bildirimi varsayılan açık gelsin.
        if not self.is_bound:
            self.fields["sms_enabled"].initial = True
            self.fields["email_enabled"].initial = True
        self._apply_user_field_rules()
        _restrict_rol_for_operator(self, acting_user)


class AdminUserUpdateForm(_UserFieldRulesMixin, forms.ModelForm):
    """Admin user düzenleme — şifre hariç tüm alanlar."""

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "rol", "is_active",
                  "phone_number", "sms_enabled", "email_enabled")

    def __init__(self, *args, acting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxInput,)):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select form-select-solid")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")
        self._apply_user_field_rules()
        _restrict_rol_for_operator(self, acting_user)


class ProfileForm(forms.ModelForm):
    """Kullanıcının kendi profili."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-solid")


class PreferencesForm(forms.ModelForm):
    """Kullanıcı tercihleri — şimdilik oturum süresi.

    Kaydedildiğinde view ayrıca mevcut oturuma `set_expiry()` uygular ki
    değişiklik anında geçerli olsun (bir sonraki login'i beklemeden)."""

    class Meta:
        model = User
        fields = ("session_timeout_minutes",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["session_timeout_minutes"].widget.attrs.setdefault(
            "class", "form-select form-select-solid")


class ChangePasswordForm(forms.Form):
    """Kullanıcı kendi şifresini değiştirir."""

    current_password = forms.CharField(
        label=_("Mevcut Şifre"), widget=forms.PasswordInput,
    )
    new_password = forms.CharField(
        label=_("Yeni Şifre"), widget=forms.PasswordInput, min_length=8,
    )
    confirm_password = forms.CharField(
        label=_("Yeni Şifre (Tekrar)"), widget=forms.PasswordInput,
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-solid")

    def clean_current_password(self):
        pw = self.cleaned_data["current_password"]
        if not self.user.check_password(pw):
            raise forms.ValidationError(_("Mevcut şifre yanlış."))
        return pw

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password") != cleaned.get("confirm_password"):
            self.add_error("confirm_password", _("Yeni şifreler eşleşmiyor."))
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.save()
        return self.user


class WebSettingsForm(forms.ModelForm):
    """Yönetici → Web Erişim Ayarları: domain + TLS modu."""

    class Meta:
        from api.models import WebSettings  # lazy — app yükleme sırası
        model = WebSettings
        fields = ("enabled", "domain", "tls_mode", "http_redirect", "letsencrypt_email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select form-select-solid")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")

    def clean(self):
        cleaned = super().clean()
        # Model.clean() koşullu zorunlulukları uygular; manuel mod sertifika
        # kontrolü (yüklenmiş cert) için instance değerlerini kullanır.
        tls_mode = cleaned.get("tls_mode")
        domain = (cleaned.get("domain") or "").strip()
        if cleaned.get("enabled") and not domain:
            self.add_error("domain", _("Etkin web erişimi için domain zorunludur."))
        if tls_mode == self.instance.TLS_LETSENCRYPT:
            if not domain:
                self.add_error("domain", _("Let's Encrypt için domain zorunludur."))
            if not (cleaned.get("letsencrypt_email") or "").strip():
                self.add_error("letsencrypt_email",
                               _("Let's Encrypt için e-posta zorunludur."))
        elif tls_mode == self.instance.TLS_MANUAL:
            if not (self.instance.manual_cert_pem and self.instance.manual_key_pem):
                self.add_error("tls_mode",
                               _("Manuel mod için önce sertifika + anahtar yükleyin."))
        return cleaned


class NotificationSettingsForm(forms.ModelForm):
    """Yönetici → Bildirim Merkezi: SMS (NetGSM) + e-posta (SMTP) ayarları."""

    class Meta:
        from api.models import NotificationSettings  # lazy
        model = NotificationSettings
        fields = (
            "email_enabled", "smtp_host", "smtp_port", "smtp_use_tls",
            "smtp_user", "smtp_password", "mail_from", "mail_subject",
            "sms_enabled", "netgsm_usercode", "netgsm_password",
            "netgsm_header", "netgsm_api_url",
        )
        widgets = {
            "smtp_password": forms.PasswordInput(render_value=True),
            "netgsm_password": forms.PasswordInput(render_value=True),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select form-select-solid")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("email_enabled"):
            for f in ("smtp_host", "smtp_user", "mail_from"):
                if not (cleaned.get(f) or "").strip():
                    self.add_error(f, _("E-posta etkinken bu alan zorunludur."))
        if cleaned.get("sms_enabled"):
            for f in ("netgsm_usercode", "netgsm_password", "netgsm_header"):
                if not (cleaned.get(f) or "").strip():
                    self.add_error(f, _("SMS etkinken bu alan zorunludur."))
        return cleaned


class CertUploadForm(forms.Form):
    """Manuel sertifika yükleme — PEM (cert+key) veya PFX (+ şifre)."""

    MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB — DoS koruması

    cert_format = forms.ChoiceField(
        label=_("Sertifika Formatı"),
        choices=(("pem", "PEM (cert + key)"), ("pfx", "PFX / PKCS#12")),
        initial="pem",
    )
    cert_file = forms.FileField(label=_("Sertifika / PFX Dosyası"), required=False)
    key_file = forms.FileField(label=_("Özel Anahtar (PEM)"), required=False)
    pfx_password = forms.CharField(
        label=_("PFX Şifresi"), widget=forms.PasswordInput, required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select form-select-solid")
            elif isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.setdefault("class", "form-control form-control-solid")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")

    def _check_size(self, f):
        if f and f.size > self.MAX_FILE_BYTES:
            raise forms.ValidationError(_("Dosya çok büyük (en fazla 1 MB)."))
        return f

    def clean_cert_file(self):
        return self._check_size(self.cleaned_data.get("cert_file"))

    def clean_key_file(self):
        return self._check_size(self.cleaned_data.get("key_file"))

    def clean(self):
        cleaned = super().clean()
        fmt = cleaned.get("cert_format")
        cert_file = cleaned.get("cert_file")
        if not cert_file:
            self.add_error("cert_file", _("Sertifika dosyası zorunludur."))
        if fmt == "pem" and not cleaned.get("key_file"):
            self.add_error("key_file", _("PEM modunda özel anahtar dosyası zorunludur."))
        return cleaned


def _apply_metronic_classes(form):
    """Form alanlarına Metronik solid input class'larını uygular.

    Select → form-select form-select-solid, checkbox → form-check-input,
    diğerleri → form-control form-control-solid.
    """
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault("class", "form-select form-select-solid")
        else:
            widget.attrs.setdefault("class", "form-control form-control-solid")


class ScanGroupForm(forms.ModelForm):
    """Sensör Ayarları → Scan Grubu ekle/düzenle.

    Modbus batch okuma bloğu. Quantity/function limitleri model `clean()`'inde
    zorlanır (function 3/4 → max 125, 1/2 → max 2000)."""

    class Meta:
        from api.models import ScanGroup  # lazy — app yükleme sırası

        model = ScanGroup
        fields = ("connection", "name", "slave_id", "function",
                  "start_address", "quantity", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_metronic_classes(self)
        # FK alanı aranabilir olsun (test/CRUD sayfalarındaki diğer select'lerle tutarlı)
        self.fields["connection"].widget.attrs["data-control"] = "select2"


class SensorConfigForm(forms.ModelForm):
    """Sensör Ayarları → Sensör ekle/düzenle.

    Admin `_SensorAdminForm` ile aynı kapsam: tüm alanlar + `sensor_type`
    zorunlu (model `default=0, blank=True` olduğu için Django default form'da
    required=False çıkıyor). Scan group inheritance + address range model
    `full_clean()`/`clean()` tarafından doğrulanır.
    """

    def __init__(self, *args, **kwargs):
        from api.models import Sensor  # lazy

        super().__init__(*args, **kwargs)
        # sensor_type'ı zorunlu choice yap (admin formuyla aynı davranış)
        self.fields["sensor_type"] = forms.TypedChoiceField(
            choices=[("", _("— Sensör tipi seçin —"))] + list(Sensor.SENSOR_TYPE),
            coerce=int, required=True, label=_("Sensör Tipi"),
        )
        # save_on_change üç-durumlu: boş = varsayılan (oluşturmada dijital için
        # açık, analog için kapalı), aksi halde açıkça Açık/Kapalı korunur.
        self.fields["save_on_change"] = forms.NullBooleanField(
            required=False, label=_("Sadece Değişimde Kaydet"),
            widget=forms.Select(choices=[
                ("", _("Varsayılan (dijital için açık)")),
                ("true", _("Açık")),
                ("false", _("Kapalı")),
            ]),
            help_text=Sensor._meta.get_field("save_on_change").help_text,
        )
        _apply_metronic_classes(self)
        for fk in ("parameter", "connection", "scan_group"):
            self.fields[fk].widget.attrs["data-control"] = "select2"

    class Meta:
        from api.models import Sensor  # lazy

        model = Sensor
        fields = (
            "parameter", "connection", "scan_group",
            "brand", "model", "serial_number", "sensor_type", "signal_type",
            "is_active", "dashboard_hidden", "is_simulated", "sim_min", "sim_max",
            "report_status",
            "slave_id", "function", "address", "quantity",
            "byte_order", "word_order", "bit_position",
            "data_type", "scale", "offset", "decimals", "digital_inverse",
            "ascii_code", "ascii_request", "ascii_response_regex",
            "ascii_line_terminator",
            "poll_interval_sec", "timeout_ms", "retry_count",
            "save_on_change", "deadband", "cov_heartbeat_sec",
        )


class ConnectionForm(forms.ModelForm):
    """Scan Grubu sihirbazı (step 1) — bağlantı oluştur/düzenle.

    Admin `ConnectionAdmin` ile aynı düzenlenebilir alan seti (runtime/meta
    salt-okunur alanlar hariç).
    """

    class Meta:
        from api.models import Connection  # lazy

        model = Connection
        fields = (
            "station", "name", "description", "is_enabled",
            "protocol", "transport", "host", "port",
            "serial_port", "baudrate", "parity", "stop_bits", "byte_size",
            "xonxoff", "rtscts", "dsrdtr",
            "poll_interval_sec", "save_interval_sec", "timeout_ms", "retry_count",
            "auto_reconnect", "reconnect_delay_sec",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_metronic_classes(self)


class GroupSensorForm(forms.ModelForm):
    """Scan Grubu sihirbazı (step 2) — gruba bağlı sensörün özlü formu.

    Admin `_ScanGroupSensorInline` ile aynı alan seti. connection/slave_id/
    function gruptan miras alınır (model `_inherit_from_scan_group`); bu yüzden
    formda yer almaz. `sensor_type` zorunlu (admin formuyla aynı davranış).
    `scan_group` AJAX kaydında zorlanır.
    """

    def __init__(self, *args, **kwargs):
        from api.models import Sensor  # lazy

        super().__init__(*args, **kwargs)
        self.fields["sensor_type"] = forms.TypedChoiceField(
            choices=[("", _("— Sensör tipi seçin —"))] + list(Sensor.SENSOR_TYPE),
            coerce=int, required=True, label=_("Sensör Tipi"),
        )
        _apply_metronic_classes(self)

    class Meta:
        from api.models import Sensor  # lazy

        model = Sensor
        fields = (
            "scan_group", "parameter", "sensor_type", "address", "data_type",
            "quantity", "byte_order", "word_order", "bit_position",
            "scale", "offset", "decimals",
            "is_active", "is_simulated", "sim_min", "sim_max",
        )


class ParameterQuickForm(forms.ModelForm):
    """Sensör sihirbazından (step 3) hızlı parametre oluşturma — modal.

    Yalnız temel alanlar; görünen ad (`parameter_txt`) zorunlu. Kanal/ölçüm
    sınırı gibi ileri alanlar sonradan admin panelinden düzenlenir. Sensör
    formundaki "Parametre" listesine yeni bir kayıt eklemek için kullanılır.
    """

    class Meta:
        from api.models import Parameter  # lazy

        model = Parameter
        fields = (
            "parameter_txt", "parameter_name", "unit",
            "min_range", "max_range", "gec_min", "gec_max",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parameter_txt"].required = True
        _apply_metronic_classes(self)


class ParameterConfigForm(forms.ModelForm):
    """Sensör Ayarları → Parametre Tanımları ekle/düzenle (tam alan seti).

    `ParameterQuickForm`'un (sihirbaz modalı) aksine tüm alanları kapsar:
    kimlik + birim + kanal eşlemesi + geçerli veri / ölçüm / range sınırları.
    Görünen ad (`parameter_txt`) zorunlu; kod adı (`parameter_name`) Bakanlık/
    Envisoft kanal koduyla eşleştiği için benzersizliği burada doğrulanır.
    """

    class Meta:
        from api.models import Parameter  # lazy

        model = Parameter
        fields = (
            "parameter_txt", "parameter_name", "station",
            "unit", "unit_txt",
            "channel_number", "device_channel_id",
            "min_range", "max_range",
            "gec_min", "gec_max",
            "olcum_min", "olcum_max",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parameter_txt"].required = True
        self.fields["parameter_txt"].help_text = _(
            "Kullanıcıya (dashboard/rapor) gösterilen okunabilir ad. Örn. \"Çözünmüş Oksijen\"."
        )
        self.fields["parameter_name"].help_text = _(
            "Kod adı — Bakanlık/Envisoft kanal kodu. Örn. \"CozunmusOksijen\"."
        )
        self.fields["station"].help_text = _(
            "İsteğe bağlı. Parametre kapsamı sensörlerden çözülür; bu alan yalnız "
            "bilgilendirme amaçlıdır."
        )
        _apply_metronic_classes(self)
        self.fields["station"].widget.attrs["data-control"] = "select2"

    def clean_parameter_name(self):
        from api.models import Parameter  # lazy

        value = (self.cleaned_data.get("parameter_name") or "").strip()
        if not value:
            return value
        qs = Parameter.objects.filter(parameter_name__iexact=value)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(_("Bu kod adına sahip başka bir parametre var."))
        return value


class DocumentUploadForm(forms.ModelForm):
    """Doküman yükleme — yalnızca belge dosyalarına izin verir (resim/video yasak)."""

    # İzin verilen belge uzantıları (resim/video ve çalıştırılabilir dosyalar dışlanır).
    ALLOWED_EXTENSIONS = {
        "pdf",
        "doc", "docx", "rtf", "odt", "txt",
        "xls", "xlsx", "ods", "csv",
        "ppt", "pptx", "odp",
    }
    # Açıkça reddedilen tipler (kullanıcıya net mesaj vermek için).
    BLOCKED_EXTENSIONS = {
        "jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "svg", "heic",
        "mp4", "avi", "mov", "mkv", "wmv", "flv", "webm", "mpeg", "mpg", "m4v",
        "mp3", "wav", "ogg", "flac", "exe", "bat", "cmd", "sh", "msi", "scr",
    }
    MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

    class Meta:
        from .models import Document  # noqa: PLC0415 — döngüsel import kaçınma

        model = Document
        fields = ("title", "doc_type", "description", "file")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.ClearableFileInput):
                widget.attrs.setdefault("class", "form-control form-control-solid")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select form-select-solid")
            else:
                widget.attrs.setdefault("class", "form-control form-control-solid")
        # Tarayıcı tarafında da belge tiplerini öner.
        self.fields["file"].widget.attrs["accept"] = (
            ".pdf,.doc,.docx,.rtf,.odt,.txt,.xls,.xlsx,.ods,.csv,.ppt,.pptx,.odp"
        )

    def clean_file(self):
        import os

        f = self.cleaned_data.get("file")
        if not f:
            return f
        if f.size > self.MAX_FILE_BYTES:
            raise forms.ValidationError(_("Dosya çok büyük (en fazla 50 MB)."))
        ext = os.path.splitext(f.name)[1].lstrip(".").lower()
        if ext in self.BLOCKED_EXTENSIONS:
            raise forms.ValidationError(
                _("Resim, video ve ses dosyaları yüklenemez. Yalnızca belge dosyalarına izin verilir.")
            )
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                _("İzin verilmeyen dosya türü (.%(ext)s). İzin verilenler: PDF, Word, Excel, "
                  "PowerPoint, metin/CSV.") % {"ext": ext or "?"}
            )
        return f
