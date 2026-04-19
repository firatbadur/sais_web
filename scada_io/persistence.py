"""
Sensör okuma sonucunu DB'ye yazan tek atomik fonksiyon.

`Reading` insert + `SensorLatest` upsert; değer değiştiyse `last_change_at`
güncellenir, `update_count` artırılır.
"""
from __future__ import annotations

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


@transaction.atomic
def persist_reading(
    sensor,
    *,
    value: Any,
    quality: str = "good",
    origin: str = "polled",
    status_code: int = 1,
    timestamp=None,
) -> Reading:
    """Bir sensör okumasını kaydet.

    - `Reading` satırı oluşturur (tarihsel historian)
    - `SensorLatest` snapshot'ını upsert eder (HMI hızlı okuma)
    - Değer önceki snapshot'tan farklıysa `last_change_at` güncellenir
    - `update_count` her çağrıda artırılır
    """
    now = timestamp or timezone.now()
    status = _status(status_code)

    reading = Reading.objects.create(
        sensor=sensor,
        value=value,
        status=status,
        quality=quality,
        origin=origin,
        time_iso=now,
    )

    latest = SensorLatest.objects.filter(sensor=sensor).first()
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

    SensorLatest.objects.update_or_create(sensor=sensor, defaults=defaults)
    return reading
