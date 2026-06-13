"""Kimlik doğrulama olay sinyalleri — giriş / çıkış / başarısız giriş.

Django'nun yerleşik auth sinyallerine bağlanır ve her oturum olayını IP bazlı
olarak merkezi olay motifine (`api.events.log_event`) yazar. Hem dashboard hem
admin (Jazzmin) girişleri kapsanır.
"""
from __future__ import annotations

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    from api.events import EventType, log_event

    log_event(
        EventType.LOGIN,
        f"Kullanıcı giriş yaptı: {user.get_username()}",
        severity="info",
        user=user,
        request=request,
    )


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    from api.events import EventType, log_event

    # user None olabilir (zaten anonim) — sessizce geç.
    if user is None:
        return
    log_event(
        EventType.LOGOUT,
        f"Oturum kapatıldı: {user.get_username()}",
        severity="info",
        user=user,
        request=request,
    )


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    from api.events import EventType, log_event

    attempted = (credentials or {}).get("username") or "?"
    log_event(
        EventType.LOGIN_FAILED,
        f"Başarısız giriş denemesi: {attempted}",
        severity="warning",
        username=attempted,
        request=request,
    )
