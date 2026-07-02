"""Bildirim gönderim çekirdeği — SMS (NetGSM) + e-posta (dinamik SMTP).

Ayarlar `NotificationSettings` singleton'undan okunur (admin-yönetilir).
Her gönderim `NotificationLog`'a kaydedilir. NetGSM çağrısı `ApiLog(direction='out')`
olarak da loglanır (`log_outbound_call`).

Faz 1: yalnız test paneli kullanır (`kind="test"`). Faz 2 alarm motoru aynı
fonksiyonları `kind="alarm"` ile çağıracak.
"""
from __future__ import annotations

import logging
import re

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import strip_tags

from .api_logging import log_outbound_call
from .models import NotificationLog, NotificationSettings

logger = logging.getLogger(__name__)

# NetGSM yanıt kodları (ilk token). 00/01/02 başarı; diğerleri hata.
NETGSM_CODES = {
    "00": "Başarılı",
    "01": "Başarılı (mesaj zamanlandı)",
    "02": "Başarılı",
    "20": "Mesaj metni / karakter sayısı hatası",
    "30": "Geçersiz kullanıcı kodu/şifre veya API erişim izni yok",
    "40": "Onaysız mesaj başlığı (gönderici adı)",
    "50": "IYS engelli alıcı",
    "51": "IYS gönderici (marka) tanımsız",
    "70": "Hatalı veya eksik parametre",
    "80": "Gönderim sınırı aşıldı",
    "85": "Mükerrer gönderim sınırı",
}

_PHONE_RE = re.compile(r"^5\d{9}$")


def normalize_phone(raw: str) -> str:
    """Telefonu NetGSM formatına (5XXXXXXXXX) indirger.

    +90/0090/90 ön ekleri ve baştaki 0 temizlenir; rakam-dışı karakterler atılır.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0090"):
        digits = digits[4:]
    elif digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


def _log(channel: str, recipient: str, message: str, ok: bool, info: str,
         *, triggered_by=None, kind: str = "test") -> None:
    try:
        NotificationLog.objects.create(
            channel=channel,
            recipient=(recipient or "")[:255],
            message=(message or "")[:500],
            status="ok" if ok else "fail",
            error="" if ok else (info or "")[:500],
            kind=kind,
            sent_by=triggered_by if getattr(triggered_by, "is_authenticated", False) else None,
        )
    except Exception:  # noqa: BLE001 — log yazımı asıl akışı bozmasın
        logger.exception("NotificationLog yazılamadı")


# --------------------------------------------------------------------------- #
# SMS (NetGSM)
# --------------------------------------------------------------------------- #

def send_sms(to: str, message: str, *, triggered_by=None, kind: str = "test") -> tuple[bool, str]:
    """Tek SMS gönderir. (ok, info) döner ve NotificationLog yazar."""
    s = NotificationSettings.load()
    phone = normalize_phone(to)

    if not s.sms_enabled:
        info = "SMS gönderimi kapalı (ayarlardan etkinleştirin)."
        _log("sms", phone or to, message, False, info, triggered_by=triggered_by, kind=kind)
        return False, info

    if not _PHONE_RE.match(phone):
        info = f"Geçersiz telefon: '{to}' (5XXXXXXXXX bekleniyor)."
        _log("sms", phone or to, message, False, info, triggered_by=triggered_by, kind=kind)
        return False, info

    params = {
        "usercode": s.netgsm_usercode,
        "password": s.netgsm_password,
        "gsmno": phone,
        "message": message,
        "msgheader": s.netgsm_header,
    }

    # NetGSM GET endpoint POST gövdesini de kabul eder. Şifre querystring yerine
    # body'de kalsın diye POST kullanıyoruz (ApiLog.url'e plaintext düşmesin).
    @log_outbound_call(component="netgsm", triggered_by=triggered_by)
    def _call():
        return requests.post(s.netgsm_api_url, data=params, timeout=10)

    try:
        resp = _call()
    except Exception as exc:  # noqa: BLE001 — ağ/timeout
        info = f"NetGSM bağlantı hatası: {exc}"
        _log("sms", phone, message, False, info, triggered_by=triggered_by, kind=kind)
        return False, info

    raw = (resp.text or "").strip()
    code = raw.split()[0] if raw else ""
    ok = code in ("00", "01", "02")
    info = NETGSM_CODES.get(code, f"Bilinmeyen NetGSM yanıtı: '{raw[:120]}'")
    _log("sms", phone, message, ok, info, triggered_by=triggered_by, kind=kind)
    return ok, info


# --------------------------------------------------------------------------- #
# E-posta (dinamik SMTP)
# --------------------------------------------------------------------------- #

def build_branded_html(message: str, *, title: str = "SAİS Mail Bildirim Servisi") -> str:
    """Eski yazılımdaki markalı e-posta gövdesi."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
        <h2 style="color:#343f4c">{title}</h2>
        <p>Uyarı: <span style="color:#ff0000"><b>{message}</b></span></p>
        <p style="color:#888;font-size:12px">
            Bu iletinin hatalı olduğunu düşünüyorsanız lütfen bildirin.<br>
            Online Çevre ve Teknolojileri | info@onlinecevre.com.tr
        </p>
    </div>
    """


def send_email(to: str, subject: str, html_body: str, *, text_body: str | None = None,
               triggered_by=None, kind: str = "test",
               attachments: list[tuple[str, bytes, str]] | None = None) -> tuple[bool, str]:
    """Tek e-posta gönderir. (ok, info) döner ve NotificationLog yazar.

    attachments: [(dosya_adı, içerik_bytes, mimetype)] — rapor PDF/Excel ekleri
    gibi ikili dosyalar için (Rapor Stüdyosu `kind="report"` kullanır).
    """
    s = NotificationSettings.load()
    to = (to or "").strip()

    if not s.email_enabled:
        info = "E-posta gönderimi kapalı (ayarlardan etkinleştirin)."
        _log("email", to, subject, False, info, triggered_by=triggered_by, kind=kind)
        return False, info

    if not to or "@" not in to:
        info = f"Geçersiz e-posta: '{to}'."
        _log("email", to, subject, False, info, triggered_by=triggered_by, kind=kind)
        return False, info

    # Dev'de gerçek SMTP'ye gitmeden console'a basmak için bayrak.
    if getattr(settings, "NOTIFICATIONS_FORCE_CONSOLE", False):
        conn = get_connection(backend=settings.EMAIL_BACKEND)
    else:
        conn = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=s.smtp_host, port=s.smtp_port,
            username=s.smtp_user, password=s.smtp_password,
            use_tls=s.smtp_use_tls, fail_silently=False,
        )

    try:
        msg = EmailMultiAlternatives(
            subject or s.mail_subject,
            text_body or strip_tags(html_body),
            s.mail_from, [to], connection=conn,
        )
        msg.attach_alternative(html_body, "text/html")
        for att_name, att_content, att_mimetype in (attachments or []):
            msg.attach(att_name, att_content, att_mimetype)
        msg.send()
    except Exception as exc:  # noqa: BLE001
        info = f"SMTP hatası: {exc}"
        _log("email", to, subject, False, info, triggered_by=triggered_by, kind=kind)
        return False, info

    _log("email", to, subject, True, "Gönderildi", triggered_by=triggered_by, kind=kind)
    return True, "Gönderildi"


# --------------------------------------------------------------------------- #
# Toplu gönderim (test paneli)
# --------------------------------------------------------------------------- #

def send_bulk(recipients, channels, message, *, subject=None, triggered_by=None,
              kind: str = "test") -> list[dict]:
    """Birden çok alıcıya seçili kanallarda gönderir.

    recipients: [{"name","phone","email"}], channels: ["sms","email"] alt kümesi.
    Dönüş: [{name, channel, target, ok, info}].
    """
    results: list[dict] = []
    html = build_branded_html(message)

    for r in recipients:
        name = r.get("name") or r.get("phone") or r.get("email") or "-"
        if "sms" in channels:
            phone = (r.get("phone") or "").strip()
            if phone:
                ok, info = send_sms(phone, message, triggered_by=triggered_by, kind=kind)
            else:
                ok, info = False, "Telefon yok"
            results.append({"name": name, "channel": "sms", "target": phone or "-", "ok": ok, "info": info})
        if "email" in channels:
            email = (r.get("email") or "").strip()
            if email:
                ok, info = send_email(email, subject, html, triggered_by=triggered_by, kind=kind)
            else:
                ok, info = False, "E-posta yok"
            results.append({"name": name, "channel": "email", "target": email or "-", "ok": ok, "info": info})

    return results
