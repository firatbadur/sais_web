"""Dashboard form'ları: login, user CRUD, profil, şifre."""
from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _


User = get_user_model()


class DashboardLoginForm(AuthenticationForm):
    """Metronic stili login formu + 'beni hatırla' checkbox'ı."""

    remember_me = forms.BooleanField(
        required=False, initial=False,
        label=_("Beni Hatırla"),
    )

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


class AdminUserCreateForm(UserCreationForm):
    """Admin panelinden user yaratma (rol + is_active)."""

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "rol", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput,)):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")


class AdminUserUpdateForm(forms.ModelForm):
    """Admin user düzenleme — şifre hariç tüm alanlar."""

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "rol", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput,)):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-solid")


class ProfileForm(forms.ModelForm):
    """Kullanıcının kendi profili."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-solid")


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
