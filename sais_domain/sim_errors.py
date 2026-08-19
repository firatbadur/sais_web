"""Bakanlık SIM ``SendData`` sonuçlarını kuyruk kararına indirger.

``sais_domain.sim_outbox`` (store-and-forward kuyruğu) bir gönderim denemesinden
sonra üç şeyden birine karar vermek zorundadır:

* **kabul edildi** → kaydı ``sent`` yap, kuyrukta ilerle,
* **geçici hata** → kuyruğu **BLOKE ET**, backoff ile aynı kaydı tekrar dene
  (kullanıcı gereksinimi: "veriyi kabul edene kadar bir sonraki dakikaların
  verileri iletilmemeli"),
* **kalıcı ret** → sınırlı sayıda dene, sonra ``failed`` damgala ve ilerle
  (tek bozuk dakika sahayı sonsuza dek kilitlemesin).

Bu modül [scada_io/comm_errors.py] ile aynı felsefededir: **saf fonksiyon** —
DB/IO/Django yok, import zinciri yok, kolayca birim test edilir. Karar tek
yerde toplanır; kuyruk motoru, dashboard rozeti ve sistem alarmı aynı tabloyu
okur.

Kategoriler ve sonuç yorumu:

| Kategori       | Tipik kaynak                                   | Sonuç      |
|----------------|------------------------------------------------|------------|
| ``timeout``    | okuma/bağlantı zaman aşımı                     | RETRIABLE  |
| ``conn_error`` | DNS, TCP reddi, TLS, ağ yok                    | RETRIABLE  |
| ``http_5xx``   | Bakanlık sunucu hatası (500/502/503/504)       | RETRIABLE  |
| ``throttled``  | HTTP 429 — hız sınırı                          | RETRIABLE  |
| ``auth``       | login reddi / ticket alınamadı                 | RETRIABLE  |
| ``bad_response``| HTTP 200 ama JSON değil (proxy/hata sayfası)  | RETRIABLE  |
| ``http_4xx``   | Bakanlık isteği reddetti (400/403/404/422...)  | PERMANENT  |
| ``rejected``   | HTTP 200 + zarf ``result: false``              | PERMANENT  |
| ``unknown``    | eşleşmeyen                                     | RETRIABLE  |

**Neden şüphede RETRIABLE?** Bloke etmek veri kaybettirmez (kayıt kuyrukta
durur, 48 saatlik `expired` tavanı zaten emniyet valfidir); yanlışlıkla
PERMANENT demek ise o dakikayı kalıcı olarak çöpe atar.
"""
from __future__ import annotations

from typing import Any, Optional


# --- Sonuç (outcome) sabitleri ---------------------------------------------
ACCEPTED = "accepted"
RETRIABLE = "retriable"
PERMANENT = "permanent"


# --- Kategori sabitleri -----------------------------------------------------
TIMEOUT = "timeout"
CONN_ERROR = "conn_error"
HTTP_5XX = "http_5xx"
THROTTLED = "throttled"
AUTH = "auth"
BAD_RESPONSE = "bad_response"
HTTP_4XX = "http_4xx"
REJECTED = "rejected"
UNKNOWN = "unknown"
OK = "ok"


# Kategori → (Türkçe etiket, sonuç). Dashboard + alarm metni bu tablodan gelir.
CATEGORY_INFO: dict[str, tuple[str, str]] = {
    OK: ("Kabul Edildi", ACCEPTED),
    TIMEOUT: ("Zaman Aşımı", RETRIABLE),
    CONN_ERROR: ("Bağlantı Hatası", RETRIABLE),
    HTTP_5XX: ("Bakanlık Sunucu Hatası (5xx)", RETRIABLE),
    THROTTLED: ("Hız Sınırı (429)", RETRIABLE),
    AUTH: ("Kimlik Doğrulama Hatası", RETRIABLE),
    BAD_RESPONSE: ("Geçersiz Yanıt (JSON değil)", RETRIABLE),
    HTTP_4XX: ("Bakanlık İsteği Reddetti (4xx)", PERMANENT),
    REJECTED: ("Bakanlık Veriyi Reddetti", PERMANENT),
    UNKNOWN: ("Bilinmeyen Hata", RETRIABLE),
}

# Sonuç → Türkçe etiket (UI rozeti).
OUTCOME_LABEL: dict[str, str] = {
    ACCEPTED: "Kabul",
    RETRIABLE: "Geçici (kuyruk bekliyor)",
    PERMANENT: "Kalıcı Ret",
}


def category_label(category: str) -> str:
    """Kategori kodu → Türkçe etiket."""
    return CATEGORY_INFO.get(category, CATEGORY_INFO[UNKNOWN])[0]


def outcome_of(category: str) -> str:
    """Kategori kodu → sonuç (accepted / retriable / permanent)."""
    return CATEGORY_INFO.get(category, CATEGORY_INFO[UNKNOWN])[1]


def blocks_queue(category: str) -> bool:
    """Bu kategori kuyruğu bloke eder mi? (yalnız RETRIABLE bloke eder)"""
    return outcome_of(category) == RETRIABLE


# --- Yardımcılar ------------------------------------------------------------

# Exception metninde aranan ipuçları (küçük harfe indirgenmiş).
_TIMEOUT_HINTS = ("timeout", "timed out", "read timed", "zaman aşımı")
_CONN_HINTS = (
    "connection refused", "connection reset", "connection aborted",
    "connection error", "failed to establish", "name or service not known",
    "temporary failure in name resolution", "nodename nor servname",
    "network is unreachable", "no route to host", "broken pipe",
    "ssl", "certificate", "max retries exceeded", "bağlantı",
)


def _status_category(status: int) -> str:
    """HTTP durum kodu → kategori."""
    if status == 429:
        return THROTTLED
    if status in (401, 403):
        # 401 normalde client içinde re-login ile yutulur; buraya düştüyse
        # login de başarısız demektir → auth (geçici sayılır, bkz. modül notu).
        return AUTH
    if 400 <= status < 500:
        return HTTP_4XX
    if status >= 500:
        return HTTP_5XX
    return UNKNOWN


def _exception_category(exc: BaseException) -> str:
    """İstisna tipi + metninden kategori çıkar (saf metin eşleme)."""
    name = type(exc).__name__
    text = f"{name}: {exc}".lower()

    # requests istisna sınıfları — isimle eşleşir (import zinciri kurmadan).
    if name in ("ConnectTimeout", "ReadTimeout", "Timeout"):
        return TIMEOUT
    if name in ("ConnectionError", "SSLError", "ProxyError", "ChunkedEncodingError"):
        return CONN_ERROR
    if name == "SaisAuthError":
        return AUTH

    if any(h in text for h in _TIMEOUT_HINTS):
        return TIMEOUT
    if any(h in text for h in _CONN_HINTS):
        return CONN_ERROR
    if "json" in text:
        return BAD_RESPONSE
    return UNKNOWN


def envelope_rejected(envelope: Any) -> bool:
    """Bakanlık zarfı açıkça ``result: false`` mi diyor?

    Zarf ``{result, message, objects}`` biçimindedir. ``result`` anahtarı YOKSA
    ret sayılmaz (bazı uçlar yalnız ``objects`` döndürür) — yalnız açıkça
    ``false`` olması reddir.
    """
    if not isinstance(envelope, dict):
        return False
    if "result" not in envelope:
        return False
    return not bool(envelope["result"])


def envelope_message(envelope: Any) -> str:
    """Zarftaki Bakanlık mesajını güvenli biçimde çıkar (yoksa boş string)."""
    if not isinstance(envelope, dict):
        return ""
    msg = envelope.get("message")
    return str(msg) if msg else ""


def classify_send_result(
    *,
    envelope: Any = None,
    exception: Optional[BaseException] = None,
    http_status: Optional[int] = None,
) -> tuple[str, str, str]:
    """Bir ``SendData`` denemesini ``(outcome, category, mesaj)`` üçlüsüne indirger.

    Çağıran şu üç durumdan birini verir:

    * ``exception`` — istek fırlattı (``http_status`` varsa ``SaisResponseError``
      üzerinden gelen HTTP kodu; yoksa ağ/timeout).
    * ``envelope`` — HTTP 200 döndü, Bakanlık zarfı elde (``send_data`` artık
      ``_envelope()`` kullandığı için ``result``/``message`` görünür).
    * ikisi de yok — savunmacı: ``unknown``.
    """
    if exception is not None:
        if http_status is not None:
            category = _status_category(int(http_status))
        else:
            category = _exception_category(exception)
        message = f"{type(exception).__name__}: {exception}"
        return outcome_of(category), category, message

    if http_status is not None and int(http_status) >= 400:
        category = _status_category(int(http_status))
        return outcome_of(category), category, f"Bakanlık HTTP {http_status}"

    if envelope_rejected(envelope):
        msg = envelope_message(envelope) or "Bakanlık veriyi reddetti (result=false)"
        return PERMANENT, REJECTED, msg

    return ACCEPTED, OK, envelope_message(envelope)
