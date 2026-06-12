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

    # Sıralama: operatörün düzenlediği display_order birincil; eşitse istasyon /
    # bağlantı / parametre adı. Dijital tabloda DO (sensor_type=3) daima DI (2)
    # üstünde olsun diye -sensor_type en başa eklenir.
    order_fields = [
        "sensor__display_order",
        "sensor__connection__station__name",
        "sensor__connection_id",
        "sensor__parameter__parameter_name",
    ]
    if type_filter == "digital":
        order_fields = ["-sensor__sensor_type"] + order_fields

    qs = (
        SensorLatest.objects
        .select_related(
            "sensor",
            "sensor__parameter",
            "sensor__connection",
            "sensor__connection__station",
            "status",
        )
        .order_by(*order_fields)
        # Admin'den "Dashboard'da Gizle" işaretli sensörler tablolarda görünmez
        # (polling/kayıt etkilenmez; yalnız görünürlük filtresi).
        .filter(sensor__dashboard_hidden=False)
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
def digital_output_command(request):
    """Operatör/admin → dijital output (sensor_type=3) sensörüne Start(1)/Stop(0).

    POST JSON: ``{"sensor_id": int, "action": "start"|"stop"}``
    Yanıt: ``{"ok": bool, "message": str|None, "command_id": int|None,
             "duplicate": bool}``

    Komut `Command` tablosuna yazılır (`source="operator"`, `requested_by`,
    `request_type=manual_output`) → Celery dispatch_commands/execute_command
    asenkron yürütür. UI hataya düşmez; idempotency_key 3 sn'lik pencereyle
    çift-tıklamayı emer, meşru tekrar toggle'a izin verir.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    action = data.get("action")
    try:
        sensor_id = int(data.get("sensor_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Geçersiz sensor_id."}, status=400)
    if action not in ("start", "stop"):
        return JsonResponse({"ok": False, "error": "Geçersiz action."}, status=400)

    # Güvenlik sınırı: yalnız dijital output (sensor_type=3) yazılabilir.
    sensor = (
        Sensor.objects.select_related("connection")
        .filter(pk=sensor_id, sensor_type=3)
        .first()
    )
    if sensor is None:
        return JsonResponse({"ok": False, "error": "Geçersiz çıkış sensörü."}, status=404)
    if not sensor.is_active:
        return JsonResponse({"ok": False, "error": "Sensör pasif."}, status=409)

    # Operatör mantıksal Start/Stop ister; digital_inverse ise fiziksel coil
    # tersine yazılır ki sonraki okuma istenen durumu göstersin (snapshot da
    # okumayı aynı şekilde tersliyor).
    value = 1 if action == "start" else 0
    coil_value = (0 if value else 1) if sensor.digital_inverse else value

    try:
        from datetime import timedelta as _timedelta

        from api.models import Command, RequestType

        bucket = int(timezone.now().timestamp() // 3)
        idem = f"manual_output:{sensor_id}:{action}:{bucket}"
        request_type = RequestType.objects.filter(code="manual_output").first()
        cmd, created = Command.objects.get_or_create(
            idempotency_key=idem,
            defaults=dict(
                sensor=sensor,
                value_type="bool",
                value=coil_value,
                status="pending",
                priority=10,
                source="operator",
                request_type=request_type,
                requested_by=request.user,
                expires_at=timezone.now() + _timedelta(minutes=5),
                max_attempts=3,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — UI hataya düşmesin
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse({
        "ok": True,
        "message": None if created else "Aynı komut zaten kuyrukta.",
        "command_id": cmd.pk,
        "duplicate": not created,
    })


@login_required
def sensors_reorder(request):
    """Operatör/admin → canlı tablo sensör sırasını kaydeder.

    POST JSON: ``{"order": [sensor_id, ...]}`` → her sensörün display_order'ı
    listedeki index'ine eşitlenir.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    try:
        data = json.loads(request.body or "{}")
        order = data.get("order")
        order = [int(x) for x in order][:1000]
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz sıra listesi."}, status=400)

    from django.db import transaction

    try:
        with transaction.atomic():
            sensors = {s.pk: s for s in Sensor.objects.filter(pk__in=order)}
            to_update = []
            for idx, sid in enumerate(order):
                s = sensors.get(sid)
                if s and s.display_order != idx:
                    s.display_order = idx
                    to_update.append(s)
            if to_update:
                Sensor.objects.bulk_update(to_update, ["display_order"])
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse({"ok": True, "updated": len(to_update)})


@login_required
def home_trend(request):
    """24 saat trend grafiği — sensör başına seri.

    Granularity cascade: ReadingHourly (24 nokta) → ReadingFifteenMin (96 nokta)
    → raw Reading (son 500 satır). Aggregate task'ları henüz dönmediyse de
    chart boş kalmasın diye.
    """
    now = timezone.now()
    since = now - timedelta(hours=24)

    # Öne çıkan aktif sensörlerden ilk 5 tanesini al (MVP — ileride filtre).
    # Dashboard'da gizlenenler trend grafiğinde de gösterilmez.
    top_sensors = list(
        Sensor.objects.filter(is_active=True, dashboard_hidden=False)
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


def _require_operator(request):
    """rol ∈ (1,2) (veya superuser) değilse 403 döndürür; değilse None."""
    from django.http import HttpResponseForbidden

    user = request.user
    if user.is_superuser or getattr(user, "rol", None) in (1, 2):
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

def _parse_semver(value):
    """'v0.2.22' / '0.2.22' -> (0,2,22); eşleşmezse None."""
    import re
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", str(value or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


@login_required
def version_info(request):
    """Çalışan sürüm + GHCR'daki en son sürüm. update_available=False ise
    'Güncelle' butonu kapatılır. GHCR_TOKEN yoksa/erişilemezse checked=False
    döner ve buton açık kalır (fallback — yine de elle yükseltilebilir)."""
    denied = _require_admin(request)
    if denied:
        return denied

    import base64
    import json
    import os
    import urllib.request

    from django.conf import settings

    current = getattr(settings, "APP_VERSION", "dev")
    out = {"current": current, "latest": None, "update_available": None, "checked": False}

    image = os.getenv("GHCR_IMAGE", "ghcr.io/firatbadur/sais_web")
    token = (os.getenv("GHCR_TOKEN", "") or "").strip()
    repo = image.split("ghcr.io/", 1)[-1].strip("/")
    if not token or "/" not in repo:
        return JsonResponse(out)  # kontrol yapılamıyor -> buton açık kalsın

    user = repo.split("/", 1)[0]
    try:
        auth = base64.b64encode(f"{user}:{token}".encode()).decode()
        treq = urllib.request.Request(
            f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull",
            headers={"Authorization": f"Basic {auth}"},
        )
        tok = json.load(urllib.request.urlopen(treq, timeout=8)).get("token")
        lreq = urllib.request.Request(
            f"https://ghcr.io/v2/{repo}/tags/list",
            headers={"Authorization": f"Bearer {tok}"},
        )
        tags = json.load(urllib.request.urlopen(lreq, timeout=8)).get("tags", []) or []
    except Exception:  # noqa: BLE001 — ağ/auth hatası -> fallback (buton açık)
        return JsonResponse(out)

    versions = [t for t in tags if _parse_semver(t)]
    if not versions:
        return JsonResponse(out)

    latest = max(versions, key=_parse_semver)
    out["latest"] = latest
    out["checked"] = True
    cur = _parse_semver(current)
    # current parse edilemiyorsa (dev) güncellemeye izin ver; aksi halde latest > current.
    out["update_available"] = (cur is None) or (_parse_semver(latest) > cur)
    return JsonResponse(out)


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
def server_info(request):
    """Sunucu bilgileri kartı — güncel public IP + değişim geçmişi.

    Her çağrıda dış IP taze çekilir ve `PublicIpRecord.record` ile kaydedilir
    (değişmişse yeni satır). Beat task'ı (`record_public_ip_task`) operatör
    sayfayı açmasa da değişimi yakalar; bu endpoint anlık görünüm + geçmiş verir.
    """
    denied = _require_admin(request)
    if denied:
        return denied

    from api.models import PublicIpRecord
    from api.tasks import fetch_public_ip

    ip = fetch_public_ip()
    if ip:
        PublicIpRecord.record(ip)

    current = PublicIpRecord.objects.filter(is_current=True).order_by("-last_seen").first()
    history = list(PublicIpRecord.objects.order_by("-last_seen")[:10])

    domain = ""
    try:
        from api.models import WebSettings
        domain = (WebSettings.load().domain or "").strip()
    except Exception:  # noqa: BLE001
        pass

    return JsonResponse({
        "public_ip": (current.ip_address if current else ip),
        "reachable": ip is not None,   # dış IP servisine ulaşılabildi mi
        "domain": domain,
        "history": [
            {
                "ip": h.ip_address,
                "first_seen": h.first_seen.isoformat() if h.first_seen else None,
                "last_seen": h.last_seen.isoformat() if h.last_seen else None,
                "is_current": h.is_current,
            }
            for h in history
        ],
    })


@login_required
def port_check(request):
    """Sunucu dıştan 443'te erişilebilir mi? Public IP + yerel Caddy 443 +
    (best-effort) public IP:443 bağlantı denemesi + harici doğrulama linki."""
    denied = _require_admin(request)
    if denied:
        return denied

    import socket

    from api.models import PublicIpRecord
    from api.tasks import fetch_public_ip

    result = {
        "public_ip": None,
        "local_443": False,      # Caddy container'ı 443 dinliyor mu (iç ağ)
        "external_443": None,    # public IP:443 dışarıdan açık mı (best-effort)
        "domain": "",
        "external_url": None,    # kesin doğrulama icin harici arac linki
    }

    # 1) Public IP — çek + değişim geçmişine kaydet
    result["public_ip"] = fetch_public_ip()
    if result["public_ip"]:
        PublicIpRecord.record(result["public_ip"])

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


# --------------------------------------------------------------------------- #
# Bildirim Merkezi: alıcı listesi + toplu test gönderimi + hazır mesajlar (rol=1)
# --------------------------------------------------------------------------- #

@login_required
def notification_recipients(request):
    """Test paneli için aktif kullanıcı listesi (select2)."""
    denied = _require_admin(request)
    if denied:
        return denied

    from users.models import CustomUser

    results = []
    for u in CustomUser.objects.filter(is_active=True).order_by("first_name", "username"):
        name = (u.get_full_name() or u.username).strip()
        phone = (u.phone_number or "").strip()
        email = (u.email or "").strip()
        chans = []
        if u.sms_enabled and phone:
            chans.append("SMS")
        if u.email_enabled and email:
            chans.append("E-posta")
        suffix = f" ({', '.join(chans)})" if chans else ""
        results.append({
            "id": u.pk,
            "text": f"{name} — {phone or '—'} / {email or '—'}{suffix}",
            "phone": phone,
            "email": email,
        })
    return JsonResponse({"results": results})


@login_required
def notification_send_test(request):
    """Seçili kullanıcılara + serbest telefon/e-postalara toplu test gönderir.

    POST JSON: {user_ids:[], extra_phones:[], extra_emails:[], channels:["sms","email"],
                message:str, subject?:str}
    """
    import json

    denied = _require_admin(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    message = (data.get("message") or "").strip()
    channels = [c for c in (data.get("channels") or []) if c in ("sms", "email")]
    if not message:
        return JsonResponse({"ok": False, "error": "Mesaj boş olamaz."}, status=400)
    if not channels:
        return JsonResponse({"ok": False, "error": "En az bir kanal seçin."}, status=400)

    from users.models import CustomUser
    from api.notifications import send_bulk

    recipients = []
    user_ids = data.get("user_ids") or []
    if user_ids:
        for u in CustomUser.objects.filter(pk__in=user_ids):
            recipients.append({
                "name": (u.get_full_name() or u.username).strip(),
                "phone": (u.phone_number or "").strip(),
                "email": (u.email or "").strip(),
            })
    for ph in (data.get("extra_phones") or []):
        ph = (ph or "").strip()
        if ph:
            recipients.append({"name": ph, "phone": ph, "email": ""})
    for em in (data.get("extra_emails") or []):
        em = (em or "").strip()
        if em:
            recipients.append({"name": em, "phone": "", "email": em})

    if not recipients:
        return JsonResponse({"ok": False, "error": "Alıcı seçilmedi."}, status=400)

    results = send_bulk(
        recipients, channels, message,
        subject=(data.get("subject") or "").strip() or None,
        triggered_by=request.user, kind="test",
    )
    sent = sum(1 for r in results if r["ok"])
    return JsonResponse({"ok": True, "sent": sent, "total": len(results), "results": results})


@login_required
def notification_templates(request):
    """Hazır mesaj CRUD. GET liste / POST {title,body,channel?} / DELETE ?id=."""
    import json

    denied = _require_admin(request)
    if denied:
        return denied

    from api.models import MessageTemplate

    if request.method == "GET":
        items = [
            {"id": t.pk, "title": t.title, "body": t.body, "channel": t.channel}
            for t in MessageTemplate.objects.all()[:100]
        ]
        return JsonResponse({"results": items})

    if request.method == "POST":
        try:
            data = json.loads(request.body or "{}")
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)
        title = (data.get("title") or "").strip()
        body = (data.get("body") or "").strip()
        channel = data.get("channel") or "both"
        if not title or not body:
            return JsonResponse({"ok": False, "error": "Başlık ve mesaj zorunlu."}, status=400)
        if channel not in ("sms", "email", "both"):
            channel = "both"
        t = MessageTemplate.objects.create(
            title=title[:120], body=body, channel=channel, created_by=request.user,
        )
        return JsonResponse({"ok": True, "id": t.pk, "title": t.title, "body": t.body,
                             "channel": t.channel})

    if request.method == "DELETE":
        tid = request.GET.get("id")
        MessageTemplate.objects.filter(pk=tid).delete()
        return JsonResponse({"ok": True})

    return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)


# --------------------------------------------------------------------------- #
# Alarm Yönetimi: istasyon IO listesi + alarm kuralı CRUD (rol 1,2)
# --------------------------------------------------------------------------- #

def _sensor_label(s):
    if s.parameter and s.parameter.parameter_name:
        return s.parameter.parameter_name
    return s.brand or s.model or f"Sensor {s.pk}"


@login_required
def alarm_io(request):
    """Bir istasyonun alarm formu IO listeleri: analog parametreler + dijital
    kanallar (DI) + çıkışlar (DO)."""
    denied = _require_operator(request)
    if denied:
        return denied
    try:
        station_id = int(request.GET.get("station") or 0) or None
    except (TypeError, ValueError):
        station_id = None
    if not station_id:
        return JsonResponse({"analog": [], "digital": []})

    analog = [
        {"id": p.id, "text": p.parameter_name or f"Parametre {p.id}"}
        for p in Parameter.objects.filter(
            sensors__connection__station_id=station_id,
            sensors__sensor_type__in=(0, 1),
        ).distinct().order_by("parameter_name")
    ]
    digital = [
        {"id": s.id, "text": _sensor_label(s)}
        for s in Sensor.objects.filter(connection__station_id=station_id, sensor_type__in=(2, 3))
        .select_related("parameter").order_by("address")
    ]
    return JsonResponse({"analog": analog, "digital": digital})


@login_required
def alarm_rules(request):
    """Alarm kuralı CRUD.

    GET ?rule_type=analog|diag → liste.
    POST JSON {toggle_id} → enable/disable; aksi halde yeni kural oluştur.
    DELETE ?id= → sil.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied

    from api.models import AlarmRule, Parameter as P, Sensor as S, Station as St

    if request.method == "GET":
        rt = request.GET.get("rule_type")
        qs = AlarmRule.objects.select_related("station", "parameter", "sensor").order_by("-created_at")
        if rt == "analog":
            qs = qs.filter(rule_type=AlarmRule.RULE_ANALOG)
        elif rt == "diag":
            qs = qs.exclude(rule_type=AlarmRule.RULE_ANALOG)
        items = [{
            "id": r.pk, "station": r.station.name if r.station else "",
            "channel": (r.parameter.parameter_name if r.parameter else "") if r.rule_type == AlarmRule.RULE_ANALOG else "",
            "type_label": r.type_label, "period": r.get_period_minutes_display(),
            "min": r.min_value, "max": r.max_value,
            "channels": (("SMS " if r.send_sms else "") + ("E-posta" if r.send_email else "")).strip() or "-",
            "message": r.message, "enabled": r.enabled,
        } for r in qs]
        return JsonResponse({"results": items})

    if request.method == "DELETE":
        AlarmRule.objects.filter(pk=request.GET.get("id")).delete()
        return JsonResponse({"ok": True})

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    # Enable/disable toggle
    if data.get("toggle_id"):
        r = AlarmRule.objects.filter(pk=data["toggle_id"]).first()
        if not r:
            return JsonResponse({"ok": False, "error": "Kural bulunamadı."}, status=404)
        r.enabled = not r.enabled
        r.save(update_fields=["enabled"])
        return JsonResponse({"ok": True, "enabled": r.enabled})

    # Yeni kural
    try:
        station = St.objects.get(pk=data.get("station_id"))
    except (St.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "İstasyon seçin."}, status=400)

    rule_type = data.get("rule_type")
    message = (data.get("message") or "").strip()
    if not message:
        return JsonResponse({"ok": False, "error": "Mesaj zorunlu."}, status=400)

    try:
        period = int(data.get("period_minutes") or 60)
    except (TypeError, ValueError):
        period = 60

    rule = AlarmRule(
        station=station, rule_type=rule_type, message=message, period_minutes=period,
        notify_all=bool(data.get("notify_all", True)),
        send_sms=bool(data.get("send_sms", True)),
        send_email=bool(data.get("send_email", True)),
        created_by=request.user,
    )

    if rule_type == AlarmRule.RULE_ANALOG:
        pid = data.get("parameter_id")
        if not pid:
            return JsonResponse({"ok": False, "error": "Kanal seçin."}, status=400)
        rule.parameter = P.objects.filter(pk=pid).first()
        # Limit tipi min/max girişinden türetilir: ikisi de varsa minmax,
        # sadece min varsa min (altı), sadece max varsa max (üstü). En az biri zorunlu.
        mn, mx = data.get("min_value"), data.get("max_value")
        has_min = mn not in ("", None)
        has_max = mx not in ("", None)
        if not has_min and not has_max:
            return JsonResponse({"ok": False, "error": "Min veya Max değerinden en az biri zorunludur."}, status=400)
        try:
            rule.min_value = float(mn) if has_min else None
            rule.max_value = float(mx) if has_max else None
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Min/Max sayısal bir değer olmalı."}, status=400)
        if has_min and has_max:
            rule.condition = AlarmRule.COND_MINMAX
        elif has_min:
            rule.condition = AlarmRule.COND_MIN
        else:
            rule.condition = AlarmRule.COND_MAX
    elif rule_type == AlarmRule.RULE_DIGITAL:
        sid = data.get("sensor_id")
        rule.sensor = S.objects.filter(pk=sid, sensor_type__in=(2, 3)).first()
        if rule.sensor is None:
            return JsonResponse({"ok": False, "error": "Dijital sensör seçin."}, status=400)
        rule.trigger_state = bool(data.get("trigger_state", True))
    elif rule_type == AlarmRule.RULE_OFFLINE:
        try:
            rule.offline_seconds = int(data.get("offline_seconds") or 900)
        except (TypeError, ValueError):
            rule.offline_seconds = 900
    else:
        return JsonResponse({"ok": False, "error": "Geçersiz alarm türü."}, status=400)

    rule.save()
    return JsonResponse({
        "ok": True, "id": rule.pk, "station": station.name,
        "type_label": rule.type_label, "period": rule.get_period_minutes_display(),
        "message": rule.message, "enabled": rule.enabled,
    })


# --------------------------------------------------------------------------- #
# Numune Senaryosu (no-code kurucu) AJAX endpoint'leri
# --------------------------------------------------------------------------- #

def _serialize_scenario(sc, *, full=False):
    data = {
        "id": sc.pk,
        "name": sc.name,
        "description": sc.description,
        "kind": sc.kind,
        "kind_label": sc.get_kind_display(),
        "is_builtin": sc.is_builtin,
        "is_active": sc.is_active,
        "enabled": sc.enabled,
        "station_id": sc.station_id,
        "station_name": sc.station.name if sc.station_id else "",
        "avg_window": sc.avg_window,
        "trigger_mode": sc.trigger_mode,
        "trigger_n": sc.trigger_n,
        "sampler_sensor_id": sc.sampler_sensor_id,
    }
    if full:
        data["parameters"] = [{
            "parameter_id": p.parameter_id,
            "parameter_name": p.parameter.parameter_name if p.parameter_id else "",
            "min_value": p.min_value,
            "max_value": p.max_value,
            "ministry_param_code": p.ministry_param_code,
            "enabled": p.enabled,
        } for p in sc.parameters.select_related("parameter").all()]
        data["steps"] = [{
            "order": s.order,
            "label": s.label,
            "after_seconds": s.after_seconds,
            "require_still_exceeded": s.require_still_exceeded,
            "actions": s.actions or [],
        } for s in sc.steps.order_by("order")]
    return data


@login_required
def scenario_list(request):
    """Senaryo kütüphanesi — özet liste."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.models import Scenario

    items = [
        _serialize_scenario(sc)
        for sc in Scenario.objects.select_related("station").all()
    ]
    return JsonResponse({"results": items})


@login_required
def scenario_detail(request):
    """Tek senaryonun tam tanımı (meta + parametreler + adımlar)."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.models import Scenario

    sc = (
        Scenario.objects
        .select_related("station")
        .filter(pk=request.GET.get("id"))
        .first()
    )
    if sc is None:
        return JsonResponse({"ok": False, "error": "Senaryo bulunamadı."}, status=404)
    return JsonResponse({"ok": True, "scenario": _serialize_scenario(sc, full=True)})


@login_required
def scenario_save(request):
    """Senaryo oluştur/güncelle — meta + parametreler + adımlar tek payload.

    Parametre ve adım setleri tamamen yeniden yazılır (sil + yeniden oluştur).
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from django.db import transaction

    from api.models import Parameter as P, Sensor as S, Station as St
    from sais_domain.models import Scenario, ScenarioParameter, ScenarioStep

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Senaryo adı zorunlu."}, status=400)

    kind = data.get("kind") or Scenario.KIND_AUTO
    if kind not in (Scenario.KIND_AUTO, Scenario.KIND_MINISTRY):
        return JsonResponse({"ok": False, "error": "Geçersiz senaryo türü."}, status=400)

    station = None
    if data.get("station_id"):
        station = St.objects.filter(pk=data.get("station_id")).first()
        if station is None:
            return JsonResponse({"ok": False, "error": "İstasyon bulunamadı."}, status=400)

    want_active = bool(data.get("is_active"))
    if want_active and station is None:
        return JsonResponse({"ok": False, "error": "Aktif etmek için istasyon seçin."}, status=400)

    sampler_sensor = None
    if data.get("sampler_sensor_id"):
        sampler_sensor = S.objects.filter(pk=data.get("sampler_sensor_id")).first()

    try:
        trigger_n = int(data.get("trigger_n") or 1)
    except (TypeError, ValueError):
        trigger_n = 1

    sc_id = data.get("id")
    with transaction.atomic():
        if sc_id:
            sc = Scenario.objects.filter(pk=sc_id).first()
            if sc is None:
                return JsonResponse({"ok": False, "error": "Senaryo bulunamadı."}, status=404)
        else:
            sc = Scenario(created_by=request.user)

        sc.name = name
        sc.description = (data.get("description") or "").strip()
        sc.kind = kind
        sc.station = station
        sc.enabled = bool(data.get("enabled", True))
        sc.avg_window = data.get("avg_window") or Scenario.WINDOW_15M
        sc.trigger_mode = data.get("trigger_mode") or Scenario.TRIGGER_ANY
        sc.trigger_n = trigger_n
        sc.sampler_sensor = sampler_sensor
        sc.save()

        # Parametreleri yeniden yaz (yalnız auto için anlamlı, ama her durumda set'i uygula).
        sc.parameters.all().delete()
        for prow in (data.get("parameters") or []):
            pid = prow.get("parameter_id")
            param = P.objects.filter(pk=pid).first() if pid else None
            if param is None:
                continue
            mn, mx = prow.get("min_value"), prow.get("max_value")
            has_min = mn not in ("", None)
            has_max = mx not in ("", None)
            if sc.kind == Scenario.KIND_AUTO and not has_min and not has_max:
                return JsonResponse(
                    {"ok": False, "error": f"{param.parameter_name}: Min veya Max zorunlu."},
                    status=400,
                )
            ScenarioParameter.objects.create(
                scenario=sc, parameter=param,
                min_value=float(mn) if has_min else None,
                max_value=float(mx) if has_max else None,
                ministry_param_code=(prow.get("ministry_param_code") or "").strip(),
                enabled=bool(prow.get("enabled", True)),
            )

        # Adımları yeniden yaz.
        sc.steps.all().delete()
        for i, srow in enumerate(data.get("steps") or []):
            try:
                after = int(srow.get("after_seconds") or 0)
            except (TypeError, ValueError):
                after = 0
            ScenarioStep.objects.create(
                scenario=sc,
                order=i,
                label=(srow.get("label") or "").strip(),
                after_seconds=after,
                require_still_exceeded=bool(srow.get("require_still_exceeded")),
                actions=srow.get("actions") or [],
            )

        if want_active:
            sc.activate()

    return JsonResponse({"ok": True, "id": sc.pk})


@login_required
def scenario_delete(request):
    """Senaryo sil — yerleşik şablonlar silinemez."""
    denied = _require_operator(request)
    if denied:
        return denied
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from sais_domain.models import Scenario

    sc_id = request.GET.get("id")
    if request.method == "POST":
        import json
        try:
            sc_id = json.loads(request.body or "{}").get("id", sc_id)
        except (ValueError, TypeError):
            pass

    sc = Scenario.objects.filter(pk=sc_id).first()
    if sc is None:
        return JsonResponse({"ok": False, "error": "Senaryo bulunamadı."}, status=404)
    if sc.is_builtin:
        return JsonResponse({"ok": False, "error": "Yerleşik şablon silinemez."}, status=400)
    sc.delete()
    return JsonResponse({"ok": True})


@login_required
def scenario_activate(request):
    """Senaryoyu aktif yap (aynı istasyon+tür için diğerlerini pasifler)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from sais_domain.models import Scenario

    try:
        sc_id = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        sc_id = None

    sc = Scenario.objects.filter(pk=sc_id).first()
    if sc is None:
        return JsonResponse({"ok": False, "error": "Senaryo bulunamadı."}, status=404)
    if sc.station_id is None:
        return JsonResponse({"ok": False, "error": "Aktif etmeden önce istasyon atayın."}, status=400)
    sc.activate()
    return JsonResponse({"ok": True})


@login_required
def scenario_status(request):
    """Canlı durum — aktif senaryolar + açık run'lar + güncel ortalamalar."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.models import Scenario, ScenarioRun, ScenarioRunLog

    runs = []
    open_runs = (
        ScenarioRun.objects
        .filter(status=ScenarioRun.STATUS_IN_PROGRESS)
        .select_related("scenario", "station")
        .order_by("-trigger_at")
    )
    for r in open_runs:
        steps = list(r.scenario.steps.order_by("order"))
        total = len(steps)
        current = next((s for s in steps if s.order > r.last_step_order), None)
        runs.append({
            "id": r.pk,
            "scenario": r.scenario.name,
            "kind": r.scenario.kind,
            "is_ministry": r.is_ministry,
            "station": r.station.name if r.station_id else "",
            "trigger_at": timezone.localtime(r.trigger_at).strftime("%d.%m.%Y %H:%M:%S"),
            "last_step_order": r.last_step_order,
            "total_steps": total,
            "current_step": current.label if current else "",
            "sample_code": r.sample_code,
            "triggered_parameters": r.triggered_parameters,
        })

    active = [
        _serialize_scenario(sc)
        for sc in Scenario.objects.select_related("station").filter(is_active=True)
    ]
    last_eval = (
        ScenarioRunLog.objects.filter(kind=ScenarioRunLog.KIND_EVAL)
        .order_by("-created_at").first()
    )
    return JsonResponse({
        "ok": True,
        "active_scenarios": active,
        "open_runs": runs,
        "latest_averages": last_eval.averages if last_eval else {},
        "latest_eval_at": (
            timezone.localtime(last_eval.created_at).strftime("%d.%m.%Y %H:%M:%S")
            if last_eval else None
        ),
    })


@login_required
def scenario_history(request):
    """Son senaryo log kayıtları (DataTables)."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.models import ScenarioRunLog

    qs = (
        ScenarioRunLog.objects
        .select_related("run", "run__scenario", "station")
        .order_by("-created_at")[:1000]
    )
    items = [{
        "created_at": timezone.localtime(l.created_at).strftime("%d.%m.%Y %H:%M:%S"),
        "ts": int(timezone.localtime(l.created_at).timestamp()),
        "station": l.station.name if l.station_id else "",
        "scenario": l.run.scenario.name if (l.run_id and l.run.scenario_id) else "",
        "kind": l.get_kind_display(),
        "step_order": l.step_order if l.step_order is not None else "",
        "averages": l.averages or {},
        "message": l.message,
        "skipped_reason": l.skipped_reason,
    } for l in qs]
    return JsonResponse({"results": items})


@login_required
def scenario_request_ministry(request):
    """Bakanlık numune talebini başlat (aktif ministry senaryosu için run açar)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import Station as St
    from sais_domain.scenario_engine import request_ministry

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    code = (data.get("code") or "").strip()
    if not code:
        return JsonResponse({"ok": False, "error": "Numune kodu zorunlu."}, status=400)

    station = St.objects.filter(pk=data.get("station_id")).first()
    if station is None:
        return JsonResponse({"ok": False, "error": "İstasyon seçin."}, status=400)

    result, run = request_ministry(station, code)
    if result == "no_scenario":
        return JsonResponse(
            {"ok": False, "error": "Bu istasyon için aktif Bakanlık senaryosu yok."},
            status=400,
        )
    if result == "exists":
        return JsonResponse({
            "ok": False,
            "error": (
                f"Şu an devam eden bir Bakanlık talebi var (kod: {run.sample_code or '—'}). "
                f"Yeni talep için önce mevcut çalışmayı 'Durum' sekmesinden iptal edin."
            ),
            "existing_run_id": run.pk,
        }, status=409)
    return JsonResponse({"ok": True, "run_id": run.pk})


@login_required
def scenario_cancel_run(request):
    """Açık bir senaryo çalışmasını iptal eder (numune alıcıyı kapatır)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from sais_domain.scenario_engine import cancel_run

    try:
        run_id = json.loads(request.body or "{}").get("run_id")
    except (ValueError, TypeError):
        run_id = None

    run = cancel_run(run_id)
    if run is None:
        return JsonResponse({"ok": False, "error": "Açık çalışma bulunamadı."}, status=404)
    return JsonResponse({"ok": True})


@login_required
def scenario_digital_sensors(request):
    """İstasyonun dijital çıkış sensörleri — numune alıcı override dropdown'u."""
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        station_id = int(request.GET.get("station") or 0) or None
    except (TypeError, ValueError):
        station_id = None
    if not station_id:
        return JsonResponse({"results": []})

    sensors = (
        Sensor.objects
        .filter(connection__station_id=station_id, sensor_type__in=(2, 3))
        .select_related("parameter")
        .order_by("parameter__parameter_name", "id")
    )
    items = [{
        "id": s.id,
        "text": (s.parameter.parameter_name if s.parameter_id else None) or f"Sensör {s.id}",
    } for s in sensors]
    return JsonResponse({"results": items})


