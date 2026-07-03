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

import hashlib
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


def _read_host_id(path: str) -> str:
    """Salt-okunur mount'lu host kimlik dosyasını oku (strip'li). Yoksa ''."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def runtime_fingerprint() -> str:
    """Bu kurulumun çalıştığı makinenin parmak izi.

    ÖNCELİK — container'a salt-okunur mount edilen HOST kimliği (Linux):
    `/host/machine-id` [+ mümkünse `/host/product_uuid`] → sha256. Böylece parmak
    izi `.env`'den DEĞİL, mount'lu host kimliğinden hesaplanır → müşteri
    `MACHINE_FINGERPRINT`'i düzenleyerek node-lock'u atlatamaz (kopyalanan kuruluma
    karşı gerçek koruma). İhraç (dashboard) ve enforcement aynı fonksiyonu çağırdığı
    için değer kendiliğinden tutarlıdır.

    FALLBACK — mount yoksa (Windows; MachineGuid+baseboard'dan sais-stack.ps1 env'e
    yazar) veya eski kurulum → env `MACHINE_FINGERPRINT`. Boşsa node-lock uygulanmaz.
    """
    mid = _read_host_id("/host/machine-id")
    if mid:
        puuid = _read_host_id("/host/product_uuid")
        return hashlib.sha256(f"{mid}|{puuid}".encode("utf-8")).hexdigest()
    return (getattr(settings, "MACHINE_FINGERPRINT", "") or "").strip()


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
    lic.machine_fingerprint = (payload.get("machine") or "").strip()
    lic.raw_token = token
    lic.last_check_ok = True
    lic.last_error = ""
    # Süre + (varsa) makine-bağlama birlikte değerlendirilir.
    bound = lic.machine_fingerprint
    machine_ok = (not bound) or (runtime_fingerprint() == bound)
    if not (valid_until and timezone.now() <= valid_until):
        lic.status = "expired"
    elif not machine_ok:
        lic.status = "invalid"
        lic.last_error = "Lisans bu makineye kilitli; donanım parmak izi eşleşmiyor."
    else:
        lic.status = "active"
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
            # Manifest'e ULAŞILDI (HTTP 200 + geçerli JSON) ama anahtarımız YOK →
            # bilinçli iptal/kaldırma. Cache'i temizle → license_active anında False
            # (ilişki bitince hızlı kesme). Ağ/HTTP hatası bu noktaya gelmez; o durumda
            # aşağıdaki except cache'i korur (SCADA outage toleransı bozulmaz).
            lic = License.load()
            lic.last_checked_at = timezone.now()
            lic.last_check_ok = True
            lic.signature_valid = False
            lic.raw_token = None
            lic.valid_until = None
            lic.status = "invalid"
            lic.last_error = f"Lisans iptal edilmiş: manifest'te '{key}' anahtarı yok."
            lic.save()
            logger.warning("Lisans iptal (manifest'te anahtar yok): %s", key)
            return lic
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


def _revalidate_cached(lic):
    """Cache'lenmiş raw_token'ı yeniden imza-doğrula; doğrulanmış payload'ı döndür.

    İmza geçerliyse **payload dict**, değilse None. Enforcement değerleri (süre,
    makine kilidi) DB kolonlarından DEĞİL — kurcalanabilir — bu doğrulanmış
    payload'dan okunur. Böylece `UPDATE license SET valid_until=...` gibi DB
    manipülasyonları etkisiz kalır (imza payload'ı kapsar).
    """
    if not lic.raw_token:
        return None
    try:
        return verify_token(lic.raw_token)
    except LicenseError:
        return None


def license_active() -> bool:
    """Gate — bu kurulum çalışmaya yetkili mi?

    - LICENSE_ENFORCE kapalı (dev) → her zaman True.
    - Geçerli imzalı token + now <= imzalı expires_at → True.
    - Hiç token uygulanmamış ama kurulum + imaj yaşı < bootstrap grace → True (yeni saha).
    - Diğer (süre dolmuş / imza geçersiz / yok) → False.
    """
    if not getattr(settings, "LICENSE_ENFORCE", False):
        return True

    from api.models import License

    lic = License.load()
    now = timezone.now()

    if lic.raw_token:
        # İmzayı yeniden doğrula; süre + makine-kilidini İMZALI payload'dan oku
        # (DB `valid_until`/`machine_fingerprint` kolonlarına GÜVENME — kurcalanabilir).
        payload = _revalidate_cached(lic)
        if payload is None:
            return False  # imza geçersiz (raw_token DB'de değiştirilmiş) → kilit
        signed_expiry = _parse_dt(payload.get("expires_at"))
        if not (signed_expiry and now <= signed_expiry):
            return False  # imzalı süre dolmuş
        # Makine-bağlama (node-lock): token kilitliyse çalıştığımız makinenin parmak
        # izi eşleşmeli. FAIL-CLOSED — kilitli ama runtime parmak izi boş/farklıysa reddet.
        bound = (payload.get("machine") or "").strip()
        if bound and runtime_fingerprint() != bound:
            logger.warning("Lisans makine parmak izi eşleşmiyor (kilitli makine ≠ bu makine).")
            return False
        return True

    # Henüz hiç token uygulanmadı → yeni kurulum bootstrap grace'i. Grace hem DB
    # `created_at`'e hem imaj BUILD_EPOCH'una çıpalı: DB'de created_at sıfırlansa
    # bile imaj grace'ten yaşlıysa açılmaz (bkz. _within_bootstrap_grace).
    return _within_bootstrap_grace(lic, now)


# Grace için mutlak tavan (saat) — `.env`'den `LICENSE_BOOTSTRAP_GRACE_HOURS` ile
# devasa değer verip token'sız süresiz çalışma bypass'ını engeller. 744s = 31 gün
# (30 günlük deneme rahat sığar). Bu tavan koddadır (obfuscate edilir), env'den aşılamaz.
MAX_BOOTSTRAP_GRACE_HOURS = 744


def _within_bootstrap_grace(lic, now) -> bool:
    """Yeni kurulum grace penceresi — DB created_at VE imaj build tarihiyle sınırlı.

    Grace = (now - created_at) < grace  VE  (BUILD_EPOCH varsa) (now - build) < grace.
    `UPDATE license SET created_at=NOW()` ile grace sıfırlansa bile imaj build
    tarihinden grace kadar sonra kilitlenir (taze kurulum taze imajla gelir → çalışır).
    Grace `MAX_BOOTSTRAP_GRACE_HOURS` ile tavanlanır → env'den süresiz grace alınamaz.
    """
    grace_h = min(int(getattr(settings, "LICENSE_BOOTSTRAP_GRACE_HOURS", 24)),
                  MAX_BOOTSTRAP_GRACE_HOURS)
    if not lic.created_at:
        return False
    age_h = (now - lic.created_at).total_seconds() / 3600.0
    if age_h >= grace_h:
        return False
    build_epoch = int(getattr(settings, "BUILD_EPOCH", 0) or 0)
    if build_epoch > 0:
        build_age_h = (now.timestamp() - build_epoch) / 3600.0
        if build_age_h >= grace_h:
            return False
    return True


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

    runtime_fp = runtime_fingerprint()
    bound_fp = (lic.machine_fingerprint or "").strip()
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
        # Makine-bağlama (node-lock) durumu — lisans üretirken runtime_fingerprint
        # operatöre gösterilir; bound_fp token'a gömülü kilit.
        "runtime_fingerprint": runtime_fp,
        "machine_fingerprint": bound_fp,
        "machine_locked": bool(bound_fp),
        "fingerprint_ok": (not bound_fp) or (runtime_fp == bound_fp),
    }
