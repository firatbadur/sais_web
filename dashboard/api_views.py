"""Dashboard home sayfası AJAX endpoint'leri.

Browser JS her 5-30 sn'de bu endpoint'lerden JSON çekip widget'ları yeniler.
Tüm endpoint'ler login_required; role check client-side değil server-side.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone

from api.events import EventType, log_event
from api.models import (
    Connection,
    Parameter,
    PowerOff,
    Reading,
    ReadingFifteenMin,
    ReadingHourly,
    Sensor,
    SensorLatest,
    SystemLog,
)


# --------------------------------------------------------------------------- #
# Mimik (Kabin İzleme) — eleman → parametre kodu eşlemesi
# --------------------------------------------------------------------------- #
# Eşleme Sensor.parameter.parameter_name üzerinden yapılır. (Parameter.station
# güvenilmez — parametreler her zaman sensörlerden çekilir.) Kodlar
# `seed_initial_data.py` ile senkron tutulmalıdır.
MIMIC_ANALYZERS = ["pH", "CozunmusOksijen", "Iletkenlik", "KOi", "AKM", "Sicaklik", "KabinSicaklik"]
MIMIC_FLOW = ["AkisHizi", "Debi"]
MIMIC_DIGITALS = [
    "Pompa1", "Pompa2", "Yikama", "HaftalikYikama", "Bakim", "NumuneAlma",
    "Desarj", "Ups", "Enerji", "AcilStop", "Kapi", "ManuelYikama", "SuYok", "SuBasti",
]
MIMIC_STALE_SECONDS = 300    # SensorLatest bayatlık eşiği (okuma "veri yok" sayılır)
MIMIC_OFFLINE_SECONDS = 900  # Connection bayatlık → iletişim arızası (PLC arıza ışığı)


def _humanize_ago_tr(dt, now=None):
    """Geçmiş bir an için kısa Türkçe görece zaman: 'az önce', '1 dk önce',
    '2 sa önce', '3 gün önce', '4 ay önce', '1 yıl önce'. dt None ise None."""
    if not dt:
        return None
    now = now or timezone.now()
    secs = int((now - dt).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return "az önce"
    mins = secs // 60
    if mins < 60:
        return f"{mins} dk önce"
    hours = mins // 60
    if hours < 24:
        return f"{hours} sa önce"
    days = hours // 24
    if days < 30:
        return f"{days} gün önce"
    months = days // 30
    if months < 12:
        return f"{months} ay önce"
    return f"{days // 365} yıl önce"


@login_required
def home_kpis(request):
    """Üst KPI kartları — açık bağlantı, aktif sensör, son saat reading
    sayısı + Bakanlık SIM'e son başarılı veri iletimi (görece zaman)."""
    now = timezone.now()
    last_hour = now - timedelta(hours=1)

    data = {
        "sensor_count": Sensor.objects.filter(is_active=True).count(),
        "connection_count": Connection.objects.filter(is_enabled=True).count(),
        "readings_last_hour": Reading.objects.filter(time_iso__gte=last_hour).count(),
    }

    # UPS durumu — dijital girişler (sensor_type 2/3) içinde adı "UPS" geçen bir
    # sensör tanımlıysa kart görünür. Tespit Sensor'den yapılır (henüz hiç okuma
    # alınmamış olabilir, SensorLatest oluşmamış olabilir); değer/aktiflik varsa
    # SensorLatest'ten okunur. Aktifleştiği an (last_change_at) frontend'in
    # 60 dk'lık geri sayımı için verilir.
    data["ups_present"] = False
    data["ups_active"] = None
    data["ups_since"] = None
    try:
        from django.db.models import Q
        ups_sensor = (
            Sensor.objects
            .filter(is_active=True, sensor_type__in=(2, 3))
            .filter(
                Q(parameter__parameter_txt__icontains="ups")
                | Q(parameter__parameter_name__icontains="ups")
            )
            .order_by("display_order", "id")
            .first()
        )
        if ups_sensor:
            data["ups_present"] = True
            latest = SensorLatest.objects.filter(sensor=ups_sensor).first()
            raw = False
            if latest and latest.value is not None:
                raw = bool(latest.value)
                if ups_sensor.digital_inverse:
                    raw = not raw
            data["ups_active"] = raw
            if raw and latest and latest.last_change_at:
                data["ups_since"] = latest.last_change_at.isoformat()
    except Exception:  # noqa: BLE001 — UPS verisi olmadan da KPI'lar çalışsın
        pass

    # Son veri iletimi (Sistem Kontrol'deki "Bakanlık SIM — Son İletim" ile aynı)
    try:
        from sais_domain.models import SystemSwitch
        switch = SystemSwitch.load()
        data["last_sim_ago"] = _humanize_ago_tr(switch.last_sim_success_at, now) or "—"
        data["last_sim_at"] = (
            switch.last_sim_success_at.isoformat() if switch.last_sim_success_at else None
        )
        data["last_sim_readtime"] = switch.last_sim_success_readtime or None
        data["sim_enabled"] = switch.sim_enabled
    except Exception:  # noqa: BLE001 — KPI'lar SIM verisi olmadan da çalışsın
        data["last_sim_ago"] = "—"
        data["last_sim_at"] = None
        data["last_sim_readtime"] = None
        data["sim_enabled"] = None

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
            "parameter_name": (parameter.display_name if parameter else None) or "-",
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
def mimic_state(request):
    """SCADA mimik (Kabin İzleme) — bir istasyonun tüm canlı durumu tek JSON'da.

    ``?station=<id>``. Mimik sayfası ~4 sn'de bir poll'lar. Eşleme parametre kodu
    (``Sensor.parameter.parameter_name``, bkz. ``MIMIC_*`` listeleri) üzerinden
    yapılır; analizör/akış/dijital değerler + yıkama + numune senaryosu +
    iletişim arıza durumu döner. Dijital çıkışlar için ``sensor_id`` döner →
    sayfadaki tıkla-komut akışı mevcut ``digital_output_command``'a POST'lar.

    Salt-okuma; ``login_required`` yeterli (sayfa zaten rol=1 ile kısıtlı).
    """
    now = timezone.now()
    try:
        station_id = int(request.GET.get("station"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Geçersiz station."}, status=400)

    # İstasyonun aktif sensörleri → parametre koduna indeksli son snapshot.
    latest_qs = (
        SensorLatest.objects
        .select_related("sensor", "sensor__parameter", "sensor__connection", "status")
        .filter(sensor__connection__station_id=station_id, sensor__is_active=True)
        .order_by("sensor__display_order", "sensor__id")
    )
    by_code = {}
    for latest in latest_qs:
        param = getattr(latest.sensor, "parameter", None)
        code = param.parameter_name if param else None
        if code and code not in by_code:
            by_code[code] = latest

    def _stale(latest):
        if not latest or not latest.readtime:
            return True
        return (now - latest.readtime).total_seconds() > MIMIC_STALE_SECONDS

    def _meta(code, latest):
        # Önce sensörün parametresi; sensör yoksa kod adıyla Parameter meta'sı.
        if latest:
            param = getattr(latest.sensor, "parameter", None)
            if param:
                return param
        return Parameter.objects.filter(parameter_name=code).first()

    # Analizörler
    analyzers = []
    for code in MIMIC_ANALYZERS:
        latest = by_code.get(code)
        meta = _meta(code, latest)
        analyzers.append({
            "code": code,
            "name": meta.display_name if meta else code,
            "value": latest.value if latest else None,
            "unit": ((meta.unit_txt or meta.unit) if meta else "") or "",
            "min": meta.min_range if meta else None,
            "max": meta.max_range if meta else None,
            "gec_min": meta.gec_min if meta else None,
            "gec_max": meta.gec_max if meta else None,
            "status_code": latest.status.code if latest and latest.status else None,
            "quality": latest.quality if latest else None,
            "stale": _stale(latest),
            "present": latest is not None,
        })

    # Akış / debimetre — ilk eşleşen kod
    flow = {"code": None, "value": None, "unit": "", "stale": True, "present": False}
    for code in MIMIC_FLOW:
        latest = by_code.get(code)
        if latest:
            meta = _meta(code, latest)
            flow = {
                "code": code,
                "value": latest.value,
                "unit": ((meta.unit_txt or meta.unit) if meta else "") or "",
                "stale": _stale(latest),
                "present": True,
            }
            break

    # Dijitaller (durum + sensor_id → tıkla-komut)
    digitals = []
    for code in MIMIC_DIGITALS:
        latest = by_code.get(code)
        if not latest:
            digitals.append({
                "code": code, "name": code, "present": False, "is_active": None,
                "sensor_id": None, "sensor_type": None, "controllable": False,
                "status_code": None, "stale": True,
            })
            continue
        sensor = latest.sensor
        meta = getattr(sensor, "parameter", None)
        raw = bool(latest.value) if latest.value is not None else False
        if sensor.digital_inverse:
            raw = not raw
        digitals.append({
            "code": code,
            "name": meta.display_name if meta else code,
            "present": True,
            "is_active": raw,
            "sensor_id": sensor.pk,
            "sensor_type": sensor.sensor_type,
            # Yalnız dijital output (sensor_type=3) komutlanabilir.
            "controllable": sensor.sensor_type == 3 and sensor.is_active,
            "status_code": latest.status.code if latest.status else None,
            "stale": _stale(latest),
        })

    # Yıkama durumu (SystemSwitch singleton — fleet geneli)
    from sais_domain.models import SystemSwitch
    switch = SystemSwitch.load()
    wash_code = switch.active_wash_status_code()
    wash = {
        "active": wash_code is not None,
        "kind": switch.wash_active_kind if wash_code else None,
        "status_code": wash_code,
        "remaining_seconds": switch.wash_remaining_seconds() if wash_code else None,
    }

    # Numune senaryosu — bu istasyonda devam eden run var mı
    sampling = {"active": False, "is_ministry": False, "step": None, "sample_code": None}
    try:
        from sais_domain.models import ScenarioRun
        run = (
            ScenarioRun.objects
            .filter(scenario__station_id=station_id, status=ScenarioRun.STATUS_IN_PROGRESS)
            .order_by("-trigger_at").first()
        )
        if run:
            sampling = {
                "active": True,
                "is_ministry": run.is_ministry,
                "step": run.last_step_order,
                "sample_code": run.sample_code or None,
            }
    except Exception:  # noqa: BLE001 — numune verisi olmadan da mimik çalışsın
        pass

    # İletişim arızası → PLC arıza ışığı: istasyonun açık bağlantılarından biri
    # hiç poll'lanmamış ya da bayatsa (eşik aşıldı). Ek sinyal: status kodu 8.
    comm_error = False
    has_enabled = False
    for conn in Connection.objects.filter(station_id=station_id, is_enabled=True):
        has_enabled = True
        if (not conn.last_polled_at
                or (now - conn.last_polled_at).total_seconds() > MIMIC_OFFLINE_SECONDS):
            comm_error = True
            break
    any_status_8 = (
        any(a["status_code"] == 8 for a in analyzers if a["status_code"] is not None)
        or any(d["status_code"] == 8 for d in digitals if d["status_code"] is not None)
    )

    return JsonResponse({
        "ok": True,
        "station_id": station_id,
        "online": has_enabled and not comm_error,
        "analyzers": analyzers,
        "flow": flow,
        "digitals": digitals,
        "wash": wash,
        "sampling": sampling,
        "fault": {
            "comm_error": comm_error,
            "any_status_8": any_status_8,
            "no_connection": not has_enabled,
        },
        "server_time": now.isoformat(),
    })


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

    if created:
        # Manuel dijital çıkış işlemi bir olaydır — kim, nereden, ne yaptı.
        from api.events import EventType, log_event

        action_label = "Start (AÇ)" if action == "start" else "Stop (KAPAT)"
        station = getattr(sensor.connection, "station", None)
        log_event(
            EventType.COMMAND,
            f"Manuel dijital çıkış: {sensor.name or ('sensör#' + str(sensor.pk))} → {action_label}",
            severity="warning", request=request, station=station,
        )

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
        label = sensor.parameter.display_name if sensor.parameter else f"Sensor-{sensor.id}"

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
    """Son olaylar feed'i — SystemLog + PowerOff + kalite=bad son okumalar.

    Normal kullanıcı (rol=3) kullanıcı hareketlerini (giriş/çıkış/komut/
    yapılandırma/kullanıcı yönetimi/yedekleme/lisans) GÖREMEZ; yalnızca IO
    değişikliklerini (dijital giriş/çıkış + PowerOff + bad reading) görür.

    Operatör (rol=2) sistem yöneticisinin (rol=1) hiçbir hareketini göremez;
    admin aktörlü SystemLog kayıtları feed'den çıkarılır.
    """
    from api.events import EventType
    from .permissions import ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER, can_view_admin_events

    now = timezone.now()

    events = []

    log_qs = SystemLog.objects.select_related("type", "user").order_by("-time_iso")
    role = getattr(request.user, "rol", None)
    if role == ROLE_USER and not request.user.is_superuser:
        # Salt-izleme: sadece IO değişiklikleri (kullanıcı audit trail'i gizli)
        log_qs = log_qs.filter(type__name=EventType.DIGITAL_IO)
    elif role == ROLE_OPERATOR and not can_view_admin_events(request.user):
        # Operatör sistem yöneticisinin hareketlerini göremez
        log_qs = log_qs.exclude(user__rol=ROLE_ADMIN).exclude(user__is_superuser=True)

    for log in log_qs[:15]:
        actor = log.user.get_username() if log.user_id else (log.username or "")
        meta_bits = []
        if actor:
            meta_bits.append(actor)
        if log.ip_address:
            meta_bits.append(log.ip_address)
        detail = log.description or ""
        if meta_bits:
            detail = (detail + " · " if detail else "") + " / ".join(meta_bits)
        events.append({
            "kind": "system_log",
            "severity": log.severity or "info",
            "time": log.time_iso.isoformat() if log.time_iso else None,
            "title": log.type.name if log.type else "Olay",
            "detail": detail,
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
            sensor_label = param.display_name if param else f"Sensor-{r.sensor.id}"
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
        # Eksik veri yeniden gönderim servisi durumu (6 saatte bir).
        "sim_enabled": switch.sim_enabled,
        "missing_check_at": (
            switch.last_missing_check_at.isoformat() if switch.last_missing_check_at else None
        ),
        "missing_found_count": switch.last_missing_found_count,
        "missing_resent_count": switch.last_missing_resent_count,
        "missing_error": switch.last_missing_error or None,
    })


@login_required
def connection_toggle(request):
    """SCADA bağlantısını TCP/IP düzeyinde aç/kapa (Sistem Kontrol kartı).

    POST JSON: ``{"connection_id": int, "enabled": bool}``
    Yanıt: ``{"ok": bool, "enabled": bool, "workers": int, "closed": int,
             "message": str|None}``

    Kapatma (``enabled=False``):
      1. ``Connection.is_enabled=False`` → ``dispatch_polls`` bu bağlantıyı bir
         daha enqueue etmez (worker tekrar bağlanmaz).
      2. Worker pool'larındaki açık socket'ler broadcast ile kapatılır →
         PLC'nin tek-bağlantı slotu boşalır (Modbus Poll vb. ile bağlanılabilir).

    Açma (``enabled=True``): yalnız ``is_enabled=True`` yapılır; bir sonraki
    polling cycle'ında (≤ poll_interval) worker bağlantıyı yeniden açar.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    try:
        data = json.loads(request.body or "{}")
        conn_id = int(data.get("connection_id"))
        enabled = bool(data.get("enabled"))
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz istek."}, status=400)

    conn = Connection.objects.filter(pk=conn_id).first()
    if conn is None:
        return JsonResponse({"ok": False, "error": "Bağlantı bulunamadı."}, status=404)

    conn.is_enabled = enabled
    conn.save(update_fields=["is_enabled"])

    from api.events import EventType, log_event
    log_event(
        EventType.CONFIG,
        f"Bağlantı {'açıldı' if enabled else 'kapatıldı'}: {conn}",
        severity="info" if enabled else "warning", request=request,
    )

    workers = closed = 0
    if not enabled:
        # Açık socket'leri kapat ki PLC slotu hemen boşalsın. Tüm worker'lara
        # broadcast; bu bağlantı artık is_enabled=False olduğu için yeniden
        # açılmaz, diğer aktif bağlantılar ≤ poll_interval içinde geri bağlanır.
        from scada_io.pool_control import broadcast_close_pools
        summary = broadcast_close_pools(reason=f"toggle-off:{conn_id}")
        workers = summary["workers"]
        closed = summary["closed"]

    return JsonResponse({
        "ok": True,
        "enabled": enabled,
        "workers": workers,
        "closed": closed,
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
            "text": p.display_name,
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
    denied = _require_operator(request)
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
    """Bir .dump dosyasını indirir. Path traversal'a karşı sıkı doğrulama."""
    denied = _require_operator(request)
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


@login_required
def api_log_detail(request, pk):
    """Tek bir ApiLog satırının tam detayı (header/body) — API Logları modal'ı.

    Gelen Basic auth kimliği (kim hangi kullanıcı adı/şifre ile denedi)
    `request_headers` içindeki `Authorization-Basic-Decoded` anahtarında durur;
    bu endpoint onu olduğu gibi döndürür, frontend modal'da gösterir.
    """
    denied = _require_admin(request)
    if denied:
        return denied

    from api.models import ApiLog

    log = ApiLog.objects.select_related("user").filter(pk=pk).first()
    if not log:
        return JsonResponse({"ok": False, "error": "Kayıt bulunamadı."}, status=404)

    return JsonResponse({
        "ok": True,
        "log": {
            "id": log.id,
            "created_at": log.created_at.strftime("%d.%m.%Y %H:%M:%S") if log.created_at else "",
            "direction": log.direction,
            "method": log.method,
            "url": log.url,
            "query_string": log.query_string,
            "response_status": log.response_status,
            "duration_ms": log.duration_ms,
            "remote_ip": log.remote_ip or "",
            "user": (log.user.get_username() if log.user else "") or log.source_component or "",
            "user_agent": log.user_agent or "",
            "request_headers": log.request_headers or "",
            "request_body": log.request_body or "",
            "response_body": log.response_body or "",
            "error_message": log.error_message or "",
        },
    })


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
    denied = _require_operator(request)
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
    denied = _require_operator(request)
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
    denied = _require_operator(request)
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
    denied = _require_operator(request)
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
    denied = _require_operator(request)
    if denied:
        return denied

    from users.models import CustomUser

    from .permissions import ROLE_MINISTRY

    results = []
    # Bakanlık (rol=4) yalnız-API kullanıcısı operasyonel bildirim almaz — listede yok.
    recipients_qs = (
        CustomUser.objects.filter(is_active=True)
        .exclude(rol=ROLE_MINISTRY)
        .order_by("first_name", "username")
    )
    for u in recipients_qs:
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

    denied = _require_operator(request)
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

    denied = _require_operator(request)
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
    if s.parameter and (s.parameter.parameter_txt or s.parameter.parameter_name):
        return s.parameter.display_name
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
        {"id": p.id, "text": p.display_name}
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
            "channel": (r.parameter.display_name if r.parameter else "") if r.rule_type == AlarmRule.RULE_ANALOG else "",
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
        return JsonResponse({"ok": False, "error": "Tesis seçin."}, status=400)

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
        "sampler_required": sc.requires_sampler(),
        "sampler_missing": sc.sampler_missing(),
    }
    if full:
        data["parameters"] = [{
            "parameter_id": p.parameter_id,
            "parameter_name": p.parameter.display_name if p.parameter_id else "",
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
        for sc in Scenario.objects.select_related("station").prefetch_related("steps").all()
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
            return JsonResponse({"ok": False, "error": "Tesis bulunamadı."}, status=400)

    want_active = bool(data.get("is_active"))
    if want_active and station is None:
        return JsonResponse({"ok": False, "error": "Aktif etmek için tesis seçin."}, status=400)

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
                    {"ok": False, "error": f"{param.display_name}: Min veya Max zorunlu."},
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
        return JsonResponse({"ok": False, "error": "Aktif etmeden önce tesis atayın."}, status=400)
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
        return JsonResponse({"ok": False, "error": "Tesis seçin."}, status=400)

    result, run = request_ministry(station, code)
    if result == "no_scenario":
        return JsonResponse(
            {"ok": False, "error": "Bu tesis için aktif Bakanlık senaryosu yok."},
            status=400,
        )
    if result == "no_sampler":
        return JsonResponse(
            {"ok": False, "error": "Bakanlık senaryosunda numune alıcı sensör tanımlı değil."},
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


# --------------------------------------------------------------------------- #
# Senaryo Tasarımcı (node-graph) AJAX endpoint'leri — demo
# --------------------------------------------------------------------------- #

@login_required
def graph_list(request):
    """Kayıtlı senaryo tasarımları (şablonlar dahil)."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.models import ScenarioGraph

    items = [{
        "id": g.id,
        "name": g.name,
        "is_template": g.is_template,
        "station_id": g.station_id,
        "updated_at": timezone.localtime(g.updated_at).strftime("%d.%m.%Y %H:%M"),
    } for g in ScenarioGraph.objects.all()]
    return JsonResponse({"results": items})


@login_required
def graph_get(request):
    """Tek tasarımın tam tanımı (graph JSON dahil)."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.models import ScenarioGraph

    g = ScenarioGraph.objects.filter(pk=request.GET.get("id")).first()
    if g is None:
        return JsonResponse({"ok": False, "error": "Tasarım bulunamadı."}, status=404)
    return JsonResponse({"ok": True, "graph": {
        "id": g.id, "name": g.name, "description": g.description,
        "station_id": g.station_id, "is_template": g.is_template, "graph": g.graph,
    }})


@login_required
def graph_save(request):
    """Senaryo tasarımı oluştur/güncelle (Drawflow export JSON)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import Station as St
    from sais_domain.models import ScenarioGraph

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Tasarım adı zorunlu."}, status=400)

    graph = data.get("graph")
    if not isinstance(graph, dict):
        return JsonResponse({"ok": False, "error": "Geçersiz graph verisi."}, status=400)

    station = None
    if data.get("station_id"):
        station = St.objects.filter(pk=data.get("station_id")).first()

    g_id = data.get("id")
    if g_id:
        g = ScenarioGraph.objects.filter(pk=g_id).first()
        if g is None:
            return JsonResponse({"ok": False, "error": "Tasarım bulunamadı."}, status=404)
    else:
        g = ScenarioGraph(created_by=request.user)

    g.name = name
    g.description = (data.get("description") or "").strip()
    g.station = station
    g.graph = graph
    g.save()
    return JsonResponse({"ok": True, "id": g.pk})


@login_required
def graph_delete(request):
    """Senaryo tasarımı sil — yerleşik şablonlar silinemez."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from sais_domain.models import ScenarioGraph

    try:
        g_id = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        g_id = None

    g = ScenarioGraph.objects.filter(pk=g_id).first()
    if g is None:
        return JsonResponse({"ok": False, "error": "Tasarım bulunamadı."}, status=404)
    if g.is_template:
        return JsonResponse({"ok": False, "error": "Yerleşik şablon silinemez."}, status=400)
    g.delete()
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
        "text": (s.parameter.display_name if s.parameter_id else None) or f"Sensör {s.id}",
    } for s in sensors]
    return JsonResponse({"results": items})


# --------------------------------------------------------------------------- #
# İnteraktif Kalibrasyon AJAX endpoint'leri
# --------------------------------------------------------------------------- #

@login_required
def calibration_params(request):
    """İstasyonun kalibre edilebilir (analog girişli) parametreleri.

    Her parametre için kalibrasyonda kullanılacak birincil analog sensörün
    id'si, birimi ve aralığı (min/max) döner — sihirbaz adım 1 dropdown'u +
    canlı tolerans bandı için.
    """
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        station_id = int(request.GET.get("station") or 0) or None
    except (TypeError, ValueError):
        station_id = None
    if not station_id:
        return JsonResponse({"results": []})

    # Analog giriş sensörü (sensor_type=0) olan parametreler; her parametre için
    # ilk analog sensörü kalibrasyon hedefi al.
    sensors = (
        Sensor.objects
        .filter(connection__station_id=station_id, sensor_type=0, parameter__isnull=False)
        .select_related("parameter")
        .order_by("parameter__parameter_name", "id")
    )
    seen = set()
    items = []
    for s in sensors:
        if s.parameter_id in seen:
            continue
        seen.add(s.parameter_id)
        p = s.parameter
        items.append({
            "sensor_id": s.id,
            "parameter_id": p.id,
            "text": p.display_name,
            "unit": p.unit_txt or p.unit or "",
            "min_range": p.min_range,
            "max_range": p.max_range,
        })
    return JsonResponse({"results": items})


@login_required
def calibration_live(request):
    """Bir sensörün anlık değeri — daldırma algılama + örnekleme polling'i.

    `SensorLatest` snapshot'ından değer/status/okuma zamanı döner. Sihirbaz
    adım 2 her 1-2 sn'de bunu çağırıp daldırmayı algılar ve süre boyunca
    örnek toplar.
    """
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        sensor_id = int(request.GET.get("sensor") or 0) or None
    except (TypeError, ValueError):
        sensor_id = None
    if not sensor_id:
        return JsonResponse({"ok": False, "error": "Sensör seçilmedi."}, status=400)

    latest = (
        SensorLatest.objects
        .select_related("status", "sensor", "sensor__parameter")
        .filter(sensor_id=sensor_id)
        .first()
    )
    if latest is None:
        return JsonResponse({
            "ok": True, "value": None, "status": None, "status_code": None,
            "readtime": None, "quality": None,
        })
    return JsonResponse({
        "ok": True,
        "value": latest.value,
        "status": latest.status.name if latest.status_id else None,
        "status_code": latest.status.code if latest.status_id else None,
        "quality": latest.quality,
        "readtime": timezone.localtime(latest.readtime).strftime("%d.%m.%Y %H:%M:%S")
        if latest.readtime else None,
    })


@login_required
def calibration_save(request):
    """Kalibrasyon sonucunu `Calibration` tablosuna kaydeder.

    Payload: sensor_id, type (0/1/2), period (sn), cal_ref, cal_average,
    cal_std, is_valid. Kullanıcı request.user'dan alınır.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import Calibration

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    sensor = Sensor.objects.filter(pk=data.get("sensor_id")).first()
    if sensor is None:
        return JsonResponse({"ok": False, "error": "Sensör bulunamadı."}, status=400)

    def _num(key):
        v = data.get(key)
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    try:
        cal_type = int(data.get("type"))
    except (TypeError, ValueError):
        cal_type = None
    if cal_type not in (0, 1, 2):
        return JsonResponse({"ok": False, "error": "Geçersiz kalibrasyon tipi."}, status=400)

    try:
        period = int(data.get("period") or 60)
    except (TypeError, ValueError):
        period = 60

    cal = Calibration.objects.create(
        sensor=sensor,
        type=cal_type,
        period=period,
        cal_ref=_num("cal_ref"),
        cal_average=_num("cal_average"),
        cal_std=_num("cal_std"),
        is_valid=bool(data.get("is_valid")),
        user=request.user,
    )
    return JsonResponse({
        "ok": True,
        "id": cal.id,
        "time_iso": timezone.localtime(cal.time_iso).strftime("%d.%m.%Y %H:%M:%S"),
    })


@login_required
def calibration_send_sim(request):
    """Kaydedilmiş bir kalibrasyonu Bakanlık SAIS ``SendCalibration`` ile gönderir."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import Calibration
    from api.serializers import CalibrationResultSerializer
    from sais_domain.clients.sim import SaisSimClient
    from sais_domain.models import SaisCabinet

    try:
        cal_id = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        cal_id = None

    cal = (
        Calibration.objects
        .select_related("sensor__parameter__station", "user")
        .filter(pk=cal_id)
        .first()
    )
    if cal is None:
        return JsonResponse({"ok": False, "error": "Kalibrasyon kaydı bulunamadı."}, status=404)

    station_id = (
        cal.sensor.parameter.station_id
        if cal.sensor_id and cal.sensor.parameter_id else None
    )
    cabinet = SaisCabinet.objects.filter(station_id=station_id).first() if station_id else None
    if cabinet is None:
        return JsonResponse(
            {"ok": False, "error": "Bu tesise bağlı Bakanlık kabini (SIM) tanımlı değil."},
            status=400,
        )

    payload = CalibrationResultSerializer(cal).data
    try:
        client = SaisSimClient(cabinet)
        envelope = client.send_calibration(payload, triggered_by=request.user)
    except Exception as exc:  # noqa: BLE001 — kullanıcıya hata mesajı dön
        return JsonResponse(
            {"ok": False, "error": f"Bakanlık gönderimi başarısız: {exc}"},
            status=502,
        )

    # Bakanlık zarfı: {"result": bool, "message": str, "objects": null}.
    # ``message``'ı (ve red durumunda hatayı) doğrudan kullanıcıya yansıt.
    if isinstance(envelope, dict):
        ministry_ok = bool(envelope.get("result", True))
        ministry_msg = (envelope.get("message") or "").strip()
    else:
        ministry_ok, ministry_msg = True, ""

    if not ministry_ok:
        return JsonResponse(
            {"ok": False, "error": ministry_msg or "Bakanlık gönderimi reddetti."},
            status=502,
        )
    return JsonResponse({"ok": True, "message": ministry_msg, "result": envelope})


# ---------------------------------------------------------------------------
# Takvim Hatırlatıcı (paylaşımlı; sadece dashboard içi bildirim)
# ---------------------------------------------------------------------------
def _serialize_reminder(r):
    """Reminder → JSON (takvim, çan feed'i ve widget ortak kontratı)."""
    local = timezone.localtime(r.remind_at)
    return {
        "id": r.pk,
        "title": r.title,
        "note": r.note or "",
        "remind_at": local.isoformat(),
        "date": local.strftime("%Y-%m-%d"),
        "time": local.strftime("%H:%M"),
        "priority": r.priority,
        "priority_label": r.get_priority_display(),
        "station_id": r.station_id,
        "station": r.station.name if r.station_id and r.station else "",
        "is_done": r.is_done,
        "is_due": (not r.is_done) and r.remind_at <= timezone.now(),
        "created_by": r.created_by.username if r.created_by_id and r.created_by else "",
    }


def _parse_remind_at(raw):
    """'YYYY-MM-DDTHH:MM' / ISO / 'DD.MM.YYYY HH:MM' → TZ-aware datetime (veya None)."""
    from datetime import datetime as _dt

    from django.utils.dateparse import parse_datetime

    if not raw:
        return None
    raw = str(raw).strip()
    dt = parse_datetime(raw)
    if dt is None:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                dt = _dt.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@login_required
def reminders_list(request):
    """Takvim için hatırlatıcı listesi.

    GET ?start=YYYY-MM-DD&end=YYYY-MM-DD (opsiyonel aralık) &include_done=1.
    Paylaşımlı → kullanıcı filtresi yok. En çok 2000 kayıt döner.
    """
    from api.models import Reminder

    qs = Reminder.objects.select_related("station").all()
    start = request.GET.get("start")
    end = request.GET.get("end")
    if start:
        sdt = _parse_remind_at(f"{start} 00:00")
        if sdt:
            qs = qs.filter(remind_at__gte=sdt)
    if end:
        edt = _parse_remind_at(f"{end} 23:59:59")
        if edt:
            qs = qs.filter(remind_at__lte=edt)
    if request.GET.get("include_done") not in ("1", "true", "yes"):
        # Takvimde geçmiş tamamlananları da göstermek isteyebiliriz; default hepsi.
        pass
    items = [_serialize_reminder(r) for r in qs.order_by("remind_at")[:2000]]
    return JsonResponse({"results": items})


@login_required
def reminders_feed(request):
    """Çan ikonu + anasayfa widget'ı için: vadesi gelmiş + yaklaşan hatırlatmalar.

    `count` = vadesi gelmiş (remind_at<=now & tamamlanmamış) sayısı → badge.
    """
    from api.models import Reminder

    now = timezone.now()
    base = Reminder.objects.select_related("station").filter(is_done=False)

    due_qs = base.filter(remind_at__lte=now).order_by("-remind_at")
    upcoming_qs = base.filter(
        remind_at__gt=now, remind_at__lte=now + timedelta(days=14),
    ).order_by("remind_at")

    due = [_serialize_reminder(r) for r in due_qs[:30]]
    upcoming = [_serialize_reminder(r) for r in upcoming_qs[:30]]
    return JsonResponse({
        "count": due_qs.count(),
        "upcoming_count": upcoming_qs.count(),
        "due": due,
        "upcoming": upcoming,
        "server_now": timezone.localtime(now).isoformat(),
    })


@login_required
def reminder_save(request):
    """Hatırlatıcı oluştur / güncelle. POST JSON {id?, title, remind_at, note, priority, station_id}."""
    import json

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    from api.models import Reminder, Station

    title = (data.get("title") or "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Başlık zorunlu."}, status=400)

    remind_at = _parse_remind_at(data.get("remind_at"))
    if remind_at is None:
        return JsonResponse({"ok": False, "error": "Geçerli bir hatırlatma zamanı girin."}, status=400)

    priority = data.get("priority") or Reminder.PRIORITY_NORMAL
    if priority not in dict(Reminder.PRIORITY_CHOICES):
        priority = Reminder.PRIORITY_NORMAL

    station = None
    if data.get("station_id"):
        station = Station.objects.filter(pk=data["station_id"]).first()

    rid = data.get("id")
    if rid:
        r = Reminder.objects.filter(pk=rid).first()
        if not r:
            return JsonResponse({"ok": False, "error": "Hatırlatıcı bulunamadı."}, status=404)
    else:
        r = Reminder(created_by=request.user)

    r.title = title[:160]
    r.note = (data.get("note") or "")
    r.remind_at = remind_at
    r.priority = priority
    r.station = station
    r.save()
    return JsonResponse({"ok": True, "reminder": _serialize_reminder(r)})


@login_required
def reminder_done(request):
    """Tamamlandı işaretle / geri al. POST JSON {id, done}."""
    import json

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    from api.models import Reminder

    r = Reminder.objects.filter(pk=data.get("id")).first()
    if not r:
        return JsonResponse({"ok": False, "error": "Hatırlatıcı bulunamadı."}, status=404)

    done = bool(data.get("done", True))
    r.is_done = done
    if done:
        r.done_at = timezone.now()
        r.done_by = request.user
    else:
        r.done_at = None
        r.done_by = None
    r.save(update_fields=["is_done", "done_at", "done_by", "updated_at"])
    return JsonResponse({"ok": True, "reminder": _serialize_reminder(r)})


@login_required
def reminder_delete(request):
    """Hatırlatıcı sil. POST JSON {id}."""
    import json

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)
    try:
        rid = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    from api.models import Reminder

    deleted, _ = Reminder.objects.filter(pk=rid).delete()
    if not deleted:
        return JsonResponse({"ok": False, "error": "Hatırlatıcı bulunamadı."}, status=404)
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Sensör Ayarları — Scan Grubu + Sensör listeleme & canlı test endpoint'leri
# ---------------------------------------------------------------------------

# read_raw destekleyen Modbus protokolleri (scan grubu testi bu protokollerde
# anlamlı; ASCII custom batch okumaz).
_MODBUS_PROTOCOLS = (
    "modbus_tcp", "modbus_rtu", "modbus_ascii",
    "modbus_rtu_over_tcp", "modbus_ascii_over_tcp",
)


@login_required
def connection_scangroups(request):
    """Bir bağlantının scan gruplarını JSON döner — sensör testi dropdown'u."""
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        conn_id = int(request.GET.get("connection") or 0) or None
    except (TypeError, ValueError):
        conn_id = None
    if not conn_id:
        return JsonResponse({"results": []})

    from api.models import ScanGroup

    groups = ScanGroup.objects.filter(connection_id=conn_id).order_by("slave_id", "start_address")
    items = [{
        "id": g.id,
        "text": f"{g.name} (slave={g.slave_id} fn={g.function} {g.start_address}..{g.end_address})",
    } for g in groups]
    return JsonResponse({"results": items})


@login_required
def connection_sensors(request):
    """Bir bağlantının sensörlerini JSON döner — sensör testi dropdown'u."""
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        conn_id = int(request.GET.get("connection") or 0) or None
    except (TypeError, ValueError):
        conn_id = None
    if not conn_id:
        return JsonResponse({"results": []})

    sensors = (
        Sensor.objects.filter(connection_id=conn_id)
        .select_related("parameter")
        .order_by("display_order", "id")
    )
    items = []
    for s in sensors:
        label = (s.parameter.display_name if s.parameter_id else None) or f"Sensör {s.id}"
        if s.address is not None:
            label = f"{label} — adr {s.address} ({s.data_type or '?'})"
        items.append({"id": s.id, "text": label})
    return JsonResponse({"results": items})


def _safe_value(v):
    """JSON serileştirilemez değerleri (bytes vb.) string'e indir."""
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    try:
        return str(v)
    except Exception:  # noqa: BLE001
        return repr(v)


@login_required
def sensor_test_run(request):
    """Kayıtlı bir sensörü anlık cihazdan oku (canlı test).

    POST JSON {sensor_id}. Reader'ı taze açar (worker pool'a dokunmaz), tek
    okuma yapar, kapatır. Döner: değer + ham register + kalite + hata + süre.
    Cihaza erişilemese bile UI çökmeden quality=bad + hata mesajı döner.
    """
    import json
    import time

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    try:
        sensor_id = int(json.loads(request.body or "{}").get("sensor_id") or 0) or None
    except (ValueError, TypeError):
        sensor_id = None
    if not sensor_id:
        return JsonResponse({"ok": False, "error": "Sensör seçilmedi."}, status=400)

    sensor = (
        Sensor.objects.select_related("connection", "scan_group", "parameter")
        .filter(pk=sensor_id)
        .first()
    )
    if sensor is None:
        return JsonResponse({"ok": False, "error": "Sensör bulunamadı."}, status=404)
    if sensor.connection_id is None:
        return JsonResponse({"ok": False, "error": "Sensörün bağlantısı tanımlı değil."}, status=400)

    from scada_io.readers.factory import build_reader

    started = time.monotonic()
    try:
        reader = build_reader(sensor.connection)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    result = {"value": None, "quality": "bad", "raw": None, "error": ""}
    try:
        if not reader.open():
            result["error"] = reader.last_error or "Bağlantı kurulamadı."
        else:
            rr = reader.read(sensor)
            result["value"] = _safe_value(rr.value)
            result["quality"] = rr.quality
            result["raw"] = _safe_value(rr.raw)
            result["error"] = rr.error or ""
    except Exception as exc:  # noqa: BLE001 — test asla UI'yı kırmasın
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            reader.close()
        except Exception:  # noqa: BLE001
            pass

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return JsonResponse({
        "ok": True,
        "value": result["value"],
        "quality": result["quality"],
        "raw": result["raw"],
        "error": result["error"],
        "elapsed_ms": elapsed_ms,
        "unit": (sensor.parameter.unit_txt or sensor.parameter.unit or "")
                if sensor.parameter_id else "",
        "sensor": str(sensor),
        "protocol": sensor.connection.protocol,
        "slave_id": sensor.slave_id,
        "function": sensor.function,
        "address": sensor.address,
        "data_type": sensor.data_type,
    })


@login_required
def scangroup_test_run(request):
    """Bir scan grubunu anlık cihazdan oku (batch).

    POST JSON {scan_group_id}. read_raw ile ham register dizisi okunur; gruba
    bağlı her sensör batch'ten decode edilerek mühendislik değeriyle döner —
    personel hangi adresin hangi değere denk geldiğini görür. Sadece Modbus
    protokolleri (read_raw destekli).
    """
    import json
    import time

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    try:
        sg_id = int(json.loads(request.body or "{}").get("scan_group_id") or 0) or None
    except (ValueError, TypeError):
        sg_id = None
    if not sg_id:
        return JsonResponse({"ok": False, "error": "Scan grubu seçilmedi."}, status=400)

    from api.models import ScanGroup

    sg = ScanGroup.objects.select_related("connection").filter(pk=sg_id).first()
    if sg is None:
        return JsonResponse({"ok": False, "error": "Scan grubu bulunamadı."}, status=404)
    if sg.connection.protocol not in _MODBUS_PROTOCOLS:
        return JsonResponse({
            "ok": False,
            "error": "Scan grubu testi yalnızca Modbus protokollerinde geçerli "
                     f"(bağlantı protokolü: {sg.connection.protocol}).",
        }, status=400)

    from scada_io.decoders import decode_sensor_from_batch
    from scada_io.readers.factory import build_reader

    started = time.monotonic()
    try:
        reader = build_reader(sg.connection)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    regs = None
    err = ""
    try:
        if not reader.open():
            err = reader.last_error or "Bağlantı kurulamadı."
        else:
            regs, err = reader.read_raw(
                slave_id=sg.slave_id, function=sg.function,
                address=sg.start_address, count=sg.quantity,
            )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            reader.close()
        except Exception:  # noqa: BLE001
            pass

    elapsed_ms = int((time.monotonic() - started) * 1000)

    if regs is None:
        return JsonResponse({
            "ok": True, "registers": None, "error": err or "Okuma başarısız.",
            "elapsed_ms": elapsed_ms, "sensors": [],
            "start_address": sg.start_address, "quantity": sg.quantity,
        })

    # Ham register'ları adres → değer çiftleriyle döndür.
    registers = [
        {"address": sg.start_address + i, "value": int(v)} for i, v in enumerate(regs)
    ]

    # Gruba bağlı sensörleri batch'ten decode et.
    sensors_out = []
    for s in sg.sensors.select_related("parameter").order_by("address", "id"):
        row = {
            "id": s.id,
            "label": (s.parameter.display_name if s.parameter_id else None) or f"Sensör {s.id}",
            "address": s.address,
            "data_type": s.data_type,
            "value": None,
            "error": "",
        }
        try:
            row["value"] = _safe_value(decode_sensor_from_batch(regs, sg.start_address, s))
        except Exception as exc:  # noqa: BLE001 — tek sensör hatası diğerlerini engellemesin
            row["error"] = str(exc)
        sensors_out.append(row)

    return JsonResponse({
        "ok": True,
        "registers": registers,
        "error": err or "",
        "elapsed_ms": elapsed_ms,
        "sensors": sensors_out,
        "start_address": sg.start_address,
        "quantity": sg.quantity,
        "protocol": sg.connection.protocol,
    })


# ---------------------------------------------------------------------------
# Scan Grubu Sihirbazı — bağlantı kaydet/detay (step 1)
# ---------------------------------------------------------------------------

# ConnectionForm.Meta.fields ile aynı düzenlenebilir alan seti (detay döndürürken).
_CONNECTION_FIELDS = (
    "station", "name", "description", "is_enabled",
    "protocol", "transport", "host", "port",
    "serial_port", "baudrate", "parity", "stop_bits", "byte_size",
    "xonxoff", "rtscts", "dsrdtr",
    "poll_interval_sec", "save_interval_sec", "timeout_ms", "retry_count",
    "auto_reconnect", "reconnect_delay_sec",
)


@login_required
def connection_save(request):
    """Bağlantı oluştur/güncelle (sihirbaz step 1). POST JSON.

    Döner: {ok, id, name, protocol} veya {ok:false, errors:{alan:[mesaj]}}.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from dashboard.forms import ConnectionForm

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    instance = None
    if data.get("id"):
        instance = Connection.objects.filter(pk=data["id"]).first()
        if instance is None:
            return JsonResponse({"ok": False, "error": "Bağlantı bulunamadı."}, status=404)

    form = ConnectionForm(data, instance=instance)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    conn = form.save()
    log_event(
        EventType.CONFIG,
        f"Bağlantı {'güncellendi' if instance else 'oluşturuldu'} (sihirbaz): {conn}",
        severity="warning", request=request,
    )
    return JsonResponse({
        "ok": True,
        "id": conn.id,
        "name": conn.name,
        "protocol": conn.protocol,
        "station": conn.station.name if conn.station_id else None,
    })


@login_required
def connection_import(request):
    """Dışa aktarılmış bir bağlantı ağacını (JSON) hedef tesise yükler. POST multipart.

    Alanlar: `file` (yüklenen .json), `station` (hedef tesis id), opsiyonel `name`
    (yeni bağlantı adı). Döner: {ok, id, name, scan_groups, sensors} veya
    {ok:false, error}.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.connection_io import ImportError_, import_connection
    from api.models import Station

    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"ok": False, "error": "Dosya seçilmedi."}, status=400)
    if upload.size and upload.size > 10 * 1024 * 1024:
        return JsonResponse({"ok": False, "error": "Dosya çok büyük (max 10 MB)."}, status=400)

    try:
        raw = upload.read().decode("utf-8")
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON dosyası."}, status=400)

    try:
        station_id = int(request.POST.get("station") or 0) or None
    except (TypeError, ValueError):
        station_id = None
    if not station_id:
        return JsonResponse({"ok": False, "error": "Hedef tesis seçilmedi."}, status=400)
    station = Station.objects.filter(pk=station_id).first()
    if station is None:
        return JsonResponse({"ok": False, "error": "Tesis bulunamadı."}, status=404)

    name = (request.POST.get("name") or "").strip() or None

    try:
        conn = import_connection(data, station, name=name)
    except ImportError_ as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 — import UI'yı kırmasın
        return JsonResponse(
            {"ok": False, "error": f"İçe aktarım başarısız: {exc}"}, status=400
        )

    sg_count = conn.scan_groups.count()
    sensor_count = conn.sensors.count()
    log_event(
        EventType.CONFIG,
        f"Bağlantı içe aktarıldı: {conn} "
        f"({sg_count} scan grubu, {sensor_count} sensör)",
        severity="warning", request=request,
    )
    return JsonResponse({
        "ok": True,
        "id": conn.id,
        "name": conn.name,
        "station": station.name,
        "scan_groups": sg_count,
        "sensors": sensor_count,
    })


@login_required
def connection_detail(request):
    """Bir bağlantının düzenlenebilir alanları (sihirbaz step 1 prefill). GET ?connection="""
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        conn_id = int(request.GET.get("connection") or 0) or None
    except (TypeError, ValueError):
        conn_id = None
    if not conn_id:
        return JsonResponse({"ok": False, "error": "Bağlantı seçilmedi."}, status=400)

    conn = Connection.objects.filter(pk=conn_id).first()
    if conn is None:
        return JsonResponse({"ok": False, "error": "Bağlantı bulunamadı."}, status=404)

    fields = {f: getattr(conn, f if f != "station" else "station_id") for f in _CONNECTION_FIELDS}
    return JsonResponse({"ok": True, "fields": fields})


# ---------------------------------------------------------------------------
# Scan Grubu Sihirbazı — grup kaydet + gruba bağlı sensör CRUD (AJAX)
# ---------------------------------------------------------------------------

def _serialize_group_sensor(s):
    """Sihirbaz step 2 tablosu için bir sensör satırı."""
    return {
        "id": s.id,
        "parameter_id": s.parameter_id,
        "parameter": (s.parameter.display_name if s.parameter_id else None) or f"Sensör {s.id}",
        "sensor_type": s.sensor_type,
        "address": s.address,
        "data_type": s.data_type,
        "quantity": s.quantity,
        "byte_order": s.byte_order,
        "word_order": s.word_order,
        "bit_position": s.bit_position,
        "scale": s.scale,
        "offset": s.offset,
        "decimals": s.decimals,
        "digital_inverse": s.digital_inverse,
        "is_active": s.is_active,
        "is_simulated": s.is_simulated,
        "sim_min": s.sim_min,
        "sim_max": s.sim_max,
    }


@login_required
def scangroup_save(request):
    """Scan grubu oluştur/güncelle (sihirbaz step 1). POST JSON.

    Döner: {ok, id, ...grup özeti} veya {ok:false, errors:{alan:[mesaj]}}.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import ScanGroup
    from dashboard.forms import ScanGroupForm

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    instance = None
    if data.get("id"):
        instance = ScanGroup.objects.filter(pk=data["id"]).first()
        if instance is None:
            return JsonResponse({"ok": False, "error": "Scan grubu bulunamadı."}, status=404)

    form = ScanGroupForm(data, instance=instance)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    sg = form.save()
    log_event(
        EventType.CONFIG,
        f"Scan grubu {'güncellendi' if instance else 'oluşturuldu'} (sihirbaz): {sg}",
        severity="warning", request=request,
    )
    return JsonResponse({
        "ok": True,
        "id": sg.id,
        "name": sg.name,
        "connection_id": sg.connection_id,
        "protocol": sg.connection.protocol,
        "slave_id": sg.slave_id,
        "function": sg.function,
        "start_address": sg.start_address,
        "quantity": sg.quantity,
        "end_address": sg.end_address,
    })


@login_required
def scangroup_rows(request):
    """Bir bağlantının scan grupları (sihirbaz step 2 tablosu). GET ?connection="""
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        conn_id = int(request.GET.get("connection") or 0) or None
    except (TypeError, ValueError):
        conn_id = None
    if not conn_id:
        return JsonResponse({"ok": True, "groups": []})

    from django.db.models import Count

    from api.models import ScanGroup

    groups = (
        ScanGroup.objects.filter(connection_id=conn_id)
        .annotate(sensor_total=Count("sensors"))
        .order_by("slave_id", "start_address")
    )
    rows = [{
        "id": g.id, "name": g.name, "slave_id": g.slave_id, "function": g.function,
        "start_address": g.start_address, "quantity": g.quantity,
        "end_address": g.end_address, "is_active": g.is_active,
        "sensor_total": g.sensor_total,
    } for g in groups]
    return JsonResponse({"ok": True, "groups": rows})


@login_required
def scangroup_delete(request):
    """Scan grubu sil (sihirbaz step 2). POST JSON {id}.

    Bağlı sensörler silinmez; scan_group SET_NULL ile boşalır (legacy mod).
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import ScanGroup

    try:
        gid = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    sg = ScanGroup.objects.filter(pk=gid).first()
    if sg is None:
        return JsonResponse({"ok": False, "error": "Scan grubu bulunamadı."}, status=404)
    label = str(sg)
    sg.delete()
    log_event(
        EventType.CONFIG, f"Scan grubu silindi (sihirbaz): {label}",
        severity="warning", request=request,
    )
    return JsonResponse({"ok": True})


@login_required
def group_sensor_list(request):
    """Bir scan grubunun sensörleri (sihirbaz step 2 tablosu). GET ?scan_group="""
    denied = _require_operator(request)
    if denied:
        return denied

    try:
        sg_id = int(request.GET.get("scan_group") or 0) or None
    except (TypeError, ValueError):
        sg_id = None
    if not sg_id:
        return JsonResponse({"ok": True, "sensors": []})

    sensors = (
        Sensor.objects.filter(scan_group_id=sg_id)
        .select_related("parameter")
        .order_by("address", "id")
    )
    return JsonResponse({"ok": True, "sensors": [_serialize_group_sensor(s) for s in sensors]})


@login_required
def group_sensor_save(request):
    """Gruba bağlı sensör oluştur/güncelle (sihirbaz step 2). POST JSON.

    scan_group payload'dan zorlanır; connection/slave/function gruptan miras
    alınır (model). address range model validasyonuyla doğrulanır.
    """
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from api.models import ScanGroup
    from dashboard.forms import GroupSensorForm

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    sg = ScanGroup.objects.filter(pk=data.get("scan_group")).first()
    if sg is None:
        return JsonResponse({"ok": False, "error": "Önce scan grubunu kaydedin."}, status=400)

    instance = None
    if data.get("id"):
        instance = Sensor.objects.filter(pk=data["id"]).first()
        if instance is None:
            return JsonResponse({"ok": False, "error": "Sensör bulunamadı."}, status=404)

    form = GroupSensorForm(data, instance=instance)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    sensor = form.save()
    log_event(
        EventType.CONFIG,
        f"Sensör {'güncellendi' if instance else 'oluşturuldu'} (sihirbaz): {sensor} (grup={sg})",
        severity="warning", request=request,
    )
    return JsonResponse({"ok": True, "sensor": _serialize_group_sensor(sensor)})


@login_required
def group_sensor_delete(request):
    """Gruba bağlı sensörü sil (sihirbaz step 2). POST JSON {id}."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    try:
        sid = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    sensor = Sensor.objects.filter(pk=sid).first()
    if sensor is None:
        return JsonResponse({"ok": False, "error": "Sensör bulunamadı."}, status=404)
    label = str(sensor)
    sensor.delete()
    log_event(
        EventType.CONFIG, f"Sensör silindi (sihirbaz): {label}",
        severity="warning", request=request,
    )
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------- #
# İlk kurulum sihirbazı (rol=1)
# --------------------------------------------------------------------------- #

@login_required
def setup_save(request):
    """Kurulum sihirbazı kaydı (rol=1). POST JSON.

    Beklenen gövde::

        {
          "station": {"name", "station_type", "address", "company"},
          "cabinet": {"device_id", "code", "name", "data_period",
                      "auth_username", "auth_secret"}   # opsiyonel
        }

    Varsayılan tesisi (id=1 / ilk aktif) günceller; tesis yoksa oluşturur.
    Tesis tipi SAIS (sürekli atıksu izleme / emisyon ölçüm) ise `cabinet`
    bilgisiyle tek bir `SaisCabinet` oluşturur/günceller. Son olarak
    `SetupState`'i tamamlandı olarak damgalar.

    Döner: {ok, station_id, requires_cabinet} veya {ok:false, errors:{...}}.
    """
    import json

    from django.db import transaction

    from api.models import SetupState, Station, StationType
    from dashboard.views import SAIS_CABINET_STATION_TYPES, default_station_id

    denied = _require_admin(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    st = data.get("station") or {}
    errors: dict[str, str] = {}

    name = (st.get("name") or "").strip()
    if not name:
        errors["name"] = "Tesis adı zorunludur."

    type_code = (st.get("station_type") or "").strip()
    station_type = StationType.objects.filter(code=type_code).first()
    if station_type is None:
        errors["station_type"] = "Geçerli bir tesis tipi seçin."

    requires_cabinet = type_code in SAIS_CABINET_STATION_TYPES

    cab = data.get("cabinet") or {}
    if requires_cabinet:
        for field, label in (
            ("device_id", "Bakanlık SIM ID"),
            ("code", "Tesis kodu"),
            ("auth_username", "Bakanlık kullanıcı adı"),
            ("auth_secret", "Bakanlık şifresi"),
        ):
            if not (cab.get(field) or "").strip():
                errors[f"cabinet_{field}"] = f"{label} zorunludur."

    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    try:
        data_period = int(cab.get("data_period") or 1)
    except (TypeError, ValueError):
        data_period = 1
    data_period = max(1, min(data_period, 1440))

    with transaction.atomic():
        sid = default_station_id()
        station = Station.objects.filter(pk=sid).first() if sid else None
        if station is None:
            station = Station(id=1)
        station.name = name
        station.station_type = station_type
        station.address = (st.get("address") or "").strip()
        station.company = (st.get("company") or "").strip()
        station.active = True
        if station.user_id is None:
            station.user = request.user
        station.save()

        if requires_cabinet:
            from sais_domain.models import SaisCabinet
            cabinet = (
                SaisCabinet.objects.filter(station=station).order_by("created_at").first()
                or SaisCabinet(station=station)
            )
            cabinet.station = station
            cabinet.device_id = (cab.get("device_id") or "").strip()
            cabinet.code = (cab.get("code") or "").strip()
            cabinet.name = (cab.get("name") or "").strip() or f"{name} Kabini"
            cabinet.data_period = data_period
            cabinet.auth_username = (cab.get("auth_username") or "").strip()
            cabinet.auth_secret = (cab.get("auth_secret") or "").strip()
            if cabinet.user_id is None:
                cabinet.user = request.user
            cabinet.save()

        state = SetupState.load()
        was_completed = state.completed
        state.completed = True
        state.completed_at = timezone.now()
        state.completed_by = request.user
        state.save()

    log_event(
        EventType.CONFIG,
        f"Kurulum sihirbazı {'güncellendi' if was_completed else 'tamamlandı'}: "
        f"tesis={station.name} (tip={type_code}), kabin={'evet' if requires_cabinet else 'hayır'}",
        severity="warning", request=request,
    )
    return JsonResponse({
        "ok": True,
        "station_id": station.id,
        "requires_cabinet": requires_cabinet,
    })


# --------------------------------------------------------------------------- #
# Mimik Tasarım Editörü (MimicScreen) — CRUD AJAX endpoint'leri
# --------------------------------------------------------------------------- #

@login_required
def mimic_screen_list(request):
    """Kayıtlı mimik tasarımlarının listesi (galeri + editör 'Aç' diyaloğu)."""
    denied = _require_admin(request)
    if denied:
        return denied
    from dashboard.models import MimicScreen

    items = [{
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "width": m.width,
        "height": m.height,
        "is_template": m.is_template,
        "thumbnail": m.thumbnail,
        "updated_at": timezone.localtime(m.updated_at).strftime("%d.%m.%Y %H:%M"),
        "created_by": (m.created_by.get_username() if m.created_by else None),
    } for m in MimicScreen.objects.select_related("created_by").order_by("-updated_at")]
    return JsonResponse({"ok": True, "results": items})


@login_required
def mimic_screen_get(request):
    """Tek mimik tasarımının tam tanımı (tuval verisi dahil)."""
    denied = _require_admin(request)
    if denied:
        return denied
    from dashboard.models import MimicScreen

    m = MimicScreen.objects.filter(pk=request.GET.get("id")).first()
    if m is None:
        return JsonResponse({"ok": False, "error": "Tasarım bulunamadı."}, status=404)
    return JsonResponse({"ok": True, "screen": {
        "id": m.id, "name": m.name, "description": m.description,
        "width": m.width, "height": m.height, "background": m.background,
        "is_template": m.is_template, "data": m.data,
    }})


@login_required
def mimic_screen_save(request):
    """Mimik tasarımı oluştur/güncelle (Fabric.js canvas JSON + thumbnail)."""
    import json

    denied = _require_admin(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from dashboard.models import MimicScreen

    try:
        payload = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Ekran adı zorunlu."}, status=400)

    data = payload.get("data")
    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Geçersiz tuval verisi."}, status=400)

    m_id = payload.get("id")
    if m_id:
        m = MimicScreen.objects.filter(pk=m_id).first()
        if m is None:
            return JsonResponse({"ok": False, "error": "Tasarım bulunamadı."}, status=404)
    else:
        m = MimicScreen(created_by=request.user)

    m.name = name
    m.description = (payload.get("description") or "").strip()
    m.data = data
    if payload.get("thumbnail"):
        m.thumbnail = payload["thumbnail"]
    try:
        m.width = int(payload.get("width") or m.width or 1280)
        m.height = int(payload.get("height") or m.height or 720)
    except (TypeError, ValueError):
        pass
    if payload.get("background"):
        m.background = str(payload["background"])[:32]
    m.save()
    return JsonResponse({"ok": True, "id": m.pk})


@login_required
def mimic_screen_delete(request):
    """Mimik tasarımı sil — yerleşik şablonlar silinemez."""
    import json

    denied = _require_admin(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Desteklenmeyen method."}, status=405)

    from dashboard.models import MimicScreen

    try:
        m_id = json.loads(request.body or "{}").get("id")
    except (ValueError, TypeError):
        m_id = None

    m = MimicScreen.objects.filter(pk=m_id).first()
    if m is None:
        return JsonResponse({"ok": False, "error": "Tasarım bulunamadı."}, status=404)
    if m.is_template:
        return JsonResponse({"ok": False, "error": "Yerleşik şablon silinemez."}, status=400)
    m.delete()
    return JsonResponse({"ok": True})


@login_required
def mimic_menu(request):
    """Header 'Mimik' menüsü — tüm rollerin görüntüleyebileceği hafif liste.

    Yalnız ``id`` + ``name`` + ``is_template`` döner (thumbnail/CRUD yok); her
    satır görüntüleyiciyi (``mimic_viewer``) yeni sekmede açar. Galeri + CRUD
    endpoint'lerinden (``mimic_screen_*``, admin-gate'li) ayrıdır; SCADA/HMI
    mimikleri her dashboard kullanıcısı izleyebilsin diye salt ``login_required``.
    """
    from dashboard.models import MimicScreen

    items = [{
        "id": m.id,
        "name": m.name,
        "is_template": m.is_template,
    } for m in MimicScreen.objects.order_by("is_template", "name")]
    return JsonResponse({"ok": True, "results": items})


@login_required
def mimic_tags(request):
    """Mimik bağlama için gerçek SCADA etiketleri + canlı değerleri.

    Her aktif sensörün otomatik `tag`'i, okunabilir etiketi, birimi, tipi ve
    `SensorLatest` anlık değeri döner. Editör tag dropdown'unu ve görüntüleyici
    canlı modunu besler. `values` haritası tag→değer (canlı poll için).

    Salt-okuma sensör değerleri (home snapshot gibi) → tüm rollere açık
    (`login_required`): viewer her rol tarafından izlenebildiğinden canlı veri
    de admin-dışı kullanıcılarda çalışmalı.
    """
    sensors = (
        Sensor.objects.filter(is_active=True)
        .exclude(tag="")
        .select_related("parameter", "connection__station", "latest")
        .order_by("connection__station__name", "display_order", "id")
    )
    items, values = [], {}
    for s in sensors:
        latest = getattr(s, "latest", None)
        val = latest.value if (latest and latest.value is not None) else 0
        param = s.parameter
        label = (param.display_name if param else None) or s.tag
        station = s.connection.station.name if (s.connection_id and s.connection.station_id) else ""
        items.append({
            "tag": s.tag,
            "label": label,
            "station": station,
            "unit": (param.unit_txt or param.unit or "") if param else "",
            "type": s.sensor_type,
            "digital": s.sensor_type in (2, 3),
            "value": round(float(val), 3) if val is not None else 0,
            # Mimik görüntüleyici tıklama menüsü için: rapor navigasyonu +
            # kontrol (yalnız dijital çıkış type=3 yazılabilir).
            "sensor_id": s.id,
            "station_id": s.connection.station_id if s.connection_id else None,
            "parameter_id": s.parameter_id,
            "is_output": s.sensor_type == 3,
        })
        values[s.tag] = round(float(val), 3) if val is not None else 0
    return JsonResponse({"ok": True, "results": items, "values": values})


def mimic_control(request):
    """Mimik görüntüleyici tıklama menüsü → "Kontrol" aksiyonu.

    Bir mimik objesine bağlı etiketin (``tag``) sensörüne komut yazar.
    Güvenlik sınırı: yalnız **dijital çıkış** (``sensor_type=3``) yazılabilir.

    POST JSON: ``{"tag": str, "action": "start"|"stop"|"set", "value"?: number}``
      - ``start``/``stop`` → aktif/pasif (1/0).
      - ``set`` → verilen sayısal değeri yazar (çıkış sensörü).

    Yanıt: ``{"ok", "message", "command_id"?, "duplicate"?}``. Operatör/admin
    (rol 1/2) gerekir; komut `Command` tablosuna yazılır → Celery yürütür
    (`digital_output_command` ile aynı akış).
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

    tag = (data.get("tag") or "").strip()
    action = data.get("action")
    if not tag:
        return JsonResponse({"ok": False, "error": "Etiket (tag) gerekli."}, status=400)
    if action not in ("start", "stop", "set"):
        return JsonResponse({"ok": False, "error": "Geçersiz action."}, status=400)

    sensor = (
        Sensor.objects.select_related("connection__station")
        .filter(is_active=True, sensor_type=3)
        .exclude(tag="")
        .filter(tag=tag)
        .first()
    )
    if sensor is None:
        return JsonResponse(
            {"ok": False, "error": "Bu etiket kontrol edilemez (dijital çıkış değil)."},
            status=404,
        )

    if action == "set":
        try:
            logical = 1 if float(data.get("value")) != 0 else 0
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Geçersiz değer."}, status=400)
    else:
        logical = 1 if action == "start" else 0

    # digital_inverse ise fiziksel coil tersine yazılır (okuma da terslediği için).
    coil_value = (0 if logical else 1) if sensor.digital_inverse else logical

    try:
        from datetime import timedelta as _timedelta

        from api.models import Command, RequestType

        bucket = int(timezone.now().timestamp() // 3)
        idem = f"mimic_control:{sensor.id}:{logical}:{bucket}"
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

    if created:
        from api.events import EventType, log_event

        lbl = {"start": "Aktif (AÇ)", "stop": "Pasif (KAPAT)", "set": f"Değer={logical}"}[action]
        station = getattr(sensor.connection, "station", None)
        log_event(
            EventType.COMMAND,
            f"Mimik kontrol: {sensor.name or ('sensör#' + str(sensor.pk))} → {lbl}",
            severity="warning", request=request, station=station,
        )

    return JsonResponse({
        "ok": True,
        "message": None if created else "Aynı komut zaten kuyrukta.",
        "command_id": cmd.pk,
        "duplicate": not created,
    })


# --------------------------------------------------------------------------- #
# SIM Ayarları: SAIS kabin CRUD + Bakanlık istasyon sorgu + şifre değiştir
# --------------------------------------------------------------------------- #
# Tüm uçlar rol 1/2 (OperatorRequiredMixin sayfasıyla aynı kapı). Bakanlık
# servisleri (GetStationInformation / ChangePassword) `SaisSimClient` üzerinden
# çağrılır; her çağrı ApiLog(direction='out') olarak otomatik kaydedilir.


def _cabinet_dict(cab):
    """Bir SaisCabinet'i JSON-safe sözlüğe çevirir. Şifre maskelenir —
    yalnız tanımlı olup olmadığı (`has_secret`) döner, ham değer asla."""
    return {
        "id": cab.pk,
        "station_id": cab.station_id,
        "station_name": cab.station.name if cab.station_id else "",
        "device_id": cab.device_id,
        "code": cab.code,
        "name": cab.name,
        "data_period": cab.data_period,
        "auth_username": cab.auth_username,
        "has_secret": bool(cab.auth_secret),
        "created_at": cab.created_at.isoformat() if cab.created_at else None,
    }


@login_required
def sim_cabinet_save(request):
    """SAIS kabin oluştur/güncelle. POST JSON:
    {id?, station_id, device_id, code, name, data_period, auth_username, auth_secret?}

    Düzenlemede `auth_secret` boş bırakılırsa mevcut şifre korunur (maskeli
    alandan boş gelmesi şifreyi silmesin)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from api.models import Station
    from sais_domain.models import SaisCabinet

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    station = Station.objects.filter(pk=data.get("station_id")).first()
    if station is None:
        return JsonResponse({"ok": False, "error": "Tesis seçilmedi veya bulunamadı."}, status=400)

    device_id = (data.get("device_id") or "").strip()
    name = (data.get("name") or "").strip()
    auth_username = (data.get("auth_username") or "").strip()
    if not device_id:
        return JsonResponse({"ok": False, "error": "Bakanlık SIM ID zorunlu."}, status=400)
    if not name:
        return JsonResponse({"ok": False, "error": "Kabin adı zorunlu."}, status=400)
    if not auth_username:
        return JsonResponse({"ok": False, "error": "Bakanlık kullanıcı adı zorunlu."}, status=400)

    try:
        data_period = int(data.get("data_period") or 1)
    except (TypeError, ValueError):
        data_period = 1

    cab_id = data.get("id")
    if cab_id:
        cab = SaisCabinet.objects.filter(pk=cab_id).first()
        if cab is None:
            return JsonResponse({"ok": False, "error": "Kabin bulunamadı."}, status=404)
    else:
        # cabinet.user, post_save sinyaliyle otomatik Bakanlık kullanıcısına
        # bağlanır (oluşturan kişi değil) — burada set etme.
        cab = SaisCabinet()

    cab.station = station
    cab.device_id = device_id[:100]
    cab.code = (data.get("code") or "").strip()[:50]
    cab.name = name[:200]
    cab.data_period = data_period
    cab.auth_username = auth_username[:50]

    secret = data.get("auth_secret")
    if secret:  # boş/None → mevcut korunur (yeni kayıtta da boş kalabilir)
        cab.auth_secret = str(secret)[:255]
    elif not cab_id:
        cab.auth_secret = ""

    cab.save()
    return JsonResponse({"ok": True, "cabinet": _cabinet_dict(cab)})


@login_required
def sim_cabinet_delete(request):
    """SAIS kabin sil. POST JSON {id}."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.models import SaisCabinet

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    cab = SaisCabinet.objects.filter(pk=data.get("id")).first()
    if cab is None:
        return JsonResponse({"ok": False, "error": "Kabin bulunamadı."}, status=404)
    cab.delete()
    return JsonResponse({"ok": True})


def _sim_client_for(cabinet_id):
    """(cabinet, client) üretir veya (None, error_message) döner."""
    from sais_domain.clients.sim import SaisSimClient
    from sais_domain.models import SaisCabinet

    cab = SaisCabinet.objects.select_related("station").filter(pk=cabinet_id).first()
    if cab is None:
        return None, None, "Kabin bulunamadı."
    if not cab.auth_username or not cab.auth_secret:
        return cab, None, "Bu kabinde Bakanlık kullanıcı adı/şifresi tanımlı değil."
    return cab, SaisSimClient(cab), None


@login_required
def sim_station_query(request):
    """Bakanlık `GetStationInformation` canlı sorgusu. POST JSON {cabinet_id}."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.clients.exceptions import SaisAuthError, SaisResponseError

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    cab, client, err = _sim_client_for(data.get("cabinet_id"))
    if err:
        status = 404 if cab is None else 400
        return JsonResponse({"ok": False, "error": err}, status=status)

    try:
        objects = client.get_station_information(triggered_by=request.user)
    except SaisAuthError as exc:
        return JsonResponse({"ok": False, "error": f"Bakanlık girişi başarısız: {exc}"}, status=502)
    except SaisResponseError as exc:
        return JsonResponse({
            "ok": False,
            "error": f"Bakanlık yanıt hatası: {exc}",
            "detail": (exc.response_text or "")[:2000],
            "status_code": exc.status_code,
        }, status=502)
    except Exception as exc:  # noqa: BLE001 — ağ/timeout vb.
        return JsonResponse({"ok": False, "error": f"Sorgu başarısız: {exc}"}, status=502)
    finally:
        client.close()

    return JsonResponse({"ok": True, "device_id": cab.device_id, "objects": objects})


@login_required
def sim_send_host_changed(request):
    """Bakanlık `SendHostChanged` — istasyon host + kabin kullanıcı/şifre günceller;
    başarılıysa yerel `SaisCabinet.auth_username`/`auth_secret`'i senkronlar.

    POST JSON {cabinet_id, connection_user, connection_password, domain_address, port}.
    Bakanlık'ın döndürdüğü ham yanıt (result/message + hata gövdesi) debug için
    response'a eklenir."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.clients.exceptions import SaisAuthError, SaisResponseError

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    conn_user = (data.get("connection_user") or "").strip()
    conn_pass = (data.get("connection_password") or "").strip()
    domain_address = (data.get("domain_address") or "").strip()
    port = (str(data.get("port") or "")).strip() or "443"
    if not conn_user:
        return JsonResponse({"ok": False, "error": "Kullanıcı adı zorunlu."}, status=400)
    if not conn_pass:
        return JsonResponse({"ok": False, "error": "Şifre zorunlu."}, status=400)
    if not domain_address:
        return JsonResponse({"ok": False, "error": "Host/IP adresi zorunlu."}, status=400)

    cab, client, err = _sim_client_for(data.get("cabinet_id"))
    if err:
        status = 404 if cab is None else 400
        return JsonResponse({"ok": False, "error": err}, status=status)

    try:
        envelope = client.send_host_changed(
            connection_user=conn_user,
            connection_password=conn_pass,
            domain_address=domain_address,
            port=port,
            triggered_by=request.user,
        )
    except SaisAuthError as exc:
        return JsonResponse({"ok": False, "error": f"Bakanlık girişi başarısız: {exc}"}, status=502)
    except SaisResponseError as exc:
        return JsonResponse({
            "ok": False,
            "error": f"Bakanlık yanıt hatası: {exc}",
            "detail": (exc.response_text or "")[:2000],
            "status_code": exc.status_code,
        }, status=502)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": f"İşlem başarısız: {exc}"}, status=502)
    finally:
        client.close()

    result = bool(envelope.get("result")) if isinstance(envelope, dict) else False
    message = (envelope.get("message") if isinstance(envelope, dict) else None) or ""
    if not result:
        return JsonResponse({
            "ok": False,
            "error": message or "Bakanlık güncellemeyi reddetti.",
            "detail": json.dumps(envelope, ensure_ascii=False)[:2000] if envelope is not None else "",
        }, status=400)

    # Bakanlık kabul etti → yerel erişim bilgilerini senkronla. (Cache'teki ticket
    # bayatlayabilir; SaisSimClient sonraki çağrıda 401 alıp yeniden login eder.)
    cab.auth_username = conn_user[:50]
    cab.auth_secret = conn_pass[:255]
    cab.save(update_fields=["auth_username", "auth_secret"])
    return JsonResponse({
        "ok": True,
        "message": message or "Bağlantı bilgileri güncellendi.",
        "response": envelope,
    })


# --------------------------------------------------------------------------- #
# SIM Ayarları: Bakanlık sorgu servisleri konsolu (read-only canlı sorgular)
# --------------------------------------------------------------------------- #
# "Bakanlık Servisleri" sayfası bu katalogtaki sorgu uçlarını tek tek (veya
# topluca) çağırır; her çağrı `SaisSimClient` üzerinden ApiLog(direction='out')
# olarak otomatik loglanır. Yalnız okuma/sorgu uçları — veri/numune gönderimi
# (SendData / SampleRequest*) bilinçli olarak DIŞARIDA tutuldu (yanlışlıkla
# Bakanlık'a kayıt düşmesin diye).
#
# Her giriş:
#   key      — frontend ile eşleşen kısa anahtar
#   label    — kullanıcıya görünen Türkçe ad
#   endpoint — Bakanlık servis adı (request URL'i + rozet için)
#   method   — SaisSimClient metot adı
#   params   — kullanıcının doldurması gereken parametreler (period/date)
#   group    — sol komut rayında gruplama
SIM_SERVICE_CATALOG = [
    {"key": "server_datetime", "label": "Sunucu Saati",
     "endpoint": "GetServerDateTime", "method": "get_server_datetime",
     "params": [], "group": "Zaman / İstasyon", "icon": "ki-time"},
    {"key": "station_information", "label": "İstasyon Bilgileri",
     "endpoint": "GetStationInformation", "method": "get_station_information",
     "params": [], "group": "Zaman / İstasyon", "icon": "ki-cloud"},
    {"key": "channel_information", "label": "Kanal Bilgileri",
     "endpoint": "GetChannelInformationByStationId", "method": "get_channel_information",
     "params": [], "group": "Zaman / İstasyon", "icon": "ki-abstract-26"},
    {"key": "parameters", "label": "Parametre Bilgileri",
     "endpoint": "GetParameters", "method": "get_parameters",
     "params": [], "group": "Genel Bilgiler", "icon": "ki-data"},
    {"key": "units", "label": "Birim Bilgileri",
     "endpoint": "GetUnits", "method": "get_units",
     "params": [], "group": "Genel Bilgiler", "icon": "ki-ruler"},
    {"key": "data_status", "label": "Veri Durum Kodları",
     "endpoint": "GetDataStatusDescription", "method": "get_data_status_descriptions",
     "params": [], "group": "Genel Bilgiler", "icon": "ki-shield-search"},
    {"key": "diagnostic_types", "label": "Diagnostik Tipleri",
     "endpoint": "GetDiagnosticTypes", "method": "get_diagnostic_types",
     "params": [], "group": "Genel Bilgiler", "icon": "ki-pulse"},
    {"key": "last_data", "label": "Son Gönderilen Veri",
     "endpoint": "GetLastData", "method": "get_last_data",
     "params": ["period"], "group": "Veri Sorguları", "icon": "ki-some-files"},
    {"key": "missing_dates", "label": "Eksik Veri Tarihleri",
     "endpoint": "GetMissingDates", "method": "get_missing_dates",
     "params": [], "group": "Veri Sorguları", "icon": "ki-calendar-remove"},
    {"key": "data_between", "label": "İki Tarih Arası Veri",
     "endpoint": "GetDataByBetweenTwoDate", "method": "get_data_between",
     "params": ["period", "start_date", "end_date"], "group": "Veri Sorguları",
     "icon": "ki-calendar-search"},
    {"key": "calibration", "label": "Kalibrasyon Kayıtları",
     "endpoint": "GetCalibration", "method": "get_calibration",
     "params": ["start_date", "end_date"], "group": "Veri Sorguları",
     "icon": "ki-test-tubes"},
]

_SIM_SERVICE_BY_KEY = {s["key"]: s for s in SIM_SERVICE_CATALOG}


def _sim_service_request_url(client, spec, kwargs):
    """Çağrının temsilî request URL'ini üretir (sayfada gösterim için).

    Gerçek istek POST + query-string ile gider; burada okunabilir bir özet
    üretilir (örn. ``.../SAIS/GetLastData?stationId=...&period=1``)."""
    from urllib.parse import urlencode

    base = f"{client.base_url}/SAIS/{spec['endpoint']}"
    query = {}
    # stationId hemen her parametreli uçta var; period/date varsa ekle.
    if spec["endpoint"] not in ("GetServerDateTime", "GetParameters",
                                "GetUnits", "GetDataStatusDescription",
                                "GetDiagnosticTypes"):
        query["stationId"] = client.cabinet.device_id
    if "period" in kwargs:
        query["period"] = kwargs["period"]
    if "start_date" in kwargs:
        query["startDate"] = kwargs["start_date"]
    if "end_date" in kwargs:
        query["endDate"] = kwargs["end_date"]
    return f"{base}?{urlencode(query)}" if query else base


@login_required
def sim_service_call(request):
    """Bakanlık sorgu servisi canlı çağrısı. POST JSON:
    {cabinet_id, service, period?, start_date?, end_date?}.

    `service` SIM_SERVICE_CATALOG anahtarı olmalı. Yanıt: çağrının `objects`'i +
    temsilî request URL + endpoint adı. Hata durumunda Bakanlık'ın ham yanıtı
    (`detail`) ve HTTP kodu döner (sim_station_query ile aynı sözleşme)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.clients.exceptions import SaisAuthError, SaisResponseError

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    spec = _SIM_SERVICE_BY_KEY.get((data.get("service") or "").strip())
    if spec is None:
        return JsonResponse({"ok": False, "error": "Bilinmeyen servis."}, status=400)

    # Parametreleri topla + doğrula.
    kwargs = {}
    if "period" in spec["params"]:
        try:
            kwargs["period"] = int(data.get("period") or 1)
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "Geçersiz periyot."}, status=400)
    for date_key in ("start_date", "end_date"):
        if date_key in spec["params"]:
            val = (data.get(date_key) or "").strip()
            if not val:
                return JsonResponse(
                    {"ok": False, "error": "Başlangıç ve bitiş tarihi zorunlu."},
                    status=400,
                )
            kwargs[date_key] = val

    cab, client, err = _sim_client_for(data.get("cabinet_id"))
    if err:
        status = 404 if cab is None else 400
        return JsonResponse({"ok": False, "error": err}, status=status)

    request_url = _sim_service_request_url(client, spec, kwargs)
    try:
        method = getattr(client, spec["method"])
        objects = method(triggered_by=request.user, **kwargs)
    except SaisAuthError as exc:
        return JsonResponse({
            "ok": False, "service": spec["key"], "endpoint": spec["endpoint"],
            "request_url": request_url,
            "error": f"Bakanlık girişi başarısız: {exc}",
        }, status=502)
    except SaisResponseError as exc:
        return JsonResponse({
            "ok": False, "service": spec["key"], "endpoint": spec["endpoint"],
            "request_url": request_url,
            "error": f"Bakanlık yanıt hatası: {exc}",
            "detail": (exc.response_text or "")[:4000],
            "status_code": exc.status_code,
        }, status=502)
    except Exception as exc:  # noqa: BLE001 — ağ/timeout vb.
        return JsonResponse({
            "ok": False, "service": spec["key"], "endpoint": spec["endpoint"],
            "request_url": request_url,
            "error": f"Sorgu başarısız: {exc}",
        }, status=502)
    finally:
        client.close()

    return JsonResponse({
        "ok": True,
        "service": spec["key"],
        "label": spec["label"],
        "endpoint": spec["endpoint"],
        "request_url": request_url,
        "device_id": cab.device_id,
        "objects": objects,
    })


# --------------------------------------------------------------------------- #
# SIM Ayarları: Dinamik Veri Raporu (GetDataByBetweenTwoDate — gün gün)
# --------------------------------------------------------------------------- #
# Bakanlık'ın iki tarih arası veri servisini gün-bazlı çağırır: kullanıcı bir
# gün seçer (varsayılan bugün), o güne ait dakikalık veriler + status'lar tablo
# olarak gelir. Sayfa SİM'e SÜREKLİ sorgu atmaz — yalnız kullanıcı gün değiştirince
# (veya Getir'e basınca) tek bir GetDataByBetweenTwoDate isteği gider; frontend
# çekilen günleri cache'ler.

# Status kataloğu + parametre meta + doğrulanmış-veri sayım mantığı tek kaynak:
# ``sais_domain.sim_report`` (job ile ortak; dashboard'a bağımlı değil).
from sais_domain.sim_report import (  # noqa: E402
    SIM_DATA_STATUS_CODES,
    SIM_PARAM_META,
    detect_params as _detect_sim_params,
    slim_row as _slim_sim_row,
)


@login_required
def sim_data_report(request):
    """Dinamik Veri Raporu — tek gün için Bakanlık `GetDataByBetweenTwoDate`.

    POST JSON {cabinet_id, date (YYYY-MM-DD), period?}. O günün 00:00:00–23:59:59
    aralığını sorgular; dakikalık satırlar + tespit edilen parametre listesini
    döndürür. SİM'e yalnız bu çağrı gider (polling yok)."""
    import json
    from datetime import datetime
    from urllib.parse import urlencode

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.clients.exceptions import SaisAuthError, SaisResponseError

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    date_str = (data.get("date") or "").strip()
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz tarih (YYYY-MM-DD)."}, status=400)
    try:
        period = int(data.get("period") or 1)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz periyot."}, status=400)

    start = f"{date_str} 00:00:00"
    end = f"{date_str} 23:59:59"

    cab, client, err = _sim_client_for(data.get("cabinet_id"))
    if err:
        status = 404 if cab is None else 400
        return JsonResponse({"ok": False, "error": err}, status=status)

    request_url = (
        f"{client.base_url}/SAIS/GetDataByBetweenTwoDate?"
        + urlencode({
            "stationId": cab.device_id, "period": period,
            "startDate": start, "endDate": end,
        })
    )

    try:
        objects = client.get_data_between(
            period=period, start_date=start, end_date=end,
            triggered_by=request.user,
        )
    except SaisAuthError as exc:
        return JsonResponse({
            "ok": False, "request_url": request_url,
            "error": f"Bakanlık girişi başarısız: {exc}",
        }, status=502)
    except SaisResponseError as exc:
        return JsonResponse({
            "ok": False, "request_url": request_url,
            "error": f"Bakanlık yanıt hatası: {exc}",
            "detail": (exc.response_text or "")[:4000],
            "status_code": exc.status_code,
        }, status=502)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({
            "ok": False, "request_url": request_url,
            "error": f"Sorgu başarısız: {exc}",
        }, status=502)
    finally:
        client.close()

    rows = objects if isinstance(objects, list) else []
    param_keys = _detect_sim_params(rows)
    parameters = []
    for key in param_keys:
        label, unit = SIM_PARAM_META.get(key, (key, ""))
        parameters.append({"key": key, "label": label, "unit": unit})

    return JsonResponse({
        "ok": True,
        "date": date_str,
        "period": period,
        "count": len(rows),
        "parameters": parameters,
        "rows": [_slim_sim_row(r) for r in rows],
        "request_url": request_url,
    })


@login_required
def sim_valid_ratio(request):
    """Aylık geçerli veri oranı — **DB'den** (canlı Bakanlık sorgusu YOK).

    POST JSON {cabinet_id, month (YYYY-MM)}. Günde 1 kez çalışan job
    (``sais_domain.tasks.compute_sim_valid_stats``) ayın günlerini gün gün çekip
    ``SimValidDay``'e damgalar; bu uç o satırların toplamından geçerli veri
    yüzdesini hesaplar. Geçerlilik yalnız doğrulanmış (``_N``) status'a dayanır.

    Hiç hesaplanmamışsa ``computed=False`` döner (kullanıcı "yeniden hesapla" ile
    job'u tetikleyebilir)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from datetime import date as _date

    from sais_domain.models import SaisCabinet, SimValidDay

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    month_str = (data.get("month") or "").strip()
    try:
        year, mon = (int(x) for x in month_str.split("-"))
        first = _date(year, mon, 1)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz ay (YYYY-MM)."}, status=400)

    cab = SaisCabinet.objects.filter(pk=data.get("cabinet_id")).first()
    if cab is None:
        return JsonResponse({"ok": False, "error": "Kabin bulunamadı."}, status=404)

    from calendar import monthrange
    last = _date(year, mon, monthrange(year, mon)[1])
    days = list(SimValidDay.objects.filter(cabinet=cab, day__gte=first, day__lte=last))

    if not days:
        return JsonResponse({
            "ok": True, "month": month_str, "computed": False,
            "valid_pct": None, "received": 0, "expected": 0,
            "days_covered": 0, "per_param": {}, "updated_at": None,
        })

    total_valid = sum(d.valid_cells() for d in days)
    total_cells = sum(d.total_cells() for d in days)
    total_expected = sum(d.expected for d in days)
    total_received = sum(d.received for d in days)
    valid_pct = round(min(100.0, total_valid / total_cells * 100), 1) if total_cells else 0.0

    # Parametre bazlı oran (toplam geçerli / toplam beklenen dakika).
    per_param_valid = {}
    for d in days:
        for p, c in (d.param_valid or {}).items():
            per_param_valid[p] = per_param_valid.get(p, 0) + c
    per_param = {
        p: round(min(100.0, v / total_expected * 100), 1) if total_expected else 0.0
        for p, v in per_param_valid.items()
    }
    last_updated = max(d.updated_at for d in days)

    return JsonResponse({
        "ok": True,
        "month": month_str,
        "computed": True,
        "valid_pct": valid_pct,
        "received": total_received,
        "expected": total_expected,
        "days_covered": len(days),
        "per_param": per_param,
        "updated_at": timezone.localtime(last_updated).strftime("%d.%m.%Y %H:%M"),
    })


@login_required
def sim_valid_recompute(request):
    """Aylık geçerli veri istatistiğini **yeniden hesaplat** (job'u tetikle).

    POST JSON {cabinet_id, month (YYYY-MM)}. Bakanlık sorgularını (gün gün) Celery
    worker'da yapan ``compute_sim_valid_stats`` task'ını enqueue eder; HTTP isteği
    beklemez. Frontend sonra ``sim_valid_ratio``'yu (DB) periyodik okuyup günceller
    — yani tarayıcı SİM'e gitmez, yalnız worker gider."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.models import SaisCabinet
    from sais_domain.tasks import compute_sim_valid_stats

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    month_str = (data.get("month") or "").strip() or None
    cab = SaisCabinet.objects.filter(pk=data.get("cabinet_id")).first()
    if cab is None:
        return JsonResponse({"ok": False, "error": "Kabin bulunamadı."}, status=404)

    try:
        compute_sim_valid_stats.delay(cabinet_id=cab.id, month=month_str)
        queued = True
    except Exception:  # noqa: BLE001 — broker yoksa (dev) senkron çalıştır
        compute_sim_valid_stats(cabinet_id=cab.id, month=month_str)
        queued = False

    return JsonResponse({"ok": True, "queued": queued})




# --------------------------------------------------------------------------- #
# Sistem Kontrol → Sistem Alarmları (toggle + ayar + canlı durum)
# --------------------------------------------------------------------------- #
def _alarm_settings_dict(s):
    """SystemAlarmSettings → JSON-safe sözlük."""
    return {
        "data_error_enabled": s.data_error_enabled,
        "data_error_codes": list(s.data_error_codes or []),
        "data_error_persist_minutes": s.data_error_persist_minutes,
        "data_error_cooldown_minutes": s.data_error_cooldown_minutes,
        "ssl_enabled": s.ssl_enabled,
        "ssl_warn_days": s.ssl_warn_days,
        "calibration_enabled": s.calibration_enabled,
        "calibration_interval_days": s.calibration_interval_days,
        "calibration_warn_days": s.calibration_warn_days,
        "license_enabled": s.license_enabled,
        "license_warn_days": s.license_warn_days,
        "poweroff_enabled": s.poweroff_enabled,
        "poweroff_min_minutes": s.poweroff_min_minutes,
        "notify_sms": s.notify_sms,
        "notify_email": s.notify_email,
    }


@login_required
def system_alarms_save(request):
    """Sistem alarm ayarlarını kaydeder. POST JSON (tüm alanlar)."""
    import json

    denied = _require_operator(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST gerekli."}, status=405)

    from sais_domain.models import SystemAlarmSettings

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz JSON."}, status=400)

    def as_int(key, default, lo=0, hi=100000):
        try:
            return max(lo, min(hi, int(data.get(key, default))))
        except (ValueError, TypeError):
            return default

    s = SystemAlarmSettings.load()
    s.data_error_enabled = bool(data.get("data_error_enabled"))
    codes = data.get("data_error_codes") or []
    try:
        s.data_error_codes = sorted({int(c) for c in codes})
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Geçersiz hata kodu listesi."}, status=400)
    s.data_error_persist_minutes = as_int("data_error_persist_minutes", 5, 1, 60)
    s.data_error_cooldown_minutes = as_int("data_error_cooldown_minutes", 60, 5, 10080)
    s.ssl_enabled = bool(data.get("ssl_enabled"))
    s.ssl_warn_days = as_int("ssl_warn_days", 3, 1, 90)
    s.calibration_enabled = bool(data.get("calibration_enabled"))
    s.calibration_interval_days = as_int("calibration_interval_days", 30, 1, 365)
    s.calibration_warn_days = as_int("calibration_warn_days", 1, 0, 90)
    s.license_enabled = bool(data.get("license_enabled"))
    s.license_warn_days = as_int("license_warn_days", 7, 1, 90)
    s.poweroff_enabled = bool(data.get("poweroff_enabled"))
    s.poweroff_min_minutes = as_int("poweroff_min_minutes", 5, 1, 1440)
    s.notify_sms = bool(data.get("notify_sms"))
    s.notify_email = bool(data.get("notify_email"))
    s.updated_by = request.user if request.user.is_authenticated else None
    s.save()
    return JsonResponse({"ok": True, "settings": _alarm_settings_dict(s)})


@login_required
def system_alarms_status(request):
    """Sistem Alarmları sekmesi canlı durum (SSL/lisans/kalibrasyon/son bildirimler).

    SSL Let's Encrypt modunda canlı TLS kontrolü yapabilir (birkaç sn) — bu yüzden
    sayfa render'ında değil, sekme açılınca AJAX ile çağrılır."""
    denied = _require_operator(request)
    if denied:
        return denied
    from sais_domain.system_alarms import current_status
    try:
        return JsonResponse({"ok": True, "status": current_status()})
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
