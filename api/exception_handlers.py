"""Tüm API hatalarını proje sözleşmesine (`{"result", "message", "objects"}`) çeviren
DRF exception handler.

Auth (401), izin (403), 404, validation ve beklenmedik 500 hataları dahil — her uç
nokta hata durumunda da aynı JSON biçiminde yanıt verir. `settings.REST_FRAMEWORK`
içinde `EXCEPTION_HANDLER` olarak kayıtlıdır.
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def _flatten_detail(detail):
    """DRF exc.detail (str / list / dict) → tek satır okunur mesaj."""
    if isinstance(detail, dict):
        parts = []
        for key, value in detail.items():
            msg = _flatten_detail(value)
            # 'non_field_errors' gibi teknik anahtarları kullanıcıya gösterme
            if key in ("non_field_errors", "detail"):
                parts.append(msg)
            else:
                parts.append(f"{key}: {msg}")
        return " ".join(p for p in parts if p)
    if isinstance(detail, (list, tuple)):
        return " ".join(_flatten_detail(item) for item in detail)
    return str(detail)


def api_exception_handler(exc, context):
    """Önce DRF'in kendi handler'ını çalıştır, sonra yanıtı sözleşmeye sar."""
    response = drf_exception_handler(exc, context)

    if response is not None:
        message = _flatten_detail(response.data) or "İstek işlenemedi."
        payload = {
            "result": False,
            "message": message,
            "objects": None,
        }
        # Status kodu (401/403/404 ...) ve WWW-Authenticate gibi header'ları koru
        new_response = Response(payload, status=response.status_code)
        for header, value in response.items():
            if header.lower() != "content-type":
                new_response[header] = value
        return new_response

    # DRF'in tanımadığı (beklenmedik) hata → düz 500 yerine sözleşmeli 500
    logger.exception("Beklenmeyen API hatası", exc_info=exc)
    return Response(
        {
            "result": False,
            "message": "Sunucuda beklenmeyen bir hata oluştu.",
            "objects": None,
        },
        status=500,
    )
