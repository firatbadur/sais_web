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
    """Anlık sensör grid'i — SensorLatest snapshot'ları.

    Query param `type`:
      - `analog`  → yalnız sensor_type ∈ (0=AI, 1=AO)
      - `digital` → yalnız sensor_type ∈ (2=DI, 3=DO); response'a `is_active`
                    (digital_inverse uygulanmış bool) ve `sensor_type` ekler
      - omit/`all` → hepsi
    """
    type_filter = (request.GET.get("type") or "all").lower()
    sensor_types = None
    if type_filter == "analog":
        sensor_types = (0, 1)
    elif type_filter == "digital":
        sensor_types = (2, 3)

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
    if sensor_types is not None:
        qs = qs.filter(sensor__sensor_type__in=sensor_types)

    # Yıkama aktifse anasayfada da Status'ları override görsün (operatör
    # "şu an yıkama gidiyor" sinyalini bir bakışta görmeli). SensorLatest'in
    # gerçek status'u değişmiyor — frontend `wash.status_code` set ise
    # rozeti override eder ve tooltip'te orijinal kodu gösterir.
    from sais_domain.models import SystemSwitch
    switch = SystemSwitch.load()
    wash_status_code = switch.active_wash_status_code()
    wash_info = {
        "kind": switch.wash_active_kind if wash_status_code else None,
        "status_code": wash_status_code,
        "remaining_seconds": switch.wash_remaining_seconds() if wash_status_code else None,
    }

    rows = []
    for latest in qs[:200]:
        sensor = latest.sensor
        parameter = getattr(sensor, "parameter", None)
        connection = getattr(sensor, "connection", None) if sensor else None
        station = getattr(connection, "station", None) if connection else None
        stype = sensor.sensor_type if sensor else None

        is_active = None
        if sensor and stype in (2, 3):
            raw = bool(latest.value) if latest.value is not None else False
            if sensor.digital_inverse:
                raw = not raw
            is_active = raw

        rows.append({
            "sensor_id": sensor.pk if sensor else None,
            "sensor_type": stype,
            "station_name": station.name if station else None,
            "connection": connection.name if connection else None,
            "parameter_name": (parameter.parameter_name if parameter else None) or "-",
            "unit": (parameter.unit_txt if parameter else "") or (parameter.unit if parameter else "") or "",
            "value": latest.value,
            "is_active": is_active,
            "quality": latest.quality,
            "status_code": latest.status.code if latest.status else None,
            "status_name": latest.status.name if latest.status else None,
            "readtime": latest.readtime.isoformat() if latest.readtime else None,
        })
    return JsonResponse({"count": len(rows), "rows": rows, "wash": wash_info})


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
def system_control_status(request):
    """Sistem Kontrol sayfası — Celery durum widget'ı + aktif yıkama state'i.

    Sayfa bunu 10 sn'de bir poll'lar. Worker ping + beat last_run_at +
    yıkama bilgileri döner; UI banner state'ini ve geri sayımı bu yanıttan
    günceller (yıkama bitince sayfa reload zorunluğu yok).
    """
    from dashboard.views import _celery_status
    from sais_domain.models import SystemSwitch

    status = _celery_status()
    switch = SystemSwitch.load()
    return JsonResponse({
        "worker_ok": status["worker_ok"],
        "worker_count": status["worker_count"],
        "beat_ok": status["beat_ok"],
        "beat_last_run": (
            status["beat_last_run"].isoformat() if status["beat_last_run"] else None
        ),
        "wash_active_kind": switch.wash_active_kind,
        "wash_started_at": (
            switch.wash_started_at.isoformat() if switch.wash_started_at else None
        ),
        "wash_ends_at": (
            switch.wash_ends_at.isoformat() if switch.wash_ends_at else None
        ),
        "wash_started_by": (
            switch.wash_started_by.username if switch.wash_started_by else None
        ),
        "wash_remaining_seconds": switch.wash_remaining_seconds(),
    })


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


# --------------------------------------------------------------------------- #
# Yönetici: Yedekleme (rol=1)
# --------------------------------------------------------------------------- #

def _require_admin(request):
    """rol=1 (veya superuser) değilse 403 döndürür; değilse None."""
    from django.http import HttpResponseForbidden

    user = request.user
    if user.is_superuser or getattr(user, "rol", None) == 1:
        return None
    return HttpResponseForbidden("forbidden")


@login_required
def backup_status(request):
    """Yedekleme sayfası poll endpoint'i — çalışan iş var mı + kısa özet.

    Sayfa 5 sn'de bir poll'lar; `running` true→false geçince listeyi tazelemek
    için reload eder.
    """
    denied = _require_admin(request)
    if denied:
        return denied

    from api.models import DatabaseBackup, DatabaseRestore

    backup_running = DatabaseBackup.objects.filter(status="running").exists()
    restore_running = DatabaseRestore.objects.filter(status="running").exists()
    last = DatabaseBackup.objects.order_by("-started_at").first()
    return JsonResponse({
        "running": backup_running or restore_running,
        "backup_running": backup_running,
        "restore_running": restore_running,
        "backup_count": DatabaseBackup.objects.filter(status="success", pruned=False).count(),
        "last_backup": {
            "filename": last.filename,
            "status": last.status,
            "started_at": last.started_at.isoformat(),
        } if last else None,
    })


@login_required
def backup_download(request, pk):
    """Bir .bak dosyasını indirir. Path traversal'a karşı sıkı doğrulama."""
    denied = _require_admin(request)
    if denied:
        return denied

    import os

    from django.conf import settings
    from django.http import FileResponse, Http404

    from api.models import DatabaseBackup

    backup = DatabaseBackup.objects.filter(pk=pk, status="success", pruned=False).first()
    if not backup:
        raise Http404("Yedek bulunamadı.")

    # Güvenlik: sadece BACKUP_DIR altındaki, DB kaydıyla eşleşen dosya.
    backup_dir = os.path.realpath(settings.BACKUP_DIR)
    full = os.path.realpath(os.path.join(backup_dir, os.path.basename(backup.filename)))
    if not full.startswith(backup_dir + os.sep) or not os.path.exists(full):
        raise Http404("Dosya erişilemez.")

    return FileResponse(
        open(full, "rb"), as_attachment=True, filename=backup.filename,
        content_type="application/octet-stream",
    )


# ---------------------------------------------------------------------------
# Sürüm / güncelleme + 443 port kontrolü (Sistem Kontrol sayfası)
# ---------------------------------------------------------------------------

@login_required
def version_info(request):
    """Çalışan sürüm (APP_VERSION). Manuel güncelleme kararı kullanıcıda."""
    denied = _require_admin(request)
    if denied:
        return denied
    from django.conf import settings
    return JsonResponse({"current": getattr(settings, "APP_VERSION", "dev")})


@login_required
def trigger_update(request):
    """Manuel sürüm yükseltme: Watchtower HTTP API'sine update isteği gönderir.

    Watchtower :stable etiketli app container'larını (web/worker/beat) en son
    image'a çeker + (yeni sürüm varsa) yeniden başlatır. Otomatik güncelleme
    KAPALI; yükseltme yalnız buradan tetiklenir.
    """
    denied = _require_admin(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    import os
    import urllib.request

    token = (os.getenv("WATCHTOWER_API_TOKEN", "") or "").strip()
    if not token:
        return JsonResponse({
            "ok": False,
            "error": "Güncelleme servisi yapılandırılmamış (WATCHTOWER_API_TOKEN yok).",
        })

    req = urllib.request.Request(
        "http://watchtower:8080/v1/update",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", "replace")
        return JsonResponse({
            "ok": True,
            "message": "Güncelleme tetiklendi. Yeni sürüm varsa container'lar "
                       "yeniden başlatılır (birkaç dakika).",
            "detail": body[:1000],
        })
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": f"Güncelleme tetiklenemedi: {exc}"})


@login_required
def port_check(request):
    """Sunucu dıştan 443'te erişilebilir mi? Public IP + yerel Caddy 443 +
    (best-effort) public IP:443 bağlantı denemesi + harici doğrulama linki."""
    denied = _require_admin(request)
    if denied:
        return denied

    import socket
    import urllib.request

    result = {
        "public_ip": None,
        "local_443": False,      # Caddy container'ı 443 dinliyor mu (iç ağ)
        "external_443": None,    # public IP:443 dışarıdan açık mı (best-effort)
        "domain": "",
        "external_url": None,    # kesin doğrulama icin harici arac linki
    }

    # 1) Public IP
    for svc in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(svc, timeout=5) as r:
                ip = r.read().decode("utf-8", "replace").strip()
                if ip:
                    result["public_ip"] = ip
                    break
        except Exception:  # noqa: BLE001
            continue

    # 2) Yerel: Caddy 443 dinliyor mu (compose iç ağından)
    try:
        s = socket.create_connection(("caddy", 443), timeout=3)
        s.close()
        result["local_443"] = True
    except Exception:  # noqa: BLE001
        result["local_443"] = False

    # 3) Best-effort dış erişim: public IP:443'e bağlanmayı dene (NAT hairpin
    #    desteklenmezse sunucu kendi public IP'sine ulaşamayabilir -> kesin değil).
    if result["public_ip"]:
        try:
            s = socket.create_connection((result["public_ip"], 443), timeout=4)
            s.close()
            result["external_443"] = True
        except Exception:  # noqa: BLE001
            result["external_443"] = False
        result["external_url"] = (
            "https://www.yougetsignal.com/tools/open-ports/"
            f"?remoteAddress={result['public_ip']}&portNumber=443"
        )

    # 4) Domain (panelde tanımlıysa)
    try:
        from api.models import WebSettings
        result["domain"] = (WebSettings.load().domain or "").strip()
    except Exception:  # noqa: BLE001
        pass

    return JsonResponse(result)
