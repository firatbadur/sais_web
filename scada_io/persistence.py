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
    status = _status(status_code)
    value = _apply_decimals(value, sensor.decimals)

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
