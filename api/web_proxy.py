"""Web erişim (Caddy reverse proxy) yapılandırma çekirdeği.

`WebSettings` kaydından paylaşılan volume'deki Caddyfile'ı üretir; Caddy `--watch`
ile dosya değişince kendini reload eder (outbound HTTP çağrısı yok). Manuel
sertifika modunda yüklenen PEM/PFX `CADDY_CERT_DIR`'e yazılır.

Tek doğruluk kaynağı `WebSettings` DB kaydıdır; Caddyfile her `apply()` çağrısında
yeniden üretilir (idempotent, restart'a dayanıklı). Generic SCADA infra → api/.
"""
from __future__ import annotations

import logging
import os
import posixpath
import tempfile

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class CertError(Exception):
    """Sertifika okuma/dönüştürme hatası (kullanıcıya gösterilebilir mesaj)."""


# ---------------------------------------------------------------------------
# Atomik dosya yazımı
# ---------------------------------------------------------------------------

def _atomic_write(path: str, content: str, mode: int = 0o644) -> None:
    """Yarım dosya asla görünmesin: temp'e yaz → fsync → os.replace.

    Caddy `--watch` yarı yazılmış Caddyfile'ı okumasın diye aynı dizinde geçici
    dosya + atomik rename kullanılır (api/db_admin.py yaz-sonra-taşı mantığı).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".swp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:  # bazı dosya sistemleri (Windows bind mount) desteklemez
            pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Caddyfile üretimi
# ---------------------------------------------------------------------------

def _reverse_proxy_block(indent: str = "    ") -> str:
    upstream = settings.CADDY_UPSTREAM
    return (
        f"{indent}reverse_proxy {upstream} {{\n"
        f"{indent}    header_up X-Forwarded-Proto {{scheme}}\n"
        f"{indent}    header_up X-Forwarded-Host {{host}}\n"
        f"{indent}    header_up X-Real-IP {{remote_host}}\n"
        f"{indent}}}\n"
    )


def _local_http_block() -> str:
    """Her zaman açık, düz-HTTP yerel erişim (:80).

    Makinedeki operatör domain/SSL hazır olmasa bile dashboard'a
    `http://localhost`/`http://127.0.0.1` ile ulaşabilsin. Caddy host'a göre
    yönlendirir: domain 443'te (HTTPS) kalırken localhost 80'de düz HTTP sunulur
    (Django güvenli-çerez ayarı `DJANGO_COOKIE_SECURE` ile HTTP login'e izin verir).
    """
    return (
        "http://localhost, http://127.0.0.1 {\n"
        + _reverse_proxy_block()
        + "}\n\n"
    )


def render_caddyfile_text(ws) -> str:
    """WebSettings → Caddyfile metni. 3 TLS modu + güvenli internal fallback."""
    domain = (ws.domain or "").strip()
    cert_dir = settings.CADDY_CERT_DIR
    proxy = _reverse_proxy_block()

    # Etkin değil veya domain yok → her zaman geçerli internal fallback (düz IP).
    if not ws.enabled or not domain:
        return (
            "# Otomatik üretildi (api.web_proxy) — web erişimi pasif/eksik.\n"
            "# Dashboard → Yönetici → Web Erişim Ayarları'ndan yapılandırın.\n"
            ":80 {\n"
            f"{proxy}"
            "}\n"
        )

    header = "# Otomatik üretildi (api.web_proxy) — elle düzenlemeyin.\n"

    if ws.tls_mode == ws.TLS_LETSENCRYPT:
        globals_block = ""
        email = (ws.letsencrypt_email or "").strip()
        opts = []
        if email:
            opts.append(f"    email {email}")
        if not ws.http_redirect:
            opts.append("    auto_https disable_redirects")
        if opts:
            globals_block = "{\n" + "\n".join(opts) + "\n}\n\n"
        return (
            header + globals_block
            + _local_http_block()
            + f"{domain} {{\n"
            + proxy
            + "}\n"
        )

    if ws.tls_mode == ws.TLS_MANUAL:
        # Caddyfile her zaman Linux container'da tüketilir → POSIX yol kullan.
        cert = posixpath.join(cert_dir, "cert.pem")
        key = posixpath.join(cert_dir, "key.pem")
        return (
            header
            + _local_http_block()
            + f"{domain} {{\n"
            + f"    tls {cert} {key}\n"
            + proxy
            + "}\n"
        )

    # internal (self-signed)
    return (
        header
        + _local_http_block()
        + f"{domain} {{\n"
        + "    tls internal\n"
        + proxy
        + "}\n"
    )


# ---------------------------------------------------------------------------
# Sertifika işlemleri (PEM doğrulama + PFX→PEM)
# ---------------------------------------------------------------------------

def _load_cryptography():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        return x509, serialization, pkcs12
    except Exception as exc:  # noqa: BLE001
        raise CertError("cryptography kütüphanesi yüklenemedi.") from exc


def cert_metadata(cert_pem: str):
    """PEM sertifikadan (CN, not_after) döndürür. Hatada (\"\", None)."""
    x509, _serialization, _pkcs12 = _load_cryptography()
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        cn = ""
        try:
            attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
            if attrs:
                cn = attrs[0].value
        except Exception:  # noqa: BLE001
            cn = cert.subject.rfc4514_string()
        not_after = cert.not_valid_after_utc
        return cn, not_after
    except Exception:  # noqa: BLE001
        return "", None


def validate_pem(cert_pem: str, key_pem: str) -> None:
    """PEM cert + key parse edilebiliyor mu? Edilemezse CertError."""
    x509, serialization, _pkcs12 = _load_cryptography()
    try:
        x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CertError("Sertifika PEM olarak okunamadı.") from exc
    try:
        serialization.load_pem_private_key(key_pem.encode("utf-8"), password=None)
    except Exception as exc:  # noqa: BLE001
        raise CertError(
            "Özel anahtar PEM olarak okunamadı (şifresiz PKCS8/PEM bekleniyor)."
        ) from exc


def pfx_to_pem(pfx_bytes: bytes, password: str | None):
    """PFX/PKCS#12 → (cert_chain_pem, key_pem). Key şifresiz PKCS8 olarak döner.

    Caddy şifresiz anahtar bekler; chain varsa cert'e eklenir.
    """
    _x509, serialization, pkcs12 = _load_cryptography()
    pw = password.encode("utf-8") if password else None
    try:
        key, cert, add_certs = pkcs12.load_key_and_certificates(pfx_bytes, pw)
    except Exception as exc:  # noqa: BLE001
        raise CertError("PFX okunamadı veya şifre yanlış.") from exc
    if cert is None or key is None:
        raise CertError("PFX içinde sertifika veya özel anahtar bulunamadı.")

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    for extra in (add_certs or []):
        cert_pem += extra.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return cert_pem, key_pem


def write_cert_files(ws) -> None:
    """Manuel modda PEM'leri CADDY_CERT_DIR'e yaz; değilse stale dosyaları sil."""
    cert_dir = settings.CADDY_CERT_DIR
    cert_path = os.path.join(cert_dir, "cert.pem")
    key_path = os.path.join(cert_dir, "key.pem")
    if ws.tls_mode == ws.TLS_MANUAL and ws.manual_cert_pem and ws.manual_key_pem:
        _atomic_write(cert_path, ws.manual_cert_pem, mode=0o644)
        _atomic_write(key_path, ws.manual_key_pem, mode=0o600)
    else:
        for path in (cert_path, key_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Tek entrypoint
# ---------------------------------------------------------------------------

def apply(ws) -> tuple[bool, str]:
    """WebSettings → cert dosyaları + Caddyfile yaz. (ok, error) döndürür.

    Hata boot'u kilitlemesin diye exception yakalanır, `last_render_error`'a
    yazılır. Caddy `--watch` dosya değişiminde reload eder.
    """
    try:
        os.makedirs(settings.CADDY_CERT_DIR, exist_ok=True)
        write_cert_files(ws)
        text = render_caddyfile_text(ws)
        _atomic_write(settings.CADDY_CONFIG_PATH, text, mode=0o644)
        ws.last_rendered_at = timezone.now()
        ws.last_render_error = ""
        ws.save(update_fields=["last_rendered_at", "last_render_error", "updated_at"])
        logger.info("Caddyfile üretildi: %s", settings.CADDY_CONFIG_PATH)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Caddyfile üretilemedi")
        try:
            ws.last_render_error = msg
            ws.save(update_fields=["last_render_error", "updated_at"])
        except Exception:  # noqa: BLE001
            pass
        return False, msg
