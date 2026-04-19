"""
API log yardımcıları: hassas veri maskeleme, body kısaltma ve giden HTTP
çağrılarını `ApiLog(direction='out')` olarak kaydeden helper'lar.

Gelen istekler için bkz. `api/middleware.py:ApiLoggingMiddleware`.
"""
from __future__ import annotations

import functools
import json
import logging
import re
import time
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from django.conf import settings

from .models import ApiLog


logger = logging.getLogger("api.logging")


# Case-insensitive olarak değeri "[REDACTED]" yapılacak header'lar.
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
}

# Body içinde bu key'lerin değeri maskelenecek (JSON ya da form payload'ları).
SENSITIVE_BODY_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "auth_secret",
    "api_key",
    "apikey",
    "client_secret",
)

# JSON / form gibi payload'larda "key": "value" veya key=value pattern'leri.
_JSON_REDACT_RE = re.compile(
    r'("(?:' + "|".join(SENSITIVE_BODY_KEYS) + r')"\s*:\s*)"[^"]*"',
    re.IGNORECASE,
)
_FORM_REDACT_RE = re.compile(
    r'(\b(?:' + "|".join(SENSITIVE_BODY_KEYS) + r')=)([^&\s]+)',
    re.IGNORECASE,
)


def _max_body_chars() -> int:
    return int(getattr(settings, "API_LOG_MAX_BODY_CHARS", 10000))


def truncate(text: str) -> str:
    """Body metnini API_LOG_MAX_BODY_CHARS limitine indir."""
    if not text:
        return ""
    limit = _max_body_chars()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[TRUNCATED]"


def redact_headers(headers: Mapping[str, Any]) -> str:
    """Header dict'ini hassas değerleri maskeleyerek JSON'a çevir."""
    if not headers:
        return ""
    masked = {}
    for k, v in headers.items():
        masked[k] = "[REDACTED]" if k.lower() in SENSITIVE_HEADERS else str(v)
    try:
        return json.dumps(masked, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(masked)


def redact_body(body: str) -> str:
    """Body içindeki yaygın hassas key değerlerini regex ile maskele."""
    if not body:
        return ""
    body = _JSON_REDACT_RE.sub(r'\1"[REDACTED]"', body)
    body = _FORM_REDACT_RE.sub(r"\1[REDACTED]", body)
    return body


def decode_bytes(raw: Any) -> str:
    """Bytes veya str body'yi güvenli biçimde str'ye çevir."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return repr(raw)
    return str(raw)


def safe_save(**fields: Any) -> Optional[ApiLog]:
    """ApiLog satırı oluştur, hata durumunda yutup logger'a yaz."""
    if not getattr(settings, "API_LOG_ENABLED", True):
        return None
    try:
        return ApiLog.objects.create(**fields)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ApiLog yazılamadı: %s", exc)
        return None


def record_outbound_call(
    *,
    method: str,
    url: str,
    request_headers: Optional[Mapping[str, Any]] = None,
    request_body: Any = "",
    response: Any = None,
    response_status: Optional[int] = None,
    response_body: Any = "",
    duration_ms: Optional[int] = None,
    error_message: str = "",
    component: str = "",
    triggered_by: Any = None,
    retry_count: Optional[int] = None,
) -> Optional[ApiLog]:
    """Bir giden HTTP çağrısını ApiLog(direction='out') olarak kaydeder.

    `response` parametresi `requests.Response` benzeri bir nesne olabilir;
    bu durumda response_status / response_body / response_headers ondan
    çıkarılır. Aksi halde caller doğrudan field'ları doldurur.
    """
    # requests.Response uyumluluğu
    if response is not None:
        if response_status is None:
            response_status = getattr(response, "status_code", None)
        if not response_body:
            response_body = getattr(response, "text", "") or ""

    target_host = ""
    try:
        target_host = urlsplit(url).hostname or ""
    except ValueError:
        target_host = ""

    return safe_save(
        direction="out",
        method=(method or "").upper()[:10],
        url=(url or "")[:2048],
        query_string="",  # outbound URL zaten querystring'i içerir
        request_headers=redact_headers(request_headers or {}),
        request_body=redact_body(truncate(decode_bytes(request_body))),
        response_status=response_status,
        response_body=redact_body(truncate(decode_bytes(response_body))),
        duration_ms=duration_ms,
        error_message=(error_message or "")[:1000],
        target_host=target_host[:255],
        source_component=(component or "")[:100],
        retry_count=retry_count,
        user=triggered_by if getattr(triggered_by, "is_authenticated", False) else None,
    )


def log_outbound_call(
    *,
    component: str = "",
    triggered_by: Any = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Bir fonksiyonun giden HTTP çağrısını otomatik kaydeder.

    Sarılan fonksiyon `requests.Response` döndürmelidir. Network/timeout
    hatası durumunda exception yeniden raise edilir; log satırı yine yazılır
    (response_status=None, error_message=str(exc)).

        @log_outbound_call(component="bakanlik_uploader")
        def upload(payload):
            return requests.post(URL, json=payload, timeout=30)
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            err = ""
            response = None
            try:
                response = fn(*args, **kwargs)
                return response
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                method = url = ""
                req_headers: Mapping[str, Any] = {}
                req_body: Any = ""
                if response is not None and getattr(response, "request", None) is not None:
                    req = response.request
                    method = getattr(req, "method", "") or ""
                    url = getattr(req, "url", "") or ""
                    req_headers = dict(getattr(req, "headers", {}) or {})
                    req_body = getattr(req, "body", "") or ""
                record_outbound_call(
                    method=method,
                    url=url,
                    request_headers=req_headers,
                    request_body=req_body,
                    response=response,
                    duration_ms=duration_ms,
                    error_message=err,
                    component=component,
                    triggered_by=triggered_by,
                )

        return wrapper

    return decorator
