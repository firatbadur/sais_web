"""
Sensör okuma sonucunu DB'ye yazan tek atomik fonksiyon.

- `SensorLatest` (snapshot) her başarılı okumada güncellenir (HMI taze değer).
- `Reading` (historian) insert'i `Connection.save_interval_sec` ile kontrollü:
  None ise her okumada yazılır; değer verilmişse sensör başına o sürede bir yazılır.
- `Sensor.decimals` verilmişse sayısal değer yuvarlanır.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from api.models import Reading, SensorLatest, StatusCode


# Yıkama/bakım sensörlerinin parametre adları → tetikleyecekleri status kodu.
# İstasyonda bu parametrelerden biri var ve değeri "aktif" (truthy) ise, aynı
# istasyondaki tüm diğer sensörlerin status'u bu kodla override edilir.
# Birden fazlası aktifse aşağıdaki öncelik sırası uygulanır.
_OVERLAY_PARAM_TO_STATUS: dict[str, int] = {
    # Yıkama (23 > 24 öncelik — manuel kritik)
    "Yikama":          23,
    "ManuelYikama":    23,
    "HaftalikYikama":  24,
    # Bakım
    "İstasyonBakimda": 25,
    "Bakim":           25,
    "BakimModu":       25,
    "TesisBakimda":    26,
}
_OVERLAY_PRIORITY = (23, 24, 25, 26)  # küçük kod = daha kritik

# Process-local cache: station_id → (status_code | None, expires_ts).
# 5 sn TTL — polling cycle'ı (5-10 sn) ile uyumlu, wash state'i değişince en
# geç bir cycle gecikme ile diğer sensörlere yansır.
_OVERLAY_CACHE: dict[int, tuple[int | None, float]] = {}
_OVERLAY_CACHE_TTL = 5.0


# Status kodu cache'i — her okumada DB sorgulamamak için process-local memoize.
# StatusCode lookup tablosu nadiren değişir; reload gerekirse worker restart yeter.
_STATUS_CACHE: dict[int, StatusCode | None] = {}


def _status(code: int) -> StatusCode | None:
    if code not in _STATUS_CACHE:
        _STATUS_CACHE[code] = StatusCode.objects.filter(code=code).first()
    return _STATUS_CACHE[code]


def _apply_decimals(value: Any, decimals: int | None) -> Any:
    """sensor.decimals verildiyse sayısal değeri yuvarla. bool dokunulmaz."""
    if decimals is None or value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value), int(decimals))


def _derive_range_status(sensor, value: Any) -> int | None:
    """Değeri Parameter aralıklarına göre değerlendir; uygun StatusCode döner.

    Sadece caller'ın status_code=1 (Geçerli) verdiği başarılı okumalar için
    çağrılır — comm/decode hatalarında zaten status başka.

    Üç ayrı aralık + öncelik (geniş → dar, kritikten az kritiğe):
      - min_range/max_range  ("Range") dışı  → 39 (Ölçüm Aralığı Dışı — donanım)
      - olcum_min/olcum_max  ("Ölçüm")  dışı → 12 (Alarm — fiziksel sınır)
      - gec_min/gec_max      ("Geçerli")dışı → 4  (Geçersiz Veri — process)
      - Hepsinin içi → None (kalır = 1 Geçerli)

    Mantık: range > olcum > gec şeklinde aralıklar daralır. En geniş aralık
    dışındaysa daha dar aralıkları kontrol etmenin anlamı yok.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    param = getattr(sensor, "parameter", None)
    if param is None:
        return None
    v = float(value)

    # 1. En geniş — sensör donanım mutlak limiti
    if param.min_range is not None and v < float(param.min_range):
        return 39
    if param.max_range is not None and v > float(param.max_range):
        return 39

    # 2. Orta — fiziksel ölçüm sınırı (alarm)
    if param.olcum_min is not None and v < float(param.olcum_min):
        return 12
    if param.olcum_max is not None and v > float(param.olcum_max):
        return 12

    # 3. En dar — kabul edilebilir process aralığı
    if param.gec_min is not None and v < float(param.gec_min):
        return 4
    if param.gec_max is not None and v > float(param.gec_max):
        return 4

    return None


def _station_overlay_status(station_id: int | None) -> int | None:
    """İstasyondaki yıkama/bakım sensörlerinden biri aktifse status kodu döner.

    Parametre adı `_OVERLAY_PARAM_TO_STATUS` map'inde olan ve değeri "aktif"
    (truthy — bool True veya float > 0) olan ilk sensörü bulur. Birden fazlası
    aktifse `_OVERLAY_PRIORITY` sırasıyla en kritik olanı döner.

    Sonuç process-local cache'de 5 sn tutulur (5-10 sn'lik polling cycle ile
    uyumlu; wash state'i değişince max 1 cycle gecikme ile diğer sensörlere
    yansır).
    """
    if not station_id:
        return None
    import time
    now_ts = time.time()
    cached = _OVERLAY_CACHE.get(station_id)
    if cached and cached[1] > now_ts:
        return cached[0]

    active_codes: set[int] = set()
    qs = (
        SensorLatest.objects
        .filter(
            sensor__connection__station_id=station_id,
            sensor__parameter__parameter_name__in=list(_OVERLAY_PARAM_TO_STATUS.keys()),
            value__isnull=False,
        )
        .select_related("sensor__parameter")
    )
    for sl in qs:
        param_name = sl.sensor.parameter.parameter_name
        code = _OVERLAY_PARAM_TO_STATUS.get(param_name)
        if code is None:
            continue
        try:
            if float(sl.value) > 0:
                active_codes.add(code)
        except (TypeError, ValueError):
            continue

    result: int | None = None
    for c in _OVERLAY_PRIORITY:
        if c in active_codes:
            result = c
            break

    _OVERLAY_CACHE[station_id] = (result, now_ts + _OVERLAY_CACHE_TTL)
    return result


def invalidate_overlay_cache(station_id: int | None = None) -> None:
    """Overlay cache'ini temizle. station_id None ise tüm cache.

    Wash sensörünün değeri değiştiğinde (digital sensör True/False geçişi)
    çağrılabilir; aksi halde 5 sn TTL ile zaten taze kalır.
    """
    if station_id is None:
        _OVERLAY_CACHE.clear()
    else:
        _OVERLAY_CACHE.pop(station_id, None)


@transaction.atomic
def persist_reading(
    sensor,
    *,
    value: Any,
    quality: str = "good",
    origin: str = "polled",
    status_code: int = 1,
    timestamp=None,
) -> Reading | None:
    """Bir sensör okumasını kaydet.

    - Değer `sensor.decimals` ile yuvarlanır (numeric ise).
    - `SensorLatest` snapshot'ı HER ZAMAN upsert edilir (HMI için taze değer).
    - `Reading` insert sadece şu koşulda yapılır:
        * Connection.save_interval_sec is None (veya 0), VEYA
        * now - SensorLatest.last_saved_at >= save_interval_sec, VEYA
        * İlk kayıt (last_saved_at henüz null)
    - Değer önceki snapshot'tan farklıysa `last_change_at` güncellenir.
    - `update_count` her çağrıda artırılır.

    Dönen değer:
        Reading instance — eğer insert yapıldıysa.
        None — save_interval nedeniyle skip edildiyse (sadece snapshot güncellendi).
    """
    now = timestamp or timezone.now()
    value = _apply_decimals(value, sensor.decimals)

    # Caller başarılı okuma diyorsa (status_code=1) ekstra durum kontrolleri:
    #   1) İstasyonda yıkama/bakım sensörü aktif mi? (overlay) → 23/24/25/26
    #   2) Değer range/olcum/gec aralıklarında mı? → 39/12/4
    # Caller başka kod verdiyse (8 iletişim, 4 geçersiz, 0 veri yok...) zaten
    # hata; override etme.
    if status_code == 1:
        station_id = (
            sensor.connection.station_id
            if sensor.connection_id and hasattr(sensor, "connection") else None
        )
        overlay = _station_overlay_status(station_id)
        if overlay is not None:
            status_code = overlay
            # Yıkama/bakım esnasında veri kullanılmamalı; kalite "uncertain"
            quality = "uncertain"
        else:
            derived = _derive_range_status(sensor, value)
            if derived is not None:
                status_code = derived
                # 4 Geçersiz + 12 Alarm → bad (kritik); 39 Aralık Dışı → uncertain
                quality = "uncertain" if derived == 39 else "bad"

    status = _status(status_code)

    latest = SensorLatest.objects.filter(sensor=sensor).first()

    # --- Reading insert koşulu: save_interval_sec ile gated ---
    save_interval = getattr(sensor.connection, "save_interval_sec", None) if sensor.connection_id else None
    should_save = True
    if save_interval and latest and latest.last_saved_at:
        elapsed = (now - latest.last_saved_at).total_seconds()
        if elapsed < save_interval:
            should_save = False

    reading = None
    if should_save:
        reading = Reading.objects.create(
            sensor=sensor,
            value=value,
            status=status,
            quality=quality,
            origin=origin,
            time_iso=now,
        )

    # --- SensorLatest upsert (her zaman) ---
    changed = (latest is None) or (latest.value != value)
    defaults = {
        "value": value,
        "status": status,
        "quality": quality,
        "readtime": now,
        "update_count": (latest.update_count + 1) if latest else 1,
    }
    if changed:
        defaults["last_change_at"] = now
    if should_save:
        defaults["last_saved_at"] = now

    SensorLatest.objects.update_or_create(sensor=sensor, defaults=defaults)
    return reading
