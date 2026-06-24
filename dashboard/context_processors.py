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
            {"label": _("Kalibrasyon Geçmişi"), "url_name": "dashboard:reports_calibrations",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Kapanma Geçmişi"), "url_name": "dashboard:reports_power_offs",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)},
            {"label": _("Komut Geçmişi"), "url_name": "dashboard:reports_commands",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Olaylar"), "url_name": "dashboard:reports_system_logs",
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
            {"label": _("İnteraktif Kalibrasyon"), "url_name": "dashboard:operator_calibration",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Alarmlar"), "url_name": "dashboard:operator_alarms",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Sistem Kontrol"), "url_name": "dashboard:admin_system_control",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Yedekleme"), "url_name": "dashboard:admin_backups",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Bildirim Merkezi"), "url_name": "dashboard:admin_notifications",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Kullanıcılar"), "url_name": "dashboard:admin_users",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
        ],
    },
    {
        "label": _("Sensör Ayarları"),
        "menu_key": "sensorcfg",
        "icon": "ki-abstract-26",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR),
        "children": [
            {"label": _("Bağlantılar"), "url_name": "dashboard:sensorcfg_connections",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Sensörler"), "url_name": "dashboard:sensorcfg_sensors",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
            {"label": _("Sensör Testi"), "url_name": "dashboard:sensorcfg_test",
             "roles": (ROLE_ADMIN, ROLE_OPERATOR)},
        ],
    },
    {
        "label": _("SIM Ayarları"),
        "url_name": "dashboard:admin_sim_settings",
        "icon": "ki-cloud-change",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR),
    },
    {
        "label": _("Takvim Hatırlatıcı"),
        "url_name": "dashboard:reminders",
        "icon": "ki-calendar-tick",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
    },
    {
        "label": _("Dokümanlar"),
        "url_name": "dashboard:documents",
        "icon": "ki-folder",
        "roles": (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER),
    },
    {
        "label": _("Yönetici"),
        "menu_key": "admin",
        "icon": "ki-shield-tick",
        "roles": (ROLE_ADMIN,),
        "children": [
            {"label": _("Mimik Tasarımları"), "url_name": "dashboard:admin_mimic",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Lisans"), "url_name": "dashboard:admin_license",
             "roles": (ROLE_ADMIN,)},
            {"label": _("Web Erişim Ayarları"), "url_name": "dashboard:admin_web_settings",
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

    Açık gelecek grup: yalnızca mevcut sayfanın bağlı olduğu grup. Bir gruba
    ait olmayan sayfalarda (ör. anasayfa) hiçbir grup otomatik açılmaz.
    """
    return {
        "dashboard_menu": _filter_menu(MENU, request.user),
        "default_open_menu": _active_menu_key(request),
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
