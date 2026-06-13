"""Merkezi olay (event) kayıt motifi — gerçek SCADA audit trail.

Sistemdeki TÜM olaylar tek bir giriş noktasından (`log_event`) `SystemLog`
tablosuna yazılır:

- Kullanıcı hareketleri: giriş / çıkış / başarısız giriş (IP bazlı)
- Manuel işlemler: dijital output Start/Stop, kullanıcı yönetimi, ayar değişikliği
- Otomatik komut yürütme sonucu (completed / failed)
- Dijital giriş/çıkış (digital input/output) durum değişimleri
- Yedekleme / lisans / yapılandırma olayları

Yeni bir olay kaynağı eklemek için tek yapılması gereken ilgili noktadan
``log_event(EventType.X, "açıklama", request=request, severity=...)`` çağırmaktır.
Bu fonksiyon ASLA exception fırlatmaz — log yazımı uygulamanın asıl işini
(polling, komut, web isteği) kesintiye uğratmaz.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EventType:
    """Olay kategorileri. Değer = ``LogType.name`` (get_or_create ile eşlenir)."""

    LOGIN = "Kullanıcı Girişi"
    LOGOUT = "Oturum Kapatma"
    LOGIN_FAILED = "Başarısız Giriş"
    COMMAND = "Komut"
    DIGITAL_IO = "Dijital Giriş/Çıkış"
    CONFIG = "Yapılandırma"
    USER_MGMT = "Kullanıcı Yönetimi"
    BACKUP = "Yedekleme / Geri Yükleme"
    LICENSE = "Lisans"
    SCENARIO = "Numune Senaryosu"
    CALIBRATION = "Kalibrasyon"
    SYSTEM = "Sistem"


# Önem dereceleri — SystemLog.SEVERITY_* ile aynı.
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


# LogType nesnelerini process-local cache'le (her olayda DB lookup yapmamak için).
# Lookup tablosu nadiren değişir; yeni kategori get_or_create ile eklenir.
_LOGTYPE_CACHE: dict[str, object] = {}


def _get_logtype(name: str):
    lt = _LOGTYPE_CACHE.get(name)
    if lt is None:
        from api.models import LogType
        lt, _ = LogType.objects.get_or_create(name=name)
        _LOGTYPE_CACHE[name] = lt
    return lt


def client_ip(request) -> str | None:
    """İstek için gerçek istemci IP'sini döner (reverse proxy farkında)."""
    if request is None:
        return None
    meta = getattr(request, "META", {}) or {}
    xff = meta.get("HTTP_X_FORWARDED_FOR") or meta.get("HTTP_X_REAL_IP")
    if xff:
        # "client, proxy1, proxy2" → ilk (gerçek istemci)
        return xff.split(",")[0].strip() or None
    return meta.get("REMOTE_ADDR") or None


def log_event(
    event_type: str,
    description: str = "",
    *,
    severity: str = SEVERITY_INFO,
    user=None,
    username: str | None = None,
    ip_address: str | None = None,
    station=None,
    request=None,
):
    """Tek bir olay kaydı oluştur.

    `request` verilirse kullanıcı + IP otomatik doldurulur (açıkça verilmemişse).
    Hata olsa bile sessizce yutar — çağıran akış asla kırılmaz.

    Dönen değer: oluşturulan ``SystemLog`` örneği veya hata durumunda ``None``.
    """
    try:
        from api.models import SystemLog

        if request is not None:
            req_user = getattr(request, "user", None)
            if user is None and req_user is not None and getattr(req_user, "is_authenticated", False):
                user = req_user
            if ip_address is None:
                ip_address = client_ip(request)

        if user is not None and not getattr(user, "pk", None):
            # Authenticated olmayan (AnonymousUser) → FK'ya yazma, ad korunur.
            if not username:
                username = getattr(user, "get_username", lambda: "")() or None
            user = None
        elif user is not None and not username:
            username = user.get_username()

        return SystemLog.objects.create(
            type=_get_logtype(event_type),
            severity=severity if severity in {SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_CRITICAL} else SEVERITY_INFO,
            user=user,
            username=(username or "")[:150] or None,
            ip_address=ip_address,
            station=station,
            description=(description or "")[:1000],
        )
    except Exception:  # noqa: BLE001 — olay kaydı asıl akışı kesmesin
        logger.exception("log_event yazılamadı: type=%s", event_type)
        return None
