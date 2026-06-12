from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "rol",
        "is_staff",
        "is_active",
        "created_at",
    )
    list_filter = ("rol", "is_staff", "is_superuser", "is_active", "isDark",
                   "sms_enabled", "email_enabled")
    search_fields = ("username", "email", "first_name", "last_name", "device_id", "phone_number")
    ordering = ("-created_at",)
    readonly_fields = ("last_login", "date_joined", "created_at")

    fieldsets = BaseUserAdmin.fieldsets + (
        (_("SAIS"), {
            "fields": ("rol", "isDark", "device_id", "added_by", "created_at"),
        }),
        (_("Bildirim"), {
            "fields": ("phone_number", "sms_enabled", "email_enabled"),
        }),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (_("SAIS"), {
            "fields": ("rol", "isDark", "device_id"),
        }),
        (_("Bildirim"), {
            "fields": ("phone_number", "sms_enabled", "email_enabled"),
        }),
    )
