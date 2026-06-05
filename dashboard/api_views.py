"""Dashboard home sayfası AJAX endpoint'leri.

Browser JS her 5-30 sn'de bu endpoint'lerden JSON çekip widget'ları yeniler.
Tüm endpoint'ler login_required; role check client-side değil server-side.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone

from api.models import (
    Connection,
    Parameter,
    PowerOff,
    Reading,
    ReadingFifteenMin,
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
        .select_related(
            "sensor",
            "sensor__parameter",
            "sensor__connection",
            "sensor__connection__station",
            "status",
        )
        .order_by(
            "sensor__connection__station__name",
            "sensor__connection_id",
            "sensor__parameter__parameter_name",
        )
    )
    rows = []
    for latest in qs[:200]:   # şimdilik 200 ile sınırla; ileride filtre eklenir
        sensor = latest.sensor
        parameter = getattr(sensor, "parameter", None)
        connection = getattr(sensor, "connection", None) if sensor else None
        station = getattr(connection, "station", None) if connection else None
        rows.append({
            "sensor_id": sensor.pk if sensor else None,
            "station_name": station.name if station else None,
            "connection": connection.name if connection else None,
            "parameter_name": (parameter.parameter_name if parameter else None) or "-",
            "unit": (parameter.unit_txt if parameter else "") or (parameter.unit if parameter else "") or "",
            "value": latest.value,
            "quality": latest.quality,
            "status_code": latest.status.code if latest.status else None,
            "readtime": latest.readtime.isoformat() if latest.readtime else None,
        })
    return JsonResponse({"count": len(rows), "rows": rows})


@login_required
def home_trend(request):
    """24 saat trend grafiği — sensör başına seri.

    Granularity cascade: ReadingHourly (24 nokta) → ReadingFifteenMin (96 nokta)
    → raw Reading (son 500 satır). Aggregate task'ları henüz dönmediyse de
    chart boş kalmasın diye.
    """
    now = timezone.now()
    since = now - timedelta(hours=24)

    # Öne çıkan aktif sensörlerden ilk 5 tanesini al (MVP — ileride filtre)
    top_sensors = list(
        Sensor.objects.filter(is_active=True)
        .select_related("parameter")
        .order_by("id")[:5]
    )

    # Tek sorgu ile hangi granularity'de veri var, tespit et.
    top_ids = [s.id for s in top_sensors]
    if ReadingHourly.objects.filter(sensor_id__in=top_ids, bucket_start__gte=since).exists():
        bucket_level = "hourly"
    elif ReadingFifteenMin.objects.filter(sensor_id__in=top_ids, bucket_start__gte=since).exists():
        bucket_level = "15min"
    else:
        bucket_level = "raw"

    series = []
    for sensor in top_sensors:
        label = sensor.parameter.parameter_name if sensor.parameter else f"Sensor-{sensor.id}"

        if bucket_level == "raw":
            readings = (
                Reading.objects
                .filter(sensor_id=sensor.id, time_iso__gte=since, value__isnull=False)
                .order_by("time_iso")
                .values("time_iso", "value")[:500]
            )
            points = [
                {"t": r["time_iso"].isoformat(), "avg": r["value"], "min": r["value"], "max": r["value"]}
                for r in readings if r["time_iso"] is not None
            ]
        else:
            model = ReadingHourly if bucket_level == "hourly" else ReadingFifteenMin
            buckets = (
                model.objects
                .filter(sensor_id=sensor.id, bucket_start__gte=since)
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
                for b in buckets if b["bucket_start"] is not None
            ]

        if points:
            series.append({"sensor_id": sensor.id, "label": label, "points": points})

    return JsonResponse({
        "since": since.isoformat(),
        "bucket_level": bucket_level,
        "series": series,
    })


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


@login_required
def station_parameters(request):
    """İstasyona ait parametre listesi — rapor formu select2'sini doldurur.

    Sensör tipine göre Analog / Dijital / Diğer optgroup'larıyla döner;
    boş gruplar atlanır.
    """
    try:
        station_id = int(request.GET.get("station") or 0) or None
    except (TypeError, ValueError):
        station_id = None
    if station_id:
        # Parameter.station FK'sı her zaman güvenilir değil; bu istasyonun
        # connection'larına bağlı sensörleri olan parametreleri döndür.
        qs = (
            Parameter.objects
            .filter(sensors__connection__station_id=station_id)
            .distinct()
            .order_by("parameter_name")
            .prefetch_related("sensors")
        )
    else:
        qs = Parameter.objects.none()

    analog, digital, other = [], [], []
    for p in qs:
        sensors = list(p.sensors.all())
        stype = sensors[0].sensor_type if sensors else None
        item = {
            "id": p.id,
            "text": p.parameter_name or f"Parametre {p.id}",
            "unit": p.unit_txt or p.unit or "",
        }
        if stype in (0, 1):
            analog.append(item)
        elif stype in (2, 3):
            digital.append(item)
        else:
            other.append(item)

    groups = []
    if analog:
        groups.append({"text": "Analog", "children": analog})
    if digital:
        groups.append({"text": "Dijital", "children": digital})
    if other:
        groups.append({"text": "Diğer", "children": other})
    return JsonResponse({"results": groups})
