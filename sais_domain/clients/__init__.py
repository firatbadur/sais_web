"""
SAIS alan-özel HTTP entegrasyon client'ları.

İki dış sistemle konuşan, ApiLog'a otomatik kayıt yapan client paketleri:

- ``SaisSimClient`` — Çevre ve Şehircilik Bakanlığı SAIS entegrasyon servisi
  (``entegrationsais.csb.gov.tr``); ticket-based authentication, login +
  veri/diagnostic/numune endpoint'leri.
- ``EnvisoftClient`` — Envisoft SCADA platformu
  (``scada.onlinecevre.com.tr`` / ``entegration.onlinecevre.com.tr``);
  HTTPBasicAuth ile diagnostic + veri gönderimi.

Tüm giden HTTP çağrıları ``api.api_logging.record_outbound_call``
üzerinden ``ApiLog(direction='out')`` olarak loglanır; hassas header
ve body içerikleri merkezi maskeleme kurallarına uyar.
"""
from .auth import double_md5
from .base import BaseHttpClient
from .diagnostics import diagnostic_detail
from .envisoft import EnvisoftClient
from .exceptions import (
    SaisAuthError,
    SaisClientError,
    SaisResponseError,
)
from .sim import SaisSimClient

__all__ = [
    "BaseHttpClient",
    "EnvisoftClient",
    "SaisAuthError",
    "SaisClientError",
    "SaisResponseError",
    "SaisSimClient",
    "diagnostic_detail",
    "double_md5",
]
