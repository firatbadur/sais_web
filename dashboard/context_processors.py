"""Dashboard template'leri için context processor'lar.

- `menu`: Rol-bazlı filtre edilmiş menü item'ları
- `available_languages`: Dil seçici dropdown için dil listesi
"""
from __future__ import annotations

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .permissions import ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER, user_has_role


# Menü tanımı — tek kaynak. Her item: label, url_name (veya url), icon, roles.
# Gruplu menüler `children` list'i içerir.
MENU = [
    {
        "label": _("Anasayfa"),
        "url_name": "dashboard:home",
        "icon": "duotune/general/gen001.svg",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
    },
    {
        "label": _("Raporlama"),
        "icon": "duotune/general/gen005.svg",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
        "children": [
            {"label": _("Sensör Okumaları"), "url_name": "dashboard:reports_readings",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Aggregate (15dk/Saat/Gün)"), "url_name": "dashboard:reports_aggregates",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Kalibrasyon Geçmişi"), "url_name": "dashboard:reports_calibrations",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Kapanma Geçmişi"), "url_name": "dashboard:reports_power_offs",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Komut Geçmişi"), "url_name": "dashboard:reports_commands",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Sistem Logları"), "url_name": "dashboard:reports_system_logs",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
        ],
    },
    {
        "label": _("Operatör"),
        "icon": "duotune/general/gen019.svg",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR),
        "children": [
            {"label": _("Numune Başlat"), "url_name": "dashboard:operator_sample",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Alarmlar"), "url_name": "dashboard:operator_alarms",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
        ],
    },
    {
        "label": _("Yönetim"),
        "icon": "duotune/general/gen017.svg",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
        "children": [
            {"label": _("İstasyonlar"), "url_name": "dashboard:management_stations",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Bağlantılar"), "url_name": "dashboard:management_connections",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Sensörler"), "url_name": "dashboard:management_sensors",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
        ],
    },
    {
        "label": _("Yönetici"),
        "icon": "duotune/coding/cod001.svg",
        "roles": (ROLE_ADMIN,),
        "children": [
            {"label": _("Sistem Kontrol"), "url_name": "dashboard:admin_system_control",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Kullanıcılar"), "url_name": "dashboard:admin_users",
             "roles": (ROLE_ADMIN,)},
            {"label": _("API Logları"), "url_name": "dashboard:admin_api_logs",
             "roles": (ROLE_ADMIN,)},
        ],
    },
    {
        "label": _("Ayarlar"),
        "icon": "duotune/coding/cod009.svg",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
        "children": [
            {"label": _("Profilim"), "url_name": "dashboard:profile",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Şifre Değiştir"), "url_name": "dashboard:change_password",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Tercihler"), "url_name": "dashboard:preferences",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
        ],
    },
]


def _filter_menu(items, user):
    """Rol filtresinden geçen item'ları seçer; grup child'larını da filtreler."""
    visible = []
    for item in items:
        if not user_has_role(user, *item.get("roles", ())):
            continue
        copy = dict(item)
        children = item.get("children")
        if children:
            filtered_children = _filter_menu(children, user)
            if not filtered_children:
                continue  # tüm child'lar gizliyse üst grup da gösterilmez
            copy["children"] = filtered_children
        # URL'yi çöz (sidebar template'inde url_name yerine tam URL kullansın)
        if "url_name" in copy:
            try:
                copy["url"] = reverse(copy["url_name"])
            except Exception:  # noqa: BLE001 — URL henüz tanımlı olmayabilir
                copy["url"] = "#"
        visible.append(copy)
    return visible


def menu(request):
    """Sidebar partial'ı tarafından kullanılır."""
    return {"dashboard_menu": _filter_menu(MENU, request.user)}


def available_languages(request):
    """Header dil seçici için."""
    return {
        "dashboard_languages": [
            {"code": "tr", "label": "Türkçe", "flag": "tr.svg"},
            {"code": "en", "label": "English", "flag": "us.svg"},
        ],
    }
