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
