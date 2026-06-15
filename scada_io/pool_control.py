"""Worker connection-pool'unu uzaktan kapatma yardımcıları.

Persistent connection pool (`scada_io.connection_pool`) her Celery worker
process'inin belleğinde (module-global) yaşar; Django web process'inden ya da
ayrı bir `manage.py` komutundan doğrudan erişilemez. Açık TCP/serial socket'leri
kapatmanın tek yolu worker process'inin içinden geçer — bu modül broker
üzerinden tüm worker node'larına broadcast eder:

  - ``close_scada_pool`` control command → solo pool'da (dev) ana process'teki
    pool'u doğrudan kapatır.
  - ``pool_restart`` → prefork pool'da (prod, ``--concurrency=N``) child
    process'leri geri dönüştürür; process ölünce OS açık socket'i kapatır.

İki yöntem de gönderilir; biri ilgisiz pool tipinde no-op olur, zararsızdır.
"""
from __future__ import annotations

import logging
from typing import Optional


logger = logging.getLogger(__name__)


def broadcast_close_pools(reason: str = "manual") -> dict:
    """Tüm worker'lara açık connection pool'larını kapattırır.

    Döner: ``{"workers": int, "closed": int, "error": str|None}``
      - ``workers``: ``close_scada_pool`` komutuna yanıt veren worker sayısı
      - ``closed``: ana process'lerde kapatılan socket sayısı (solo pool'da
        gerçek; prefork'ta socket child'larda olduğu için 0 olabilir ama
        ``pool_restart`` yine de kapatır)
      - ``error``: broadcast başarısızsa hata metni
    """
    from sais_web.celery import app

    summary = {"workers": 0, "closed": 0, "error": None}  # type: dict
    try:
        replies = app.control.broadcast(
            "close_scada_pool",
            arguments={"reason": reason},
            reply=True, timeout=3,
        ) or []
        for reply in replies:
            for _node, payload in reply.items():
                summary["workers"] += 1
                if isinstance(payload, dict):
                    summary["closed"] += int(payload.get("closed") or 0)
    except Exception as exc:  # noqa: BLE001 — broker erişilemez / inspect timeout
        summary["error"] = str(exc)
        logger.warning("broadcast_close_pools: close_scada_pool hatası: %s", exc)
        return summary

    # prefork child'larını geri dönüştür (idle child socket'leri için tek yol).
    try:
        app.control.broadcast("pool_restart", arguments={"reload": False}, reply=False)
    except Exception as exc:  # noqa: BLE001 — solo pool desteklemez, zararsız
        logger.debug("broadcast_close_pools: pool_restart atlandı: %s", exc)

    return summary
