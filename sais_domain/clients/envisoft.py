"""
Envisoft SCADA platformu HTTP client'ı.

Bakanlık servisinden ayrı, Envisoft'un kendi entegrasyon uçları:

- ``GET  https://scada.onlinecevre.com.tr/sais-get-diagnostic/``
  istasyondaki olayları log'lamak için (paramsta STATIONID, LOGTURU vb.)
- ``POST https://entegration.onlinecevre.com.tr/SendData``
  ham ölçüm satırlarını içeride aktarmak için.

Her iki uç ``HTTPBasicAuth`` ile korunur ve self-signed sertifika kullanır;
TLS doğrulama default ``False``'tur ama ``ENVISOFT_VERIFY_TLS=1`` ile prod'da
açılabilir. Credentials env var'larında tutulmalı:
``ENVISOFT_USERNAME`` / ``ENVISOFT_PASSWORD``.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

import requests
import urllib3
from django.conf import settings
from requests.auth import HTTPBasicAuth

from sais_domain.models import SaisCabinet

from .base import BaseHttpClient


logger = logging.getLogger("sais_domain.clients.envisoft")


class EnvisoftClient(BaseHttpClient):
    """Envisoft entegrasyon servisleri için HTTP client."""

    DEFAULT_DIAGNOSTIC_URL = "https://scada.onlinecevre.com.tr/sais-get-diagnostic/"
    DEFAULT_SEND_DATA_URL = "https://entegration.onlinecevre.com.tr/SendData"
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/70.0.3538.77 Safari/537.36"
    )
    component = "envisoft"
    default_timeout = 10

    def __init__(
        self,
        cabinet: SaisCabinet,
        *,
        diagnostic_url: Optional[str] = None,
        send_data_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
        verify_tls: Optional[bool] = None,
    ) -> None:
        if verify_tls is None:
            verify_tls = bool(getattr(settings, "ENVISOFT_VERIFY_TLS", False))
        super().__init__(timeout=timeout, verify_tls=verify_tls)
        # Self-signed sertifika uyarısı tek sefer susturulur.
        if not verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.cabinet = cabinet
        self.diagnostic_url = (
            diagnostic_url
            or getattr(settings, "ENVISOFT_DIAGNOSTIC_URL", None)
            or self.DEFAULT_DIAGNOSTIC_URL
        )
        self.send_data_url = (
            send_data_url
            or getattr(settings, "ENVISOFT_SEND_DATA_URL", None)
            or self.DEFAULT_SEND_DATA_URL
        )
        self._username = (
            username or getattr(settings, "ENVISOFT_USERNAME", None) or "envisoft"
        )
        self._password = (
            password or getattr(settings, "ENVISOFT_PASSWORD", None) or "envisoft21"
        )
        self.session.headers["User-Agent"] = self.DEFAULT_USER_AGENT

    @property
    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self._username, self._password)

    # ----------------------------------------------------------- Endpoints

    def send_diagnostic(
        self,
        *,
        log_type: int,
        log_detail: str,
        username: str = "admin",
        triggered_by: Any = None,
    ) -> requests.Response:
        """``GET sais-get-diagnostic`` — Envisoft tarafına olay bildirimi.

        ``log_type`` Envisoft'un kendi log tipi numarasıdır (Bakanlık
        DiagnosticTypeNo'dan ayrı; örn. 126 = numune talep akışı).
        """
        params = {
            "STATIONID": self.cabinet.station_id,
            "STATIONNAME": (
                self.cabinet.station.name if self.cabinet.station_id else ""
            ),
            "LOGTURU": log_type,
            "LOGDETAY": log_detail,
            "KULLANICI": username,
        }
        return self.request(
            "GET",
            self.diagnostic_url,
            params=params,
            auth=self._auth,
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_diagnostic",
        )

    def send_data(
        self,
        rows: Iterable[Any],
        *,
        triggered_by: Any = None,
    ) -> requests.Response:
        """``POST SendData`` — ölçüm satırlarını topluca gönderir.

        ``rows`` orijinal istasyon kodundaki tuple/dict satır formatını
        olduğu gibi aktarır; şema validasyonu yapılmaz, payload server
        tarafında yorumlanır.
        """
        payload = {"data": list(rows)}
        return self.request(
            "POST",
            self.send_data_url,
            json=payload,
            auth=self._auth,
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_data",
        )
