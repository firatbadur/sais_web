"""
Bakanlık SAIS dakikalık veri (``GetDataByBetweenTwoDate``) yardımcıları.

Status kodu kataloğu + parametre tespiti + **doğrulanmış (``_N``) veriye göre**
geçerli sayım mantığı tek yerde. Hem dashboard görünüm katmanı
(``dashboard.api_views``) hem de günde 1 çalışan istatistik job'u
(``sais_domain.tasks.compute_sim_valid_stats``) buradan beslenir — dashboard'a
bağımlı değildir (katman ihlali olmaz).

Geçerli veri oranı **yalnız validasyon sonucu (``{Param}_N_Status``)** üzerinden
hesaplanır; ham (``{Param}_Status``) yalnız ``_N`` hiç yoksa fallback'tir.
"""
from __future__ import annotations

from typing import Any, Iterable


# Bakanlık veri durum kodları (GetDataStatusDescription çıktısı ile birebir).
# ``valid`` = Bakanlık ``IsValid`` bayrağı (geçerli veri sayımına dahil mi).
SIM_DATA_STATUS_CODES: list[dict[str, Any]] = [
    {"code": 0, "name": "VeriYok", "desc": "Veri Yok", "valid": False},
    {"code": 1, "name": "VeriGecerli", "desc": "Veri Geçerli", "valid": True},
    {"code": 4, "name": "Gecersiz", "desc": "Geçersiz", "valid": False},
    {"code": 7, "name": "KalLimitDisi", "desc": "Kalibrasyon Limit Dışı", "valid": False},
    {"code": 8, "name": "IletisimHatasi", "desc": "İletişim Hatası", "valid": False},
    {"code": 9, "name": "SistemKal", "desc": "Sistem Kalibrasyon", "valid": False},
    {"code": 12, "name": "Alarm", "desc": "Alarm", "valid": False},
    {"code": 15, "name": "Purge", "desc": "Purge", "valid": True},
    {"code": 19, "name": "KalHatasi", "desc": "Kalibrasyon Hatası", "valid": False},
    {"code": 21, "name": "AkisYok", "desc": "Akış ölçerde hata var", "valid": False},
    {"code": 22, "name": "DesarjYok", "desc": "Deşarj Yok", "valid": False},
    {"code": 23, "name": "Yikama", "desc": "Yıkama", "valid": True},
    {"code": 24, "name": "HaftalikYikama", "desc": "Haftalık Yıkama", "valid": True},
    {"code": 25, "name": "IstasyonBakimda", "desc": "İstasyon Bakımda", "valid": False},
    {"code": 26, "name": "TesisBakimda", "desc": "Tesis Bakımda", "valid": False},
    {"code": 30, "name": "Cihaz Bakımda", "desc": "Cihaz Bakımda", "valid": False},
    {"code": 31, "name": "Debi Arızası", "desc": "Debi Arızası", "valid": True},
    {"code": 35, "name": "Nokta1Kalibrasyon", "desc": "1. Nokta Kalibrasyonu", "valid": False},
    {"code": 36, "name": "Nokta2Kalibrasyon", "desc": "2. Nokta Kalibrasyonu", "valid": False},
    {"code": 39, "name": "OlcumAraligiDisinda", "desc": "Ölçüm aralığı dışında", "valid": False},
    {"code": 200, "name": "Eksik/Geçersiz Yıkama", "desc": "Eksik veya Geçersiz Yıkama", "valid": False},
    {"code": 201, "name": "Eksik/Geçersiz Haftalık Yıkama", "desc": "Eksik veya Geçersiz Haftalık Yıkama", "valid": False},
    {"code": 202, "name": "Geçersiz/Eksik Aylık Kalibrasyon", "desc": "Geçersiz veya Eksik Aylık Kalibrasyon", "valid": False},
    {"code": 203, "name": "Geçersiz Akış Hızı", "desc": "Geçersiz Akış Hızı Değeri", "valid": False},
    {"code": 204, "name": "Geçersiz Debi", "desc": "Geçersiz Debi Değeri", "valid": False},
    {"code": 205, "name": "Tekrar Veri", "desc": "Tekrar Veri", "valid": False},
    {"code": 206, "name": "Geçersiz Birim", "desc": "Geçersiz Birim", "valid": False},
]

STATUS_BY_CODE: dict[int, dict[str, Any]] = {s["code"]: s for s in SIM_DATA_STATUS_CODES}
VALID_CODES: set[int] = {s["code"] for s in SIM_DATA_STATUS_CODES if s["valid"]}

# Bakanlık parametre anahtarı → (görünen ad, birim). Bilinmeyen anahtar key adıyla
# gösterilir. Doküman §6.1 parametre/birim tablosu ile uyumlu.
SIM_PARAM_META: dict[str, tuple[str, str]] = {
    "AKM": ("AKM", "mg/l"),
    "CozunmusOksijen": ("Çözünmüş Oksijen", "mg/l"),
    "Debi": ("Debi", "m³/dk"),
    "KOi": ("KOİ", "mg/l"),
    "KOI": ("KOİ", "mg/l"),
    "pH": ("pH", "--"),
    "Sicaklik": ("Sıcaklık", "°C"),
    "Iletkenlik": ("İletkenlik", "mS/cm"),
    "AkisHizi": ("Akış Hızı", "m/sn"),
    "HariciDebi": ("Harici Debi", "m³/dk"),
    "DesarjDebi": ("Deşarj Debi", "m³/dk"),
}

# Veri satırındaki ölçüm-dışı (audit/meta) anahtarlar.
META_KEYS: set[str] = {
    "id", "created", "createdby", "changed", "changedby",
    "Stationid", "StationId", "SoftwareVersion",
}


def is_valid_code(code: Any) -> bool:
    """Bir status kodu geçerli veri sayılıyor mu (Bakanlık ``IsValid``)."""
    return code in VALID_CODES


def detect_params(rows: Iterable[dict]) -> list[str]:
    """Veri satırlarından temel parametre anahtarlarını sıralı tespit eder.

    ``{Param}`` (taban) anahtarlarını döndürür; ``_Status``, ``_N``,
    ``_N_Status`` ve meta/audit anahtarları hariç. Sıra ilk satırın anahtar
    sırasını korur."""
    params: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            if k in META_KEYS or k in ("ReadTime", "Period"):
                continue
            if k.endswith("_Status") or k.endswith("_N"):
                continue
            if k not in seen:
                seen.add(k)
                params.append(k)
        if params:
            break
    return params


def validated_status(row: dict, param_key: str) -> Any:
    """Bir parametrenin **doğrulanmış** status kodu (``{Param}_N_Status``).

    Doğrulama sonucu yoksa ham ``{Param}_Status``'a düşer (bazı parametrelerde
    ``_N`` varyantı hiç olmayabilir)."""
    nkey = param_key + "_N_Status"
    return row[nkey] if nkey in row else row.get(param_key + "_Status")


def valid_counts(rows: Iterable[dict], params: list[str]) -> dict[str, int]:
    """Her parametre için **doğrulanmış** status'a göre geçerli kayıt sayısı.

    Yalnız ``{Param}_N_Status`` geçerli (``IsValid``) olan satırlar sayılır."""
    counts = {p: 0 for p in params}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for p in params:
            if is_valid_code(validated_status(row, p)):
                counts[p] += 1
    return counts


def slim_row(row: dict) -> dict:
    """Audit/meta alanlarını atıp ReadTime/Period + parametre/status anahtarlarını korur."""
    if not isinstance(row, dict):
        return {}
    return {k: v for k, v in row.items() if k not in META_KEYS}
