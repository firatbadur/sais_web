"""
Gelen HTTP isteklerini otomatik olarak `ApiLog(direction='in')` olarak
kaydeden middleware. Hassas header/body değerleri maskelenir; streaming
response gövdesi atlanır; log yazımındaki hatalar yutulur.

Outbound (giden) istekler için bkz. `api/api_logging.py`.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from django.conf import settings
from django.http import StreamingHttpResponse

from .api_logging import (
    decode_bytes,
    redact_body,
    redact_headers,
    safe_save,
    truncate,
)


logger = logging.getLogger("api.middleware")


def _skip_paths() -> Iterable[str]:
    return getattr(settings, "API_LOG_SKIP_PATHS", [])


def _is_enabled() -> bool:
    return bool(getattr(settings, "API_LOG_ENABLED", True))


def _client_ip(request) -> str:
    """X-Forwarded-For (ilk IP) varsa onu, yoksa REMOTE_ADDR'ı döner."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _request_headers(request) -> dict:
    """request.META'dan HTTP_* anahtarlarını okuyup gerçek header isimlerine döndür."""
    headers = {}
    for key, value in request.META.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            headers[name] = value
    # Content-Type / Content-Length HTTP_ prefix'i taşımaz, ayrıca al
    if "CONTENT_TYPE" in request.META:
        headers["Content-Type"] = request.META["CONTENT_TYPE"]
    if "CONTENT_LENGTH" in request.META:
        headers["Content-Length"] = request.META["CONTENT_LENGTH"]
    return headers


class ApiLoggingMiddleware:
    """Her gelen isteği ApiLog'a kaydeder."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _is_enabled() or self._should_skip(request.path):
            return self.get_response(request)

        # Body'yi view çalışmadan önce oku (Django/DRF cache'e koyar).
        try:
            raw_body = request.body
        except Exception:  # noqa: BLE001 — bazı multipart durumlarda raise edebilir
            raw_body = b""

        start = time.perf_counter()
        error_message = ""
        response = None
        try:
            response = self.get_response(request)
            return response
        except Exception as exc:  # noqa: BLE001
            error_message = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._record(request, response, raw_body, duration_ms, error_message)

    def process_exception(self, request, exception):
        # Asıl yakalama __call__ içindeki except'tedir; bu sadece güvenlik ağı.
        # Django, process_exception None döndürürse default handler'a iletir.
        return None

    @staticmethod
    def _should_skip(path: str) -> bool:
        for prefix in _skip_paths():
            if path.startswith(prefix):
                return True
        return False

    @staticmethod
    def _record(request, response, raw_body, duration_ms, error_message):
        try:
            user = getattr(request, "user", None)
            user_obj = user if getattr(user, "is_authenticated", False) else None

            response_status = getattr(response, "status_code", None) if response is not None else None
            response_body = ""
            if response is not None and not isinstance(response, StreamingHttpResponse):
                content = getattr(response, "content", b"") or b""
                response_body = decode_bytes(content)

            safe_save(
                direction="in",
                method=(request.method or "")[:10],
                url=(request.path or "")[:2048],
                query_string=redact_body(request.META.get("QUERY_STRING", "") or ""),
                request_headers=redact_headers(_request_headers(request)),
                request_body=redact_body(truncate(decode_bytes(raw_body))),
                response_status=response_status,
                response_body=truncate(response_body),
                duration_ms=duration_ms,
                remote_ip=_client_ip(request) or None,
                user=user_obj,
                user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
                error_message=error_message[:1000],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ApiLog inbound kaydı yazılamadı: %s", exc)
