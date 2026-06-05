"""Dashboard template filter/tag'ları."""
from __future__ import annotations

from django import template
from django.urls import NoReverseMatch, resolve

from ..permissions import user_has_role


register = template.Library()


@register.simple_tag(takes_context=True)
def menu_active(context, url_name: str, css_class: str = "active") -> str:
    """Mevcut sayfa verilen url_name ile eşleşiyorsa `css_class` döner."""
    request = context.get("request")
    if not request:
        return ""
    try:
        match = resolve(request.path)
    except Exception:  # noqa: BLE001
        return ""
    if match.view_name == url_name or match.url_name == url_name.split(":")[-1]:
        return css_class
    return ""


@register.simple_tag(takes_context=True)
def menu_parent_active(context, group_label: str, css_class: str = "here show") -> str:
    """Grup menüsü — child sayfalarından biri aktifse parent'a `here show` class ekler.

    Şimdilik basit: group_label'ı template'te manuel işaretlenen url_name
    listesi ile karşılaştırmak yerine `current_app` yaklaşımı.
    """
    # Basit implementasyon: url'de group'un path prefix'i varsa aktif say
    request = context.get("request")
    if not request:
        return ""
    # group_label'a karşı gelen path parçaları — örneğin "reports" için /dashboard/reports/
    prefix_map = {
        "reports": "/dashboard/reports/",
        "operator": "/dashboard/operator/",
        "management": "/dashboard/management/",
        "admin": "/dashboard/admin-pages/",
        "settings": "/dashboard/settings/",
    }
    prefix = prefix_map.get(group_label.lower())
    if prefix and request.path.startswith(prefix):
        return css_class
    return ""


@register.filter
def has_role(user, roles_csv: str) -> bool:
    """Template'te: {% if user|has_role:"1,2" %}"""
    try:
        roles = tuple(int(r.strip()) for r in roles_csv.split(",") if r.strip())
    except ValueError:
        return False
    return user_has_role(user, *roles)


@register.simple_tag(takes_context=True)
def querystring_without(context, *keys) -> str:
    """request.GET'i verilen key'leri çıkararak urlencode eder.

    Kullanım: ?{% querystring_without "page" %}&page=2
    Sayfalama linklerinde mevcut filtre parametrelerini koruyup `page`'i
    yeniden yazmaya yarar.
    """
    request = context.get("request")
    if not request:
        return ""
    qd = request.GET.copy()
    for k in keys:
        if k in qd:
            qd.pop(k)
    return qd.urlencode()


@register.filter
def quality_badge(quality: str) -> str:
    """Sensör kalite → Bootstrap/Metronic badge class'ı."""
    return {
        "good": "badge-light-success",
        "bad": "badge-light-danger",
        "uncertain": "badge-light-warning",
        "stale": "badge-light-secondary",
        "substituted": "badge-light-info",
        "manual": "badge-light-primary",
    }.get((quality or "").lower(), "badge-light-secondary")
