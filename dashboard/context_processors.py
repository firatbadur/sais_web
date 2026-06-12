"""Dashboard template'leri için context processor'lar.

- `menu`: Rol-bazlı filtre edilmiş menü item'ları
- `available_languages`: Dil seçici dropdown için dil listesi
"""
from __future__ import annotations

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .permissions import ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER, user_has_role


# Menü tanımı — tek kaynak. Her item: label, url_name (veya url), icon, roles.
# Gruplu menüler `children` list'i içerir.
MENU = [
    {
        "label": _("Anasayfa"),
        "url_name": "dashboard:home",
        "icon": "ki-home-2",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
    },
    {
        "label": _("Raporlama"),
        "menu_key": "reports",
        "icon": "ki-chart-line-star",
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
            {"label": _("Alarm Raporları"), "url_name": "dashboard:reports_alarms",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
        ],
    },
    {
        "label": _("Operatör"),
        "menu_key": "operator",
        "icon": "ki-notification-bing",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR),
        "children": [
            {"label": _("Numune Senaryosu"), "url_name": "dashboard:operator_sample",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Senaryo Tasarımcı (Demo)"), "url_name": "dashboard:operator_scenario_designer",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Alarmlar"), "url_name": "dashboard:operator_alarms",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
        ],
    },
    {
        "label": _("Yönetim"),
        "menu_key": "management",
        "icon": "ki-wifi",
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
        "menu_key": "admin",
        "icon": "ki-shield-tick",
        "roles": (ROLE_ADMIN,),
        "children": [
            {"label": _("Sistem Kontrol"), "url_name": "dashboard:admin_system_control",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Yedekleme"), "url_name": "dashboard:admin_backups",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Lisans"), "url_name": "dashboard:admin_license",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Web Erişim Ayarları"), "url_name": "dashboard:admin_web_settings",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Bildirim Merkezi"), "url_name": "dashboard:admin_notifications",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Kullanıcılar"), "url_name": "dashboard:admin_users",
             "roles": (ROLE_ADMIN,)},
            {"label": _("API Logları"), "url_name": "dashboard:admin_api_logs",
             "roles": (ROLE_ADMIN,)},
        ],
    },
    {
        "label": _("Ayarlar"),
        "menu_key": "settings",
        "icon": "ki-setting-2",
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


def _default_open_menu(user):
    """Hiçbir grupta değilken (ör. anasayfa) role göre açık gelecek grup.

    rol 1 → Yönetici, rol 2 → Operatör, rol 3 (ve diğer) → Raporlama.
    """
    if getattr(user, "is_superuser", False) or getattr(user, "rol", None) == ROLE_ADMIN:
        return "admin"
    if getattr(user, "rol", None) == ROLE_OPERATOR:
        return "operator"
    return "reports"


def _active_menu_key(request):
    """Mevcut sayfanın bağlı olduğu menü grubunun menu_key'i (yoksa None)."""
    match = getattr(request, "resolver_match", None)
    if not match:
        return None
    current = match.view_name  # ör. "dashboard:reports_readings"
    for group in MENU:
        for child in group.get("children", ()):
            if child.get("url_name") == current:
                return group.get("menu_key")
    return None


def menu(request):
    """Sidebar partial'ı tarafından kullanılır.

    Açık gelecek grup: önce mevcut sayfanın grubu; o bir gruba ait değilse
    (ör. anasayfa) role-bazlı varsayılan grup.
    """
    open_menu = _active_menu_key(request) or _default_open_menu(request.user)
    return {
        "dashboard_menu": _filter_menu(MENU, request.user),
        "default_open_menu": open_menu,
    }


def available_languages(request):
    """Header dil seçici için."""
    return {
        "dashboard_languages": [
            {"code": "tr", "label": "Türkçe", "flag": "tr.svg"},
            {"code": "en", "label": "English", "flag": "us.svg"},
        ],
    }


def app_version(request):
    """Footer'da gösterilen uygulama sürümü (CI build'inde git tag'i)."""
    return {"app_version": settings.APP_VERSION}


def license_status(request):
    """Lisans durumu — base.html'deki uyarı banner'ı için.

    Sadece kimlik doğrulanmış dashboard kullanıcılarında hesaplanır (admin/anonim
    sayfalarda gereksiz sorgu yapmamak için).
    """
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {"license_status": None}
    try:
        from api.licensing import license_status_dict
        return {"license_status": license_status_dict()}
    except Exception:  # noqa: BLE001 — banner asla sayfayı kırmasın
        return {"license_status": None}
