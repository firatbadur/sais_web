"""
Çevre ve Şehircilik Bakanlığı SAIS entegrasyon client'ı.

``entegrationsais.csb.gov.tr`` üzerindeki resmi servis kümesini sarar:

- ``POST /Security/login`` — kullanıcı/şifre ile ticket alır
- ``POST /SAIS/SendHostChanged`` — istasyon host + kabin kullanıcı/şifre güncelle
- ``POST /SAIS/GetStationInformation`` — Bakanlık'taki istasyon kayıt bilgisi
- ``POST /SAIS/GetLastData`` — bakanlığa aktarılan en son veri tarihini sorgu
- ``POST /SAIS/GetMissingDates`` — eksik veri pencereleri
- ``POST /SAIS/SendData`` — periyodik ölçüm gönderimi
- ``POST /SAIS/SendDiagnostic`` — serbest metin diagnostic
- ``POST /SAIS/SendDiagnosticWithTypeNo`` — kodlu diagnostic
- ``POST /SAIS/SendCalibration`` — kalibrasyon kaydı
- ``POST /SAIS/SampleRequestStart`` — numune alımı başlatıldı bildirimi
- ``POST /SAIS/SampleRequestError`` — numune alımı hata bildirimi
- ``POST /SAIS/SampleRequestComplete`` — numune alımı tamamlandı bildirimi
- ``POST /SAIS/SampleRequestLimitOver`` — limit aşımı sample code talebi

Authentication akışı:

1. ``login()`` çift-MD5'lenmiş şifreyle ticket alır; ticket ve cookie'ler
   Django cache'ine (``sais:sim:ticket:{cabinet_id}``) yazılır.
2. ``_post_authenticated()`` her çağrıda cache'ten ticket okur; eksikse
   otomatik login eder.
3. Yanıt ``HTTP 401`` ise ticket geçersiz sayılır, bir kere yeniden login
   yapılır ve aynı çağrı retry'lanır (orijinal istasyon kodundaki
   davranışla birebir aynı).

Cabinet başına izole; aynı süreçte birden fazla SaisCabinet için ayrı
``SaisSimClient`` örneği kullanılabilir.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Mapping, Optional
from urllib.parse import urljoin

import requests
from django.conf import settings

from sais_domain.models import SaisCabinet

from .auth import (
    acquire_login_lock,
    clear_session,
    double_md5,
    load_session,
    release_login_lock,
    store_session,
)
from .base import BaseHttpClient
from .exceptions import SaisAuthError, SaisResponseError


logger = logging.getLogger("sais_domain.clients.sim")


class SaisSimClient(BaseHttpClient):
    """Bakanlık SAIS API client'ı (kabin başına bir örnek)."""

    DEFAULT_BASE_URL = "https://entegrationsais.csb.gov.tr"
    DEFAULT_SOFTWARE_VERSION = "EnvisoftV.2"
    component = "sais_sim"
    default_timeout = 50

    # ``GetMissingDates`` zaman zaman çok büyük yanıt döndürür; ayrı timeout.
    MISSING_DATES_TIMEOUT = 180

    def __init__(
        self,
        cabinet: SaisCabinet,
        *,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        software_version: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> None:
        # Timeout + retry saha koşullarına göre `.env`'den ayarlanır; eskiden
        # sabit 50 sn / retry yoktu ve Bakanlık yavaşlayınca dakika kaybediliyordu.
        super().__init__(
            timeout=(
                timeout if timeout is not None
                else int(getattr(settings, "SAIS_SIM_TIMEOUT", self.default_timeout))
            ),
            verify_tls=True,
            connect_timeout=int(getattr(settings, "SAIS_SIM_CONNECT_TIMEOUT", 10)),
            retries=(
                retries if retries is not None
                else int(getattr(settings, "SAIS_SIM_HTTP_RETRIES", 0))
            ),
            # Okuma timeout'u transport'ta TEKRARLANMAZ (Celery limit aritmetiği,
            # bkz. settings.SAIS_SIM_HTTP_READ_RETRIES); kuyruk backoff'u yapar.
            read_retries=int(getattr(settings, "SAIS_SIM_HTTP_READ_RETRIES", 0)),
            backoff=float(getattr(settings, "SAIS_SIM_HTTP_BACKOFF", 2.0)),
            backoff_max=int(getattr(settings, "SAIS_SIM_HTTP_BACKOFF_MAX", 30)),
            retry_statuses=getattr(
                settings, "SAIS_SIM_RETRY_STATUSES", (429, 500, 502, 503, 504),
            ),
        )
        self.cabinet = cabinet
        self.base_url = (
            base_url
            or getattr(settings, "SAIS_SIM_BASE_URL", None)
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.software_version = (
            software_version
            or getattr(settings, "SAIS_SIM_SOFTWARE_VERSION", None)
            or self.DEFAULT_SOFTWARE_VERSION
        )
        # Ağır sorgu uçları (GetMissingDates / GetDataByBetweenTwoDate) zaten
        # 180 sn timeout kullanıyor; oraya normal retry sayısını uygulamak en
        # kötü ~12 dk süren bir istek doğururdu (kullanıcı-tetikli SIM konsolu
        # bunu bekleyemez). Bu yüzden ayrı, düşük-retry'li bir session tutulur.
        self._long_query_retries = int(
            getattr(settings, "SAIS_SIM_LONG_QUERY_RETRIES", 1)
        )
        self._long_session: Optional[requests.Session] = None

    def _get_long_session(self) -> requests.Session:
        """Ağır sorgu uçları için düşük-retry'li session (cookie'ler paylaşılır)."""
        if self._long_session is None:
            sess = requests.Session()
            self._mount_retries(sess, self._long_query_retries)
            # Ticket cookie'leri ana session'la ortak olmalı.
            sess.cookies = self.session.cookies
            self._long_session = sess
        return self._long_session

    def close(self) -> None:
        if self._long_session is not None:
            try:
                self._long_session.close()
            except Exception:  # noqa: BLE001
                pass
            self._long_session = None
        super().close()

    # ---------------------------------------------------------------- Auth

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _hashed_password(self) -> str:
        return double_md5(self.cabinet.auth_secret or "")

    def _restore_cookies(self) -> Optional[str]:
        """Cache'teki cookie'leri session'a geri yükle, ticket'ı döndür."""
        ticket, cookies = load_session(self.cabinet.id)
        if cookies:
            self.session.cookies.update(cookies)
        return ticket

    @staticmethod
    def _auth_headers(ticket: str) -> dict[str, str]:
        # Bakanlık beklediği header: stringified JSON ``{"TicketId": "..."}``.
        return {"AToken": json.dumps({"TicketId": ticket})}

    def login(self, *, triggered_by: Any = None) -> str:
        """Yeni ticket al; cache'i ve session cookie'lerini yenile.

        Başarılı olursa ticket string'i döner; hata durumunda
        ``SaisAuthError`` yükselir.

        **Kilitli:** aynı kabin için eşzamanlı login'ler birbirinin ticket'ını
        geçersiz kılıyordu (``clear_session`` + yarış). Kilidi alamayan çağıran
        kısa süre bekleyip cache'te beliren ticket'ı kullanır; gerçek login'i
        yalnız kilit sahibi yapar.
        """
        if not acquire_login_lock(self.cabinet.id):
            for _ in range(6):
                time.sleep(0.5)
                ticket, cookies = load_session(self.cabinet.id)
                if ticket:
                    if cookies:
                        self.session.cookies.update(cookies)
                    logger.debug(
                        "SAIS login kilidi başkasındaydı; taze ticket kullanıldı "
                        "(cabinet=%s)", self.cabinet.id,
                    )
                    return ticket
            # Kilit sahibi başarısız/yavaş — kendimiz deneriz (kilit TTL ile düşer).
        try:
            return self._do_login(triggered_by=triggered_by)
        finally:
            release_login_lock(self.cabinet.id)

    def _do_login(self, *, triggered_by: Any = None) -> str:
        """Asıl login akışı (kilit sahibi tarafından çağrılır)."""
        clear_session(self.cabinet.id)
        self.session.cookies.clear()

        payload = {
            "username": self.cabinet.auth_username,
            "password": self._hashed_password(),
        }
        try:
            response = self.request(
                "POST",
                self._url("/Security/login"),
                json=payload,
                triggered_by=triggered_by,
                log_component=f"{self.component}.login",
            )
        except requests.RequestException as exc:
            raise SaisAuthError(f"SAIS login network hatası: {exc}") from exc

        if response.status_code != 200:
            raise SaisAuthError(
                f"SAIS login başarısız: HTTP {response.status_code}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise SaisAuthError(f"SAIS login yanıtı JSON değil: {exc}") from exc

        objects = data.get("objects") if isinstance(data, dict) else None
        if not isinstance(objects, dict) or not objects.get("TicketId"):
            # objects=null tipik olarak Bakanlık'ın girişi reddetmesi demek
            # (kullanıcı/şifre veya SIM kayıt bilgisi hatalı / test verisi).
            server_msg = data.get("message") if isinstance(data, dict) else None
            raise SaisAuthError(
                "SAIS login reddedildi — yanıtta TicketId yok "
                "(kullanıcı/şifre veya SIM kayıt bilgileri hatalı olabilir). "
                f"Sunucu mesajı: {server_msg!r}"
            )
        ticket = objects["TicketId"]

        store_session(
            self.cabinet.id,
            ticket=ticket,
            cookies=self.session.cookies.get_dict(),
        )
        logger.info(
            "SAIS login başarılı (cabinet=%s, station=%s)",
            self.cabinet.id, self.cabinet.device_id,
        )
        return ticket

    def _ensure_ticket(self, *, triggered_by: Any = None) -> str:
        ticket = self._restore_cookies()
        if not ticket:
            ticket = self.login(triggered_by=triggered_by)
        return ticket

    def _post_authenticated(
        self,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout: Optional[int] = None,
        triggered_by: Any = None,
        log_component: Optional[str] = None,
        long_query: bool = False,
    ) -> requests.Response:
        """Ticket-yenileme bilen POST sarmalayıcısı.

        İlk çağrı 401 dönerse ticket geçersiz sayılır, yeniden login + tek
        seferlik retry yapılır. ``retry_count`` ApiLog'a yazılır.
        """
        ticket = self._ensure_ticket(triggered_by=triggered_by)
        url = self._url(path)
        kwargs: dict[str, Any] = {"headers": self._auth_headers(ticket)}
        if json_body is not None:
            kwargs["json"] = json_body
        if params is not None:
            kwargs["params"] = dict(params)
        if timeout is not None:
            kwargs["timeout"] = timeout

        sess = self._get_long_session() if long_query else None
        response = self.request(
            "POST", url,
            log_component=log_component,
            triggered_by=triggered_by,
            retry_count=0,
            session=sess,
            **kwargs,
        )
        if response.status_code != 401:
            return response

        # 401 → ticket bayatlamış; bir kere yeniden login + retry.
        logger.info(
            "SAIS 401 — ticket yenileniyor (cabinet=%s, path=%s)",
            self.cabinet.id, path,
        )
        ticket = self.login(triggered_by=triggered_by)
        kwargs["headers"] = self._auth_headers(ticket)
        return self.request(
            "POST", url,
            log_component=log_component,
            triggered_by=triggered_by,
            retry_count=1,
            session=sess,
            **kwargs,
        )

    # ----------------------------------------------------------- Endpoints

    def get_last_data(
        self,
        *,
        period: int = 1,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/GetLastData`` — bakanlığa aktarılan en son veri."""
        params = {"stationId": self.cabinet.device_id, "period": period}
        response = self._post_authenticated(
            "/SAIS/GetLastData",
            params=params,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_last_data",
        )
        return self._unwrap(response)

    def get_missing_dates(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetMissingDates`` — eksik veri pencereleri."""
        params = {"stationId": self.cabinet.device_id}
        response = self._post_authenticated(
            "/SAIS/GetMissingDates",
            params=params,
            timeout=self.MISSING_DATES_TIMEOUT,
            long_query=True,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_missing_dates",
        )
        return self._unwrap(response)

    def send_data(
        self,
        *,
        readtime: str,
        values: Mapping[str, Any],
        period: int = 1,
        software_version: Optional[str] = None,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SendData`` — periyodik ölçüm gönderimi.

        ``values`` parametresi ``{"pH": 7.4, "pH_Status": 1, ...}`` formatında;
        çağıran sensör/parametre eşlemesini yapıp düz dict olarak verir.
        ``Readtime``, ``Stationid``, ``SoftwareVersion``, ``Period`` zarfı
        otomatik eklenir.

        **Dönüş: HAM ZARF** (``{result, message, objects}``) — ``objects``
        açılmaz. Bu uçta anlamlı bilgi ``result``/``message``'dadır; çağıran
        ``sais_domain.sim_errors.classify_send_result`` ile yorumlar.
        """
        payload: dict[str, Any] = {
            "Readtime": readtime,
            "Stationid": self.cabinet.device_id,
            "SoftwareVersion": software_version or self.software_version,
            "Period": period,
        }
        payload.update(values)
        response = self._post_authenticated(
            "/SAIS/SendData",
            json_body=payload,
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_data",
        )
        # ``_unwrap`` DEĞİL ``_envelope``: Bakanlık'ın ret zarfı
        # ``{"result": false, "message": "...", "objects": null}`` biçimindedir;
        # ``_unwrap`` "objects" anahtarını görüp içeriğini (null) döndürdüğü için
        # ret bilgisi YUTULUYORDU → çağıran her HTTP 200'ü kabul sayıyor,
        # "SİM'e son iletim" damgası yanlışlıkla yeşil kalıyordu. Ham zarf
        # döndürülür; kabul/ret ayrımı ``sais_domain.sim_errors`` ile yapılır.
        # (``send_calibration`` / ``send_host_changed`` ile aynı sözleşme.)
        return self._envelope(response)

    def send_diagnostic(
        self,
        *,
        details: str,
        start_date: Optional[str] = None,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SendDiagnostic`` — serbest metin diagnostic kaydı."""
        payload = {
            "stationId": self.cabinet.device_id,
            "details": details,
            "startDate": start_date or _now_iso(),
        }
        response = self._post_authenticated(
            "/SAIS/SendDiagnostic",
            json_body=payload,
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_diagnostic",
        )
        return self._unwrap(response, allow_non_dict=True)

    def send_diagnostic_with_type(
        self,
        *,
        type_no: int,
        details: str,
        start_date: Optional[str] = None,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SendDiagnosticWithTypeNo`` — kodlu diagnostic.

        ``type_no`` Bakanlık tablosundaki resmi tip kodudur; insanca okunan
        karşılığı için bkz. ``diagnostics.diagnostic_detail()``.
        """
        payload = {
            "stationId": self.cabinet.device_id,
            "details": details,
            "DiagnosticTypeNo": type_no,
            "startDate": start_date or _now_iso(),
        }
        response = self._post_authenticated(
            "/SAIS/SendDiagnosticWithTypeNo",
            json_body=payload,
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_diagnostic_with_type",
        )
        return self._unwrap(response, allow_non_dict=True)

    def send_calibration(
        self,
        payload: Mapping[str, Any],
        *,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SendCalibration`` — kalibrasyon kaydı.

        Bakanlık'ın beklediği payload alanları (StationId, DBColumnName,
        Zero*/Span*/Result*) bu katmanda zorlanmaz; çağıran şemayı denetler.
        Çağrı + yanıt ApiLog'a düşer.

        Bu uçta ``objects`` her zaman ``null``'dır; anlamlı bilgi zarfın
        ``result`` (bool) + ``message`` (str) alanlarındadır. Bu yüzden
        ``_unwrap`` yerine **ham zarf** döndürülür ki çağıran Bakanlık'ın
        mesajını kullanıcıya gösterebilsin.
        """
        response = self._post_authenticated(
            "/SAIS/SendCalibration",
            json_body=dict(payload),
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_calibration",
        )
        return self._envelope(response)

    def sample_request_start(
        self,
        sample_code: str,
        *,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SampleRequestStart`` — numune alımı başladı bildirimi."""
        return self._sample_request(
            "/SAIS/SampleRequestStart",
            sample_code,
            triggered_by=triggered_by,
            label="sample_request_start",
        )

    def sample_request_error(
        self,
        sample_code: str,
        *,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SampleRequestError`` — numune alımında hata bildirimi."""
        return self._sample_request(
            "/SAIS/SampleRequestError",
            sample_code,
            triggered_by=triggered_by,
            label="sample_request_error",
        )

    def sample_request_complete(
        self,
        sample_code: str,
        *,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SampleRequestComplete`` — numune alımı tamamlandı bildirimi."""
        return self._sample_request(
            "/SAIS/SampleRequestComplete",
            sample_code,
            triggered_by=triggered_by,
            label="sample_request_complete",
        )

    def get_sample_code(
        self,
        parameter: str,
        *,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SampleRequestLimitOver`` — limit aşımı için sample code talebi."""
        payload = {
            "StationId": self.cabinet.device_id,
            "Parameter": parameter,
        }
        response = self._post_authenticated(
            "/SAIS/SampleRequestLimitOver",
            json_body=payload,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_sample_code",
        )
        return self._unwrap(response)

    def get_station_information(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetStationInformation`` — Bakanlık'taki istasyon kayıt bilgisi.

        Bakanlık SAIS sisteminde bu kabin (``stationId`` = SIM ID) için tanımlı
        istasyon meta verisini döndürür (kod, ad, veri periyodu, kurulum/doğum
        tarihi, adres, firma vb.). Diğer sorgu uçları (``GetLastData`` /
        ``GetMissingDates``) ile aynı ``stationId`` query-param sözleşmesini
        izler; yanıt zarfının ``objects`` alanı olduğu gibi döndürülür.
        """
        params = {"stationId": self.cabinet.device_id}
        response = self._post_authenticated(
            "/SAIS/GetStationInformation",
            params=params,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_station_information",
        )
        return self._unwrap(response)

    # -------------------------------------------------- Sorgu (read-only) uçları
    # Bakanlık → kabin yönünden değil, kabin → Bakanlık yönündeki bilgi/sorgu
    # servisleri. Hepsi POST; çoğu body/param almaz, parametreli olanlar query
    # string ile gider (Bakanlık spec'i ile birebir).

    def get_server_datetime(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetServerDateTime`` — Bakanlık merkez sunucu saati.

        Body/param yok; ``objects`` düz bir ISO tarih-saat string'idir
        (örn. ``"2020-10-17T10:18:12"``)."""
        response = self._post_authenticated(
            "/SAIS/GetServerDateTime",
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_server_datetime",
        )
        return self._unwrap(response, allow_non_dict=True)

    def get_channel_information(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetChannelInformationByStationId`` — Bakanlık'taki kanal listesi."""
        params = {"stationId": self.cabinet.device_id}
        response = self._post_authenticated(
            "/SAIS/GetChannelInformationByStationId",
            params=params,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_channel_information",
        )
        return self._unwrap(response)

    def get_parameters(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetParameters`` — geçerli parametre adları + tipleri (body yok)."""
        response = self._post_authenticated(
            "/SAIS/GetParameters",
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_parameters",
        )
        return self._unwrap(response)

    def get_units(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetUnits`` — birim kimlik + adları (body yok)."""
        response = self._post_authenticated(
            "/SAIS/GetUnits",
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_units",
        )
        return self._unwrap(response)

    def get_data_between(
        self,
        *,
        period: int = 1,
        start_date: str,
        end_date: str,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/GetDataByBetweenTwoDate`` — iki tarih arası gönderilmiş veri.

        ``start_date`` / ``end_date`` Bakanlık'ın beklediği
        ``"YYYY-MM-DD HH:MM:SS"`` (boşluklu) formatında string olmalı."""
        params = {
            "stationId": self.cabinet.device_id,
            "period": period,
            "startDate": start_date,
            "endDate": end_date,
        }
        response = self._post_authenticated(
            "/SAIS/GetDataByBetweenTwoDate",
            params=params,
            timeout=self.MISSING_DATES_TIMEOUT,
            long_query=True,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_data_between",
        )
        return self._unwrap(response)

    def get_data_status_descriptions(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetDataStatusDescription`` — geçerli veri durum kodları.

        Bu uç istisnai olarak yanıtı **düz dizi** döndürür (``{result, message,
        objects}`` zarfı yok); ``_unwrap`` dict olmayan gövdeyi olduğu gibi geri
        verir."""
        response = self._post_authenticated(
            "/SAIS/GetDataStatusDescription",
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_data_status_descriptions",
        )
        return self._unwrap(response, allow_non_dict=True)

    def get_diagnostic_types(self, *, triggered_by: Any = None) -> Any:
        """``/SAIS/GetDiagnosticTypes`` — diagnostik tip kodları (body yok)."""
        response = self._post_authenticated(
            "/SAIS/GetDiagnosticTypes",
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_diagnostic_types",
        )
        return self._unwrap(response)

    def get_calibration(
        self,
        *,
        start_date: str,
        end_date: str,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/GetCalibration`` — iki tarih arası merkeze gönderilen kalibrasyonlar.

        ``start_date`` / ``end_date`` ``"YYYY-MM-DD"`` veya
        ``"YYYY-MM-DD HH:MM:SS"`` string olabilir (Bakanlık her ikisini kabul eder)."""
        params = {
            "stationId": self.cabinet.device_id,
            "startDate": start_date,
            "endDate": end_date,
        }
        response = self._post_authenticated(
            "/SAIS/GetCalibration",
            params=params,
            triggered_by=triggered_by,
            log_component=f"{self.component}.get_calibration",
        )
        return self._unwrap(response)

    def send_host_changed(
        self,
        *,
        connection_user: str,
        connection_password: str,
        domain_address: str,
        port: Any,
        triggered_by: Any = None,
    ) -> Any:
        """``/SAIS/SendHostChanged`` — istasyon host + kabin kullanıcı/şifre güncelle.

        Bakanlık merkezi yazılımındaki kayıtlı dış-erişim bilgilerini günceller:
        kabin yazılımı kullanıcı adı/şifresi (``ConnectionUser`` /
        ``ConnectionPassword``) ve dış erişim host/port (``ConnectionDomainAddress``
        / ``ConnectionPort``). Bunlar Bakanlık'ın kabin yazılımına bağlanırken
        kullandığı bilgilerdir (bizim ``GetStationInformation`` yanıtımızdaki
        alanlarla birebir).

        Şifre **düz metin** gönderilir (bu uç login değil; spec body örneği düz
        ``ConnectionPassword`` bekler). Çağıran başarı (HTTP 200 + ``result=true``)
        durumunda yerel ``SaisCabinet.auth_username``/``auth_secret``'i senkronlar.

        Anlamlı bilgi zarfın ``result``/``message`` alanlarındadır → ``_unwrap``
        yerine **ham zarf** döndürülür (``send_calibration`` ile aynı sözleşme).
        """
        payload = {
            "StationId": self.cabinet.device_id,
            "ConnectionUser": connection_user,
            "ConnectionPassword": connection_password,
            "ConnectionDomainAddress": domain_address,
            "ConnectionPort": str(port),
        }
        response = self._post_authenticated(
            "/SAIS/SendHostChanged",
            json_body=payload,
            triggered_by=triggered_by,
            log_component=f"{self.component}.send_host_changed",
        )
        return self._envelope(response)

    # ------------------------------------------------------------ Helpers

    def _sample_request(
        self,
        path: str,
        sample_code: str,
        *,
        triggered_by: Any,
        label: str,
    ) -> Any:
        payload = {
            "StationId": self.cabinet.device_id,
            "SampleCode": sample_code,
        }
        response = self._post_authenticated(
            path,
            json_body=payload,
            triggered_by=triggered_by,
            log_component=f"{self.component}.{label}",
        )
        return self._unwrap(response, allow_non_dict=True)

    @staticmethod
    def _envelope(response: requests.Response) -> Any:
        """Bakanlık ``{result, message, objects}`` zarfını **ham** döndür.

        ``objects``'i açmaz — ``SendCalibration`` gibi ``objects=null`` dönüp
        anlamı ``result``/``message``'da taşıyan uçlar içindir. HTTP hatası veya
        JSON olmayan yanıtta ``SaisResponseError`` yükselir.
        """
        if response.status_code >= 400:
            raise SaisResponseError(
                f"Bakanlık HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=response.text or "",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SaisResponseError(
                f"Bakanlık yanıtı JSON değil (HTTP {response.status_code})",
                status_code=response.status_code,
                response_text=response.text or "",
            ) from exc

    @staticmethod
    def _unwrap(
        response: requests.Response,
        *,
        allow_non_dict: bool = False,
    ) -> Any:
        """Bakanlık ``{result, message, objects}`` zarfını aç.

        - ``allow_non_dict=False`` (default): yanıt dict değilse veya
          ``objects`` anahtarı yoksa ``SaisResponseError`` yükselir.
        - ``allow_non_dict=True``: bazı endpoint'ler (SendData, sample
          bildirim akışı) sadece ``{"result": true}`` dönebilir; yanıtın
          tamamı ham haliyle döndürülür.
        """
        if response.status_code >= 400:
            raise SaisResponseError(
                f"Bakanlık HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=response.text or "",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SaisResponseError(
                f"Bakanlık yanıtı JSON değil (HTTP {response.status_code})",
                status_code=response.status_code,
                response_text=response.text or "",
            ) from exc
        if not isinstance(payload, dict):
            return payload
        if "objects" in payload:
            return payload["objects"]
        if allow_non_dict:
            return payload
        raise SaisResponseError(
            "Bakanlık yanıtında 'objects' alanı yok",
            status_code=response.status_code,
            response_text=response.text or "",
        )


def _now_iso() -> str:
    """Bakanlık'ın beklediği yerel saat ISO formatı (saniye dahil)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
