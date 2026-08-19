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
from typing import Any, Iterable, Mapping, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

    #: Bağlantı kurma (TCP+TLS) timeout'u. ``None`` → tek-değer timeout kullanılır.
    default_connect_timeout: Optional[int] = None

    #: Transport seviyesi tekrar denemesi. Varsayılan 0 → davranış eskisi gibi
    #: (tek deneme); alt sınıflar/çağıran açıkça artırır.
    default_retries: int = 0
    #: **Okuma** timeout'unun transport'ta tekrarı — bilinçli olarak 0.
    #: Sunucu bağlantıyı kabul edip yanıt vermiyorsa her tekrar tam timeout
    #: kadar bloklar; tekrarı burada yapmak tek çağrıyı dakikalarca uzatır ve
    #: Celery task limitini aşar. Bu tekrar çağıran katmana (kuyruk backoff'u)
    #: aittir. Bağlantı kurulamaması ve 5xx ise ucuzdur → burada tekrarlanır.
    default_read_retries: int = 0
    #: urllib3 backoff factor — bekleme ``backoff * 2**(deneme-1)`` saniye.
    default_backoff: float = 2.0
    #: urllib3 backoff tavanı (sn).
    default_backoff_max: int = 30
    #: Bu HTTP kodlarında istek tekrarlanır (geçici sunucu hataları).
    default_retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)

    def __init__(
        self,
        *,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        connect_timeout: Optional[int] = None,
        retries: Optional[int] = None,
        read_retries: Optional[int] = None,
        backoff: Optional[float] = None,
        backoff_max: Optional[int] = None,
        retry_statuses: Optional[Iterable[int]] = None,
    ) -> None:
        read_timeout = timeout if timeout is not None else self.default_timeout
        conn_timeout = (
            connect_timeout if connect_timeout is not None
            else self.default_connect_timeout
        )
        # ``requests`` (connect, read) tuple'ını doğrudan destekler.
        self.timeout: Any = (
            (conn_timeout, read_timeout) if conn_timeout else read_timeout
        )
        self.read_timeout = read_timeout
        self.connect_timeout = conn_timeout
        self.verify_tls = verify_tls
        self.retries = (
            int(retries) if retries is not None else int(self.default_retries)
        )
        self.read_retries = (
            int(read_retries) if read_retries is not None
            else int(self.default_read_retries)
        )
        self.backoff = (
            float(backoff) if backoff is not None else float(self.default_backoff)
        )
        self.backoff_max = (
            int(backoff_max) if backoff_max is not None
            else int(self.default_backoff_max)
        )
        self.retry_statuses = tuple(
            retry_statuses if retry_statuses is not None
            else self.default_retry_statuses
        )
        self.session: requests.Session = requests.Session()
        self._mount_retries(self.session, self.retries)

    # ---- Retry / transport ----

    def _build_adapter(self, retries: int) -> HTTPAdapter:
        """Verilen tekrar sayısıyla bir ``HTTPAdapter`` üretir.

        **POST açıkça izinlidir.** urllib3 varsayılan olarak POST'u idempotent
        saymaz ve tekrarlamaz; ama bu entegrasyondaki yazma uçları
        (``SendData``) ``Stationid`` + ``Readtime`` ile **idempotenttir** —
        aynı dakika tekrar gönderilince Bakanlık üzerine yazar (eksik veri
        yeniden gönderim servisi zaten bu varsayımla çalışıyor). Yanıtı hiç
        alamadığımız bir timeout'ta tekrar denemek bu yüzden güvenlidir.
        """
        read_retries = min(self.read_retries, retries)
        kwargs: dict[str, Any] = dict(
            # ``total`` diğerlerinin üst sınırı; connect + status tekrarına izin
            # verecek kadar yüksek tutulur.
            total=retries,
            connect=retries,
            read=read_retries,
            status=retries,
            status_forcelist=list(self.retry_statuses),
            backoff_factor=self.backoff,
            # 5xx tükendiğinde istisna fırlatma — SON YANITI ver ki ApiLog'a
            # düşsün ve çağıran HTTP kodunu sınıflandırabilsin.
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        try:
            retry = Retry(allowed_methods=frozenset(["GET", "POST"]), **kwargs)
        except TypeError:  # urllib3 < 1.26 uyumluluğu
            retry = Retry(method_whitelist=frozenset(["GET", "POST"]), **kwargs)
        try:
            retry.backoff_max = self.backoff_max
        except Exception:  # noqa: BLE001 — sürüm farkı; tavan yoksa da çalışır
            pass
        return HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)

    def _mount_retries(self, session: requests.Session, retries: int) -> None:
        adapter = self._build_adapter(retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

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
        session: Optional[requests.Session] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Tek bir HTTP çağrısı; ApiLog satırı her durumda yazılır.

        ``requests.RequestException`` alt sınıfları yutulmaz — caller
        protokol-bağımlı yorumlamayı (401 → re-login vb.) yapabilir.

        ``session`` verilirse o kullanılır (alt sınıflar farklı retry
        politikalı ikinci bir session tutabilsin diye; bkz. SaisSimClient'ın
        ağır sorgu session'ı).
        """
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify_tls)
        sess = session if session is not None else self.session

        component = log_component or self.component
        t0 = time.perf_counter()
        response: Optional[requests.Response] = None
        err = ""

        try:
            response = sess.request(method, url, **kwargs)
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
                retry_count=_effective_retry_count(response, retry_count),
            )


def _effective_retry_count(
    response: Optional[requests.Response],
    declared: Optional[int],
) -> Optional[int]:
    """ApiLog'a yazılacak gerçek deneme sayısını çıkarır.

    urllib3 ``Retry`` tekrarları ``session.request`` **içinde** olduğu için tek
    bir ``ApiLog`` satırı yazılır; kaç kez denendiği aksi halde kaybolurdu.
    ``response.raw.retries.history`` transport seviyesindeki tekrarları taşır;
    çağıranın bildirdiği (``declared``, ör. 401 → re-login tekrarı) sayıyla
    toplanır. Bilgi yoksa ``declared`` aynen döner.
    """
    transport = 0
    try:
        retries = getattr(getattr(response, "raw", None), "retries", None)
        history = getattr(retries, "history", None)
        if history:
            transport = len(history)
    except Exception:  # noqa: BLE001 — log yolu asla patlamamalı
        transport = 0
    if not transport:
        return declared
    return transport + (declared or 0)


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
