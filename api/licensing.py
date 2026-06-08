"""
Lisans çekirdeği — imzalı (Ed25519) saha lisansı doğrulama + enforcement.

Akış:
  - `fetch_and_refresh()`  : LICENSE_URL'den manifest çek → kendi LICENSE_KEY token'ını al
  - `apply_token()`        : Ed25519 imza doğrula + key eşleşmesi + License singleton'a yaz
  - `license_active()`     : gate — imza geçerli VE now <= valid_until (ağsız, tarih bazlı)
  - `license_status_dict()`: dashboard banner / admin sayfası için durum özeti

Enforcement imzalı token'ın `expires_at` tarihine dayanır; gate anında ağ gerekmez.
İmza, müşterinin yerel `License` kaydını düzenleyip süre uzatmasını engeller.

`canonical()` scripts/license_tool.py'deki ile BİREBİR aynı olmalı.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class LicenseError(Exception):
    """Token doğrulama / uygulama hatası."""


def canonical(payload: dict) -> bytes:
    """İmza üzerinde anlaşılan kanonik gösterim (license_tool.py ile senkron)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def verify_token(token: dict) -> dict:
    """Token imzasını gömülü public key ile doğrula; payload döndür.

    `token` = {"payload": {...}, "signature": "<hex>"}. Geçersizse LicenseError.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if not isinstance(token, dict) or "payload" not in token or "signature" not in token:
        raise LicenseError("Token biçimi hatalı (payload/signature yok).")

    pub_hex = (settings.LICENSE_PUBLIC_KEY or "").strip()
    if not pub_hex:
        raise LicenseError("LICENSE_PUBLIC_KEY tanımlı değil.")

    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(bytes.fromhex(token["signature"]), canonical(token["payload"]))
    except (InvalidSignature, ValueError) as exc:
        raise LicenseError(f"İmza doğrulanamadı: {exc}")

    return token["payload"]


def _parse_dt(value: str):
    """ISO tarih → tz-aware datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def apply_token(token: dict, *, source: str = "") -> "object":
    """Token'ı doğrula, kendi anahtarımıza ait mi kontrol et, License'a yaz.

    Geçersiz imza / yanlış key → License.status='invalid' (kilit). Döndürdüğü
    License kaydı güncellenmiş haldedir.
    """
    from api.models import License

    lic = License.load()
    lic.last_checked_at = timezone.now()

    try:
        payload = verify_token(token)
    except LicenseError as exc:
        lic.signature_valid = False
        lic.status = "invalid"
        lic.last_check_ok = False
        lic.last_error = str(exc)
        lic.save()
        logger.warning("Lisans imza doğrulama başarısız (%s): %s", source, exc)
        raise

    expected_key = (settings.LICENSE_KEY or "").strip()
    token_key = (payload.get("key") or "").strip()
    if expected_key and token_key != expected_key:
        lic.signature_valid = True
        lic.status = "invalid"
        lic.last_check_ok = False
        lic.last_error = f"Token anahtarı ({token_key}) bu kurulumun anahtarıyla ({expected_key}) eşleşmiyor."
        lic.save()
        raise LicenseError(lic.last_error)

    valid_until = _parse_dt(payload.get("expires_at"))
    lic.license_key = token_key
    lic.customer = payload.get("customer", "")
    lic.valid_until = valid_until
    lic.issued_at = _parse_dt(payload.get("issued_at"))
    lic.signature_valid = True
    lic.raw_token = token
    lic.last_check_ok = True
    lic.last_error = ""
    lic.status = "active" if (valid_until and timezone.now() <= valid_until) else "expired"
    lic.save()
    logger.info("Lisans uygulandı (%s): key=%s expires=%s", source, token_key, valid_until)
    return lic


def fetch_and_refresh() -> "object":
    """LICENSE_URL manifest'ini çek, kendi token'ımızı bul, uygula.

    Manifest: {"licenses": {"<key>": {payload, signature}, ...}}.
    Ağ/parse hatası License'ı invalid YAPMAZ — sadece last_error kaydeder
    (mevcut imzalı token geçerliliğini korur; offline'da süre dolana dek çalışır).
    """
    import requests

    from api.api_logging import record_outbound_call
    from api.models import License

    url = (settings.LICENSE_URL or "").strip()
    key = (settings.LICENSE_KEY or "").strip()
    if not url or not key:
        lic = License.load()
        lic.last_checked_at = timezone.now()
        lic.last_check_ok = False
        lic.last_error = "LICENSE_URL veya LICENSE_KEY tanımlı değil."
        lic.save()
        return lic

    err = ""
    status_code = None
    body = ""
    try:
        resp = requests.get(url, timeout=15)
        status_code = resp.status_code
        body = resp.text
        resp.raise_for_status()
        manifest = resp.json()
        token = (manifest.get("licenses") or {}).get(key)
        if not token:
            raise LicenseError(f"Manifest'te '{key}' anahtarı yok.")
        return apply_token(token, source="remote")
    except LicenseError:
        raise
    except Exception as exc:  # noqa: BLE001 — ağ/parse hatası: mevcut token korunur
        err = f"{type(exc).__name__}: {exc}"
        lic = License.load()
        lic.last_checked_at = timezone.now()
        lic.last_check_ok = False
        lic.last_error = err
        lic.save()
        logger.warning("Lisans fetch başarısız: %s", err)
        return lic
    finally:
        record_outbound_call(
            method="GET", url=url, response_status=status_code,
            response_body=body, error_message=err, component="license",
        )


def _revalidate_cached(lic) -> bool:
    """Cache'lenmiş raw_token'ı yeniden imza-doğrula (manipülasyon tespiti)."""
    if not lic.raw_token:
        return False
    try:
        verify_token(lic.raw_token)
        return True
    except LicenseError:
        return False


def license_active() -> bool:
    """Gate — bu kurulum çalışmaya yetkili mi?

    - LICENSE_ENFORCE kapalı (dev) → her zaman True.
    - Geçerli imzalı token + now <= valid_until → True.
    - Hiç başarılı kontrol yok ama kurulum yaşı < bootstrap grace → True (yeni saha).
    - Diğer (süre dolmuş / imza geçersiz / yok) → False.
    """
    if not getattr(settings, "LICENSE_ENFORCE", False):
        return True

    from api.models import License

    lic = License.load()
    now = timezone.now()

    if lic.valid_until and lic.raw_token:
        # raw_token imzası hâlâ geçerli mi (DB elle kurcalanmış olabilir)?
        if _revalidate_cached(lic) and now <= lic.valid_until:
            return True
        return False

    # Henüz hiç geçerli token uygulanmadı → yeni kurulum bootstrap grace'i.
    grace_h = int(getattr(settings, "LICENSE_BOOTSTRAP_GRACE_HOURS", 24))
    age = (now - lic.created_at).total_seconds() / 3600.0 if lic.created_at else 0
    return age < grace_h


def license_status_dict() -> dict:
    """Banner / admin sayfası için durum özeti."""
    from api.models import License

    enforce = bool(getattr(settings, "LICENSE_ENFORCE", False))
    lic = License.load()
    active = license_active()
    days = lic.days_remaining()
    warn_days = int(getattr(settings, "LICENSE_WARN_DAYS", 15))

    if not enforce:
        state = "disabled"
    elif active and days is not None and days <= warn_days:
        state = "warning"
    elif active:
        state = "active"
    else:
        state = "expired"

    return {
        "enforce": enforce,
        "active": active,
        "state": state,                 # disabled | active | warning | expired
        "status": lic.status,
        "customer": lic.customer,
        "license_key": lic.license_key,
        "valid_until": lic.valid_until,
        "days_remaining": days,
        "signature_valid": lic.signature_valid,
        "last_checked_at": lic.last_checked_at,
        "last_check_ok": lic.last_check_ok,
        "last_error": lic.last_error,
        "warn_days": warn_days,
    }
