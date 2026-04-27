"""
ApiLog'a otomatik kayıt yapan, ``requests.Session`` tabanlı temel HTTP client.

Tüm SAIS-flavor client'ları (``SaisSimClient``, ``EnvisoftClient``) bu
sınıftan türer ve ``self.request(method, url, ...)`` üzerinden HTTP çağrısı
yapar. Her çağrı (başarı, başarısızlık, exception) ``ApiLog(direction='out')``
satırı oluşturur; çağıran kod ekstra log atmak zorunda kalmaz.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping, Optional

import requests

from api.api_logging import record_outbound_call


logger = logging.getLogger("sais_domain.clients")


class BaseHttpClient:
    """Session reuse + ApiLog entegre HTTP yardımcısı.

    Subclass'lar yalnızca endpoint başına public method tanımlar; transport
    seviyesi (timeout, TLS, log) burada toplanır.
    """

    #: Alt sınıflar override eder; ``ApiLog.source_component`` alanına yazılır.
    component: str = "sais_domain"

    #: Alt sınıflar override eder veya ``__init__`` ile değiştirir.
    default_timeout: int = 50

    def __init__(
        self,
        *,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
    ) -> None:
        self.timeout = timeout if timeout is not None else self.default_timeout
        self.verify_tls = verify_tls
        self.session: requests.Session = requests.Session()

    # ---- Lifecycle ----

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "BaseHttpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- Core request ----

    def request(
        self,
        method: str,
        url: str,
        *,
        log_component: Optional[str] = None,
        triggered_by: Any = None,
        retry_count: Optional[int] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Tek bir HTTP çağrısı; ApiLog satırı her durumda yazılır.

        ``requests.RequestException`` alt sınıfları yutulmaz — caller
        protokol-bağımlı yorumlamayı (401 → re-login vb.) yapabilir.
        """
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify_tls)

        component = log_component or self.component
        t0 = time.perf_counter()
        response: Optional[requests.Response] = None
        err = ""

        try:
            response = self.session.request(method, url, **kwargs)
            return response
        except requests.RequestException as exc:
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("HTTP çağrısı başarısız: %s %s — %s", method, url, err)
            raise
        finally:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            req_method, req_url, req_headers, req_body = _extract_request_meta(
                method, url, kwargs, response
            )
            record_outbound_call(
                method=req_method,
                url=req_url,
                request_headers=req_headers,
                request_body=req_body,
                response=response,
                duration_ms=duration_ms,
                error_message=err,
                component=component,
                triggered_by=triggered_by,
                retry_count=retry_count,
            )


def _extract_request_meta(
    method: str,
    url: str,
    kwargs: Mapping[str, Any],
    response: Optional[requests.Response],
) -> tuple[str, str, Mapping[str, Any], Any]:
    """Log için method/url/headers/body üçlüsünü güvenli biçimde çıkar.

    ``response.request`` mevcutsa (network'e ulaştıysak) onu kullanırız —
    requests'in serialize ettiği son hali; aksi halde kwargs'tan fallback.
    """
    if response is not None and getattr(response, "request", None) is not None:
        req = response.request
        return (
            getattr(req, "method", method) or method,
            getattr(req, "url", url) or url,
            dict(getattr(req, "headers", {}) or {}),
            getattr(req, "body", "") or "",
        )

    # Network'e hiç ulaşamadık — kwargs'tan elimizden geleni topla.
    headers = dict(kwargs.get("headers") or {})
    body: Any = ""
    if "json" in kwargs:
        try:
            body = json.dumps(kwargs["json"], ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            body = str(kwargs["json"])
    elif "data" in kwargs:
        body = str(kwargs["data"])
    return method, url, headers, body
