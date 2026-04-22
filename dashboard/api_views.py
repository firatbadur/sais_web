"""Dashboard home sayfası AJAX endpoint'leri.

Browser JS her 5-30 sn'de bu endpoint'lerden JSON çekip widget'ları yeniler.
Tüm endpoint'ler login_required; role check client-side değil server-side.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone

from api.models import (
    Connection,
    PowerOff,
    Reading,
    ReadingHourly,
    Sensor,
    SensorLatest,
    Station,
    SystemLog,
)


@login_required
def home_kpis(request):
    """Üst KPI kartları — aktif istasyon, sensör, bağlantı, son saat reading sayısı."""
    now = timezone.now()
    last_hour = now - timedelta(hours=1)

    data = {
        "station_count": Station.objects.filter(active=True).count(),
        "sensor_count": Sensor.objects.filter(is_active=True).count(),
        "connection_count": Connection.objects.filter(is_enabled=True).count(),
        "readings_last_hour": Reading.objects.filter(time_iso__gte=last_hour).count(),
    }
    return JsonResponse(data)


@login_required
def home_snapshot(request):
    """Anlık sensör grid'i — SensorLatest snapshot'ları."""
    qs = (
        SensorLatest.objects
        .select_related("sensor", "sensor__parameter", "sensor__connection", "status")
        .order_by("sensor__connection_id", "sensor__parameter__parameter_name")
    )
    rows = []
    for latest in qs[:200]:   # şimdilik 200 ile sınırla; ileride filtre eklenir
        sensor = latest.sensor
        parameter = getattr(sensor, "parameter", None)
        rows.append({
            "sensor_id": sensor.pk if sensor else None,
            "parameter_name": (parameter.parameter_name if parameter else None) or "-",
            "unit": (parameter.unit_txt if parameter else "") or "",
            "value": latest.value,
            "quality": latest.quality,
            "status_code": latest.status.code if latest.status else None,
            "readtime": latest.readtime.isoformat() if latest.readtime else None,
            "connection": sensor.connection.name if sensor and sensor.connection_id else None,
        })
    return JsonResponse({"count": len(rows), "rows": rows})


@login_required
def home_trend(request):
    """24 saat trend grafiği — ReadingHourly'den seçili sensörlerin saatlik ortalaması."""
    now = timezone.now()
    since = now - timedelta(hours=24)

    # Öne çıkan aktif sensörlerden ilk 5 tanesini al (MVP — ileride filtre)
    top_sensor_ids = list(
        Sensor.objects.filter(is_active=True)
        .order_by("id")
        .values_list("id", flat=True)[:5]
    )

    series = []
    for sid in top_sensor_ids:
        buckets = (
            ReadingHourly.objects
            .filter(sensor_id=sid, bucket_start__gte=since)
            .order_by("bucket_start")
            .values("bucket_start", "avg_value", "min_value", "max_value")
        )
        points = [
            {
                "t": b["bucket_start"].isoformat() if b["bucket_start"] else None,
                "avg": b["avg_value"],
                "min": b["min_value"],
                "max": b["max_value"],
            }
            for b in buckets
        ]
        if points:
            sensor = Sensor.objects.select_related("parameter").filter(pk=sid).first()
            label = sensor.parameter.parameter_name if sensor and sensor.parameter else f"Sensor-{sid}"
            series.append({"sensor_id": sid, "label": label, "points": points})

    return JsonResponse({"since": since.isoformat(), "series": series})


@login_required
def home_events(request):
    """Son olaylar feed'i — SystemLog + PowerOff + kalite=bad son okumalar."""
    now = timezone.now()

    events = []

    for log in SystemLog.objects.select_related("type").order_by("-time_iso")[:10]:
        events.append({
            "kind": "system_log",
            "time": log.time_iso.isoformat() if log.time_iso else None,
            "title": log.type.name if log.type else "Log",
            "detail": log.description or "",
        })

    for po in PowerOff.objects.select_related("station").order_by("-time_iso")[:5]:
        events.append({
            "kind": "power_off",
            "time": po.time_iso.isoformat() if po.time_iso else None,
            "title": f"PowerOff — {po.station.name if po.station else '?'}",
            "detail": (
                f"Başlangıç: {po.start_date.isoformat() if po.start_date else '-'}, "
                f"Bitiş: {po.end_date.isoformat() if po.end_date else 'devam ediyor'}"
            ),
        })

    recent_bad = (
        Reading.objects.filter(quality="bad", time_iso__gte=now - timedelta(hours=6))
        .select_related("sensor", "sensor__parameter")
        .order_by("-time_iso")[:5]
    )
    for r in recent_bad:
        sensor_label = "?"
        if r.sensor:
            param = getattr(r.sensor, "parameter", None)
            sensor_label = param.parameter_name if param else f"Sensor-{r.sensor.id}"
        events.append({
            "kind": "bad_reading",
            "time": r.time_iso.isoformat() if r.time_iso else None,
            "title": f"Bad quality — {sensor_label}",
            "detail": "Sensör okuması başarısız oldu (quality=bad).",
        })

    # Zaman sıralı, desc
    events.sort(key=lambda e: e["time"] or "", reverse=True)
    return JsonResponse({"count": len(events), "events": events[:20]})
