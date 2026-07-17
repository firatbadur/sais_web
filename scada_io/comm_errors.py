"""İletişim (Modbus/ASCII) hata metinlerini kök-neden kategorilerine sınıflandırır.

Reader'lar ve pymodbus, okuma başarısız olduğunda serbest-metin bir hata döndürür
(`ReadResult.error` / `Connection.last_error_message` / scan-group `err`). Bu metin
"bizden mi PLC/ağdan mı" sorusunun cevabını taşır ama ham haliyle üzerine yazılır ve
kategorize edilmez. `classify_comm_error()` metni sabit bir kategori koduna indirger;
teşhis komutu (`diagnose_comm_errors`) ve dashboard bu kategoriler üzerinden istatistik
çıkarır.

Bu modül **saf fonksiyondur** — DB/IO yok, kolayca test edilir. Sınıflandırma yalnız
gözlem içindir; `poll_connection`'ın `status_code` (8/4) atama mantığını DEĞİŞTİRMEZ.

Kategoriler ve kaynak yorumu:

| Kategori          | Anlamı / tipik metin                                  | Kaynak     |
|-------------------|-------------------------------------------------------|------------|
| illegal_address   | ExceptionResponse exception_code=2                    | config (biz)|
| illegal_function  | ExceptionResponse exception_code=1                    | config (biz)|
| illegal_value     | ExceptionResponse exception_code=3                    | config (biz)|
| slave_failure     | ExceptionResponse exception_code=4/5/6 (cihaz iç hata)| device      |
| decode            | "decode: ... register gerektirir", regex eşleşmedi    | config (biz)|
| conn_open         | "bağlantı açılamadı", "failed to connect", port yok   | network     |
| conn_reset        | "connection reset", "broken pipe", "refused"          | network     |
| crc_frame         | "crc", "unable to decode response", "anlaşılmaz"      | line        |
| timeout           | "no response", "timeout", "[input/output]"            | ambiguous   |
| unknown           | eşleşmeyen / boş                                       | unknown     |
"""
from __future__ import annotations

# --- Kategori sabitleri -----------------------------------------------------
ILLEGAL_ADDRESS = "illegal_address"
ILLEGAL_FUNCTION = "illegal_function"
ILLEGAL_VALUE = "illegal_value"
SLAVE_FAILURE = "slave_failure"
DECODE = "decode"
CONN_OPEN = "conn_open"
CONN_RESET = "conn_reset"
CRC_FRAME = "crc_frame"
TIMEOUT = "timeout"
UNKNOWN = "unknown"


# Kaynak yorumu: "config" = bizim yapılandırmamız, "device" = PLC/cihaz,
# "network" = ağ/TCP katmanı, "line" = seri hat/gürültü, "ambiguous" = ikisi de
# olabilir (timeout — yavaş cihaz VEYA kısa timeout/eşzamanlı socket).
SOURCE_CONFIG = "config"
SOURCE_DEVICE = "device"
SOURCE_NETWORK = "network"
SOURCE_LINE = "line"
SOURCE_AMBIGUOUS = "ambiguous"
SOURCE_UNKNOWN = "unknown"

# Kategori → (Türkçe etiket, kaynak). Dashboard/komut bu tabloyu kullanır.
CATEGORY_INFO: dict[str, tuple[str, str]] = {
    ILLEGAL_ADDRESS: ("Geçersiz Adres (cihaz reddetti)", SOURCE_CONFIG),
    ILLEGAL_FUNCTION: ("Geçersiz Fonksiyon (cihaz reddetti)", SOURCE_CONFIG),
    ILLEGAL_VALUE: ("Geçersiz Değer (cihaz reddetti)", SOURCE_CONFIG),
    SLAVE_FAILURE: ("Cihaz İç Hatası", SOURCE_DEVICE),
    DECODE: ("Çözümleme/Parse Hatası", SOURCE_CONFIG),
    CONN_OPEN: ("Bağlantı Açılamadı", SOURCE_NETWORK),
    CONN_RESET: ("Bağlantı Koptu/Reddedildi", SOURCE_NETWORK),
    CRC_FRAME: ("CRC/Çerçeve Hatası", SOURCE_LINE),
    TIMEOUT: ("Zaman Aşımı / Yanıt Yok", SOURCE_AMBIGUOUS),
    UNKNOWN: ("Bilinmeyen", SOURCE_UNKNOWN),
}


def category_label(category: str) -> str:
    """Kategori kodu → Türkçe etiket."""
    return CATEGORY_INFO.get(category, CATEGORY_INFO[UNKNOWN])[0]


def category_source(category: str) -> str:
    """Kategori kodu → kaynak yorumu (config/device/network/line/ambiguous)."""
    return CATEGORY_INFO.get(category, CATEGORY_INFO[UNKNOWN])[1]


def _exception_code(text: str) -> int | None:
    """pymodbus ExceptionResponse metninden `exception_code=N` değerini çıkar.

    Örn: "Modbus hata: ExceptionResponse(dev_id=1, function_code=131,
    exception_code=2)" → 2.
    """
    marker = "exception_code="
    idx = text.find(marker)
    if idx == -1:
        return None
    idx += len(marker)
    digits = ""
    for ch in text[idx:]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


# Kategori → o kategoriye ait alt-metin (substring) anahtar kelimeleri. Sıra
# ÖNEMLİDİR: en spesifik/kesin ayrımlar önce denenir (aşağıdaki ORDER listesi).
_KEYWORDS: dict[str, tuple[str, ...]] = {
    CONN_OPEN: (
        "bağlantı açılamadı", "bağlantı kurulamadı", "kurulamadı", "bağlantı yok",
        "failed to connect", "could not open port", "pyserial kurulu değil",
        "connectionrefusederror", "connection refused", "refused",
    ),
    CONN_RESET: (
        "connection reset", "connectionreseterror", "reset by peer", "broken pipe",
        "brokenpipeerror", "no route", "aborted", "not connected", "disconnected",
        "socket", "10054", "10053", "errno 104", "errno 32", "[connection]",
    ),
    CRC_FRAME: (
        "crc", "checksum", "frame", "unable to decode", "anlaşılmaz response",
        "invalid response", "incomplete",
    ),
    TIMEOUT: (
        "no response", "yanıt yok", "timeout", "timed out", "timeouterror",
        "[input/output]", "cihazdan yanıt",
    ),
    DECODE: (
        "decode:", "register gerektirir", "desteklenmeyen data_type",
        "bit_position", "batch boyut", "aralığı", "regex eşleşmedi", "regex hata",
        "ascii_request boş", "desteklenmeyen function code", "anlaşılmaz",
    ),
}

# Sınıflandırma sırası — üstteki eşleşme kazanır.
_ORDER: tuple[str, ...] = (CONN_OPEN, CONN_RESET, CRC_FRAME, TIMEOUT, DECODE)


def classify_comm_error(error_text: str | None) -> str:
    """Ham iletişim hata metnini bir kategori koduna indirger.

    Boş/None → ``UNKNOWN``. Modbus ExceptionResponse'lar exception_code'a göre
    (illegal_*/slave_failure) ayrılır; kalanlar keyword eşleşmesiyle. Eşleşme
    yoksa ``UNKNOWN``.
    """
    if not error_text:
        return UNKNOWN
    text = str(error_text).lower()

    # 1) Modbus protokol reddi (cihaz cevap verdi, isteği reddetti).
    code = _exception_code(text)
    if code is not None:
        return {
            1: ILLEGAL_FUNCTION,
            2: ILLEGAL_ADDRESS,
            3: ILLEGAL_VALUE,
        }.get(code, SLAVE_FAILURE)  # 4/5/6 (device failure/ack/busy) → cihaz
    # exception_code parse edilemese de isimle geldiyse:
    if "illegal data address" in text or "illegaladdress" in text:
        return ILLEGAL_ADDRESS
    if "illegal function" in text or "illegalfunction" in text:
        return ILLEGAL_FUNCTION
    if "illegal data value" in text or "illegalvalue" in text:
        return ILLEGAL_VALUE

    # 2) Keyword-tabanlı sınıflandırma (sıralı — spesifikten genele).
    for category in _ORDER:
        for kw in _KEYWORDS[category]:
            if kw in text:
                return category

    return UNKNOWN
