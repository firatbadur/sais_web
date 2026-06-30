"""
Bakanlık SIM ve Envisoft'a periyodik veri gönderim Celery task'ları.

Akış::

    publish_minute_data         (beat: cron */1 * * * *)
        └─ aktif kabinler  →  publish_cabinet_data.delay(cabinet_id)
                ├─ SaisSimClient.send_data(...)
                └─ EnvisoftClient.send_data(...)

Tek kabinin yavaş yanıtı diğerlerini geciktirmesin diye fan-out yapılır.
İki dış sistem (SIM + Envisoft) bağımsızdır — biri fail ederse diğeri
yine denenir. Tüm HTTP çağrıları ``BaseHttpClient`` üzerinden geçtiği
için ``ApiLog(direction='out')`` satırları otomatik yazılır.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .clients import EnvisoftClient, SaisClientError, SaisSimClient
from .models import SaisCabinet, SimValidDay, SystemSwitch
from .sim_report import detect_params, valid_counts
from .services import (
    build_envisoft_rows,
    build_sim_payload,
    build_sim_payloads_for_times,
)


logger = logging.getLogger("sais_domain.tasks")


@shared_task(name="sais_domain.tasks.run_scenarios")
def run_scenarios() -> dict:
    """Aktif numune alma senaryolarını değerlendirir + açık run'ları ilerletir.

    Lisans bitmişse senaryo motoru da durur (polling/yayın gibi) — fiziksel
    numune alıcı tetiği ve SIM bildirimleri yapılmaz.
    """
    from api.licensing import license_active
    if not license_active():
        return {"skipped": "license_inactive"}
    from .scenario_engine import run
    return run()


@shared_task(name="sais_domain.tasks.advance_scenario_run")
def advance_scenario_run(run_id: int) -> dict:
    """Tek bir açık senaryo run'ını ilerletir (SIM I/O içerir).

    `request_ministry` (StartSample servisi + dashboard operatör talebi) HTTP
    isteğini bekletmemek için step yürütmesini bu task'a devreder. Lisans
    bitmişse senaryo motoru gibi durur.
    """
    from api.licensing import license_active
    if not license_active():
        return {"run_id": run_id, "skipped": "license_inactive"}
    from .scenario_engine import advance_open_run
    advance_open_run(run_id)
    return {"run_id": run_id}


@shared_task(name="sais_domain.tasks.publish_minute_data")
def publish_minute_data() -> dict:
    """Aktif kabinler için per-cabinet publish task'ları enqueue eder.

    Beat tarafından her dakika başında çağrılır. Tek tek kabinler için
    fan-out — bir kabinin yavaş yanıtı diğerlerini geciktirmez.
    """
    # Lisans bitmişse veri yayını (SIM + Envisoft) durur.
    from api.licensing import license_active
    if not license_active():
        return {"dispatched": 0, "skipped": "license_inactive"}

    cabinet_ids = list(
        SaisCabinet.objects
        .filter(station__active=True)
        .values_list("id", flat=True)
    )
    for cabinet_id in cabinet_ids:
        publish_cabinet_data.delay(cabinet_id)
    logger.info("publish_minute_data: %d cabinet enqueue edildi", len(cabinet_ids))
    return {"dispatched": len(cabinet_ids)}


@shared_task(name="sais_domain.tasks.publish_cabinet_data")
def publish_cabinet_data(cabinet_id: int) -> dict:
    """Tek bir kabinin SIM + Envisoft veri gönderimini yapar.

    İki gönderim birbirinden bağımsız çalışır; istisnalar log'lanır ama
    fırlatılmaz — bir kabinin patlaması beat'i etkilemez. Her başarısız
    çağrı zaten ``ApiLog`` üstünde error_message ile görünür durumda.
    """
    # Defansif: lisans bitmişse gönderim yapılmaz.
    from api.licensing import license_active
    if not license_active():
        return {"cabinet_id": cabinet_id, "skipped": "license_inactive"}

    cabinet = (
        SaisCabinet.objects
        .select_related("station")
        .filter(pk=cabinet_id)
        .first()
    )
    if cabinet is None:
        logger.warning("publish_cabinet_data: cabinet=%s bulunamadı", cabinet_id)
        return {"cabinet_id": cabinet_id, "skipped": "cabinet bulunamadı"}
    if not cabinet.station_id or not cabinet.station.active:
        return {"cabinet_id": cabinet_id, "skipped": "istasyon pasif"}

    switch = SystemSwitch.load()
    force_status = switch.active_wash_status_code()

    # Yıkama süresi bittiyse state'i temizle — race-free conditional update,
    # böylece eş zamanlı task'lar bayrağı çiftleyemez. Bayrak set ama
    # active_wash_status_code() None döndüyse süre dolmuş demektir.
    if force_status is None and switch.wash_active_kind:
        SystemSwitch.objects.filter(pk=1, wash_active_kind=switch.wash_active_kind).update(
            wash_active_kind=None,
            wash_started_at=None,
            wash_ends_at=None,
            wash_started_by=None,
        )

    readtime = timezone.localtime()

    sim_result = (
        _publish_sim(cabinet, readtime, force_status=force_status) if switch.sim_enabled
        else {"sent": False, "reason": "sim_disabled"}
    )
    envisoft_result = (
        _publish_envisoft(cabinet, readtime, force_status=force_status) if switch.envisoft_enabled
        else {"sent": False, "reason": "envisoft_disabled"}
    )

    return {
        "cabinet_id": cabinet_id,
        "readtime": readtime.strftime("%Y-%m-%dT%H:%M:00"),
        "wash_status": force_status,
        "sim": sim_result,
        "envisoft": envisoft_result,
    }


def _publish_sim(cabinet: SaisCabinet, readtime, *, force_status: int | None = None) -> dict:
    payload = build_sim_payload(cabinet, readtime=readtime, force_status=force_status)
    if payload.is_empty:
        return {"sent": False, "reason": "no analog values"}
    try:
        with SaisSimClient(cabinet) as client:
            result = client.send_data(
                readtime=payload.readtime,
                values=payload.values,
                period=payload.period,
            )
        # Bakanlık 200 + result:true ile kabul ettiyse son iletim damgasını yaz.
        accepted = True
        if isinstance(result, dict) and "result" in result:
            accepted = bool(result["result"])
        if accepted:
            SystemSwitch.mark_sim_success(payload.readtime)
        return {"sent": True, "values": len(payload.values), "result": result}
    except SaisClientError as exc:
        logger.warning(
            "SIM SendData başarısız (cabinet=%s): %s", cabinet.id, exc,
        )
        return {"sent": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "SIM SendData beklenmedik hata (cabinet=%s)", cabinet.id,
        )
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


@shared_task(name="sais_domain.tasks.resend_missing_data")
def resend_missing_data() -> dict:
    """Bakanlık'ın eksik bildirdiği (son 48 saat) verileri yeniden gönderir.

    Beat tarafından **6 saatte bir** çağrılır. Her aktif kabin için Bakanlık
    ``GetMissingDates`` servisini sorgular; dönen eksik dakikaları ``Reading``
    historian'ından doldurup ``SendData`` ile yeniden iletir.

    Gate'ler (ikisi de ``publish_minute_data`` ile aynı mantık):

    - Lisans aktif değilse → no-op.
    - ``SystemSwitch.sim_enabled`` kapalıysa → no-op. (Kullanıcı isteği: "SIM'e
      veri gönder" pasifken eksik veri gönderimi de pasif olur.)

    Tek run'da gönderilen dakika sayısı ``SAIS_MISSING_RESEND_MAX_MINUTES`` ile
    sınırlı; kalan dakikalar bir sonraki run'da toparlanır (eksik kümesi
    küçüldükçe yakınsar). Sonuç ``SystemSwitch``'e damgalanır (Sistem Kontrol
    sayfasında görüntülenir).
    """
    from api.licensing import license_active
    if not license_active():
        return {"skipped": "license_inactive"}

    switch = SystemSwitch.load()
    if not switch.sim_enabled:
        return {"skipped": "sim_disabled"}

    cabinets = list(
        SaisCabinet.objects
        .select_related("station")
        .filter(station__active=True)
    )

    total_found = 0
    total_resent = 0
    errors: list[str] = []
    for cabinet in cabinets:
        try:
            result = _resend_cabinet_missing(cabinet)
            total_found += result["found"]
            total_resent += result["resent"]
        except Exception as exc:  # noqa: BLE001 — bir kabin diğerlerini durdurmasın
            logger.exception(
                "resend_missing_data: cabinet=%s başarısız", cabinet.id,
            )
            errors.append(f"{cabinet.device_id}: {type(exc).__name__}: {exc}")

    SystemSwitch.mark_missing_run(
        found=total_found,
        resent=total_resent,
        error="; ".join(errors),
    )
    logger.info(
        "resend_missing_data: %d kabin, %d eksik dakika bulundu, %d yeniden gönderildi",
        len(cabinets), total_found, total_resent,
    )
    return {
        "cabinets": len(cabinets),
        "found": total_found,
        "resent": total_resent,
        "errors": errors,
    }


def _resend_cabinet_missing(cabinet: SaisCabinet) -> dict:
    """Tek kabin için ``GetMissingDates`` → backfill → ``SendData`` akışı.

    Bakanlık'ın eksik dakika listesini çeker, son 48 saatle sınırlar, en eski
    dakikalardan başlayarak ``SAIS_MISSING_RESEND_MAX_MINUTES`` kadarını
    ``Reading`` historian'ından doldurup gönderir. ``found`` = eksik dakika
    sayısı (cap sonrası işlenmeye aday), ``resent`` = Bakanlık'ın 200 + result
    ile kabul ettiği dakika sayısı.
    """
    max_minutes = int(getattr(settings, "SAIS_MISSING_RESEND_MAX_MINUTES", 720))
    lookback = int(getattr(settings, "SAIS_MISSING_BACKFILL_LOOKBACK_MIN", 10))

    with SaisSimClient(cabinet) as client:
        objects = client.get_missing_dates()
        raw_dates = []
        if isinstance(objects, dict):
            raw_dates = objects.get("MissingDates") or []

        targets = _parse_missing_dates(raw_dates)
        if not targets:
            return {"found": 0, "resent": 0}

        # En eskiden başla; cap aşılırsa kalan sonraki run'da toparlanır.
        targets = targets[:max_minutes]

        payloads = build_sim_payloads_for_times(
            cabinet, targets, lookback_minutes=lookback,
        )

        resent = 0
        for payload in payloads:
            if payload.is_empty:
                continue
            try:
                result = client.send_data(
                    readtime=payload.readtime,
                    values=payload.values,
                    period=payload.period,
                )
            except SaisClientError as exc:
                logger.warning(
                    "resend_missing_data SendData başarısız (cabinet=%s, readtime=%s): %s",
                    cabinet.id, payload.readtime, exc,
                )
                continue
            accepted = True
            if isinstance(result, dict) and "result" in result:
                accepted = bool(result["result"])
            if accepted:
                resent += 1

        return {"found": len(targets), "resent": resent}


def _parse_missing_dates(raw_dates) -> list[datetime]:
    """Bakanlık ISO tarih string'lerini TZ-aware datetime listesine çevirir.

    Bakanlık naive yerel saat verir (``2020-11-24T00:55:00``). Yalnız son 48
    saatteki dakikalar tutulur (servis sözleşmesi de 48 saatle sınırlı; defansif
    filtre). Sıralı + tekilleştirilmiş döner (en eski → en yeni).
    """
    cutoff = timezone.now() - timedelta(hours=48)
    out: set[datetime] = set()
    for item in raw_dates:
        if not item:
            continue
        try:
            dt = datetime.fromisoformat(str(item))
        except ValueError:
            continue
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        if dt >= cutoff:
            out.add(dt)
    return sorted(out)


def _publish_envisoft(cabinet: SaisCabinet, readtime, *, force_status: int | None = None) -> dict:
    rows = build_envisoft_rows(cabinet, readtime=readtime, force_status=force_status)
    if not rows:
        return {"sent": False, "reason": "no rows"}
    try:
        with EnvisoftClient(cabinet) as client:
            response = client.send_data(rows)
        return {
            "sent": True,
            "rows": len(rows),
            "status": response.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Envisoft SendData hata (cabinet=%s)", cabinet.id,
        )
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Geçerli veri istatistiği — günde 1 kez, gün gün (kısım kısım) hesaplama
# ---------------------------------------------------------------------------
# Aylık geçerli veri oranını dashboard'da göstermek için ayın tamamını tek
# istekle çekmek yerine (31 gün ≈ 44k kayıt — ağır), bu job ayın her gününü
# AYRI ``GetDataByBetweenTwoDate`` (period=1) ile çeker ve ``SimValidDay``'e
# damgalar. Geçmiş günler bir kez hesaplanıp ``finalized`` edilir; yalnız bugün
# her run'da güncellenir. Dashboard aylık kartı bu satırların DB toplamından
# beslenir → SİM'e canlı ay-sorgusu YOK. Geçerlilik **yalnız doğrulanmış
# (``_N``) status** üzerinden sayılır.


def _expected_minutes_for_day(day, now):
    """O güne ait beklenen dakika sayısı: geçmiş gün=1440, bugün=o ana kadar."""
    if day < now.date():
        return 1440
    if day == now.date():
        midnight = datetime(day.year, day.month, day.day)
        return max(1, min(1440, int((now - midnight).total_seconds() // 60) + 1))
    return 0  # gelecek


def _compute_cabinet_day(cabinet, day, client, *, finalize, now):
    """Tek kabin + tek gün → Bakanlık'a TEK istek; SimValidDay upsert eder."""
    start = f"{day.isoformat()} 00:00:00"
    if day == now.date():
        end = now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        end = f"{day.isoformat()} 23:59:59"
    objects = client.get_data_between(period=1, start_date=start, end_date=end)
    rows = objects if isinstance(objects, list) else []
    params = detect_params(rows)
    counts = valid_counts(rows, params)
    SimValidDay.objects.update_or_create(
        cabinet=cabinet, day=day,
        defaults={
            "expected": _expected_minutes_for_day(day, now),
            "received": len(rows),
            "param_valid": counts,
            "param_count": len(params),
            "finalized": finalize,
        },
    )
    return len(rows)


@shared_task(name="sais_domain.tasks.compute_sim_valid_stats")
def compute_sim_valid_stats(cabinet_id=None, month=None) -> dict:
    """Aktif kabinler için bir ayın günlük geçerli-veri istatistiğini hesaplar.

    Gün gün (kısım kısım) çeker; geçmiş + finalize edilmiş günleri atlar, bugünü
    yeniden hesaplar. ``cabinet_id`` verilirse yalnız o kabin; ``month`` (YYYY-MM)
    verilirse o ay (yoksa geçerli ay) — manuel yeniden hesaplama/backfill için.
    """
    from calendar import monthrange
    from datetime import date

    from api.licensing import license_active
    if not license_active():
        return {"skipped": "license_inactive"}

    now = timezone.localtime().replace(tzinfo=None)
    if month:
        try:
            year, mon = (int(x) for x in str(month).split("-"))
        except (ValueError, TypeError):
            return {"error": "bad_month"}
    else:
        year, mon = now.year, now.month

    today = now.date()
    first = date(year, mon, 1)
    end_day = min(date(year, mon, monthrange(year, mon)[1]), today)
    if end_day < first:
        return {"skipped": "future_month", "month": f"{year:04d}-{mon:02d}"}

    cabs = SaisCabinet.objects.filter(station__active=True)
    if cabinet_id:
        cabs = cabs.filter(id=cabinet_id)
    cabs = list(cabs)

    computed = 0
    for cabinet in cabs:
        existing = {
            d.day: d for d in SimValidDay.objects.filter(
                cabinet=cabinet, day__gte=first, day__lte=end_day,
            )
        }
        client = SaisSimClient(cabinet)
        try:
            day = first
            while day <= end_day:
                rec = existing.get(day)
                # Geçmiş + zaten kesinleşmiş gün → atla (tekrar SİM'e gitme).
                if rec and rec.finalized and day < today:
                    day += timedelta(days=1)
                    continue
                try:
                    _compute_cabinet_day(
                        cabinet, day, client,
                        finalize=(day < today), now=now,
                    )
                    computed += 1
                except Exception as exc:  # noqa: BLE001 — bir gün patlasa diğerleri sürsün
                    logger.warning(
                        "compute_sim_valid_stats hata cab=%s day=%s: %s",
                        cabinet.id, day, exc,
                    )
                day += timedelta(days=1)
        finally:
            client.close()

    return {
        "cabinets": len(cabs),
        "days_computed": computed,
        "month": f"{year:04d}-{mon:02d}",
    }


# ---------------------------------------------------------------------------
# Sistem uyarı mekanizmaları (SSL / lisans / kalibrasyon / kesinti / veri hatası)
# ---------------------------------------------------------------------------
# Sistem Kontrol → Sistem Alarmları'ndan toggle'lı. İki beat:
#   check_system_alarms       (her 10 dk)  → Bakanlık veri hatası + kesinti
#   check_system_alarms_daily (günde 1)    → SSL + lisans + kalibrasyon
# Motor sais_domain.system_alarms; bildirim send_bulk(kind="alarm").


@shared_task(name="sais_domain.tasks.check_system_alarms")
def check_system_alarms() -> dict:
    """10 dakikada bir: Bakanlık veri hatası (son 10 dk) + kesinti kontrolü."""
    from .system_alarms import run_realtime
    return run_realtime()


@shared_task(name="sais_domain.tasks.check_system_alarms_daily")
def check_system_alarms_daily() -> dict:
    """Günde bir: SSL bitiş + lisans bitiş + kalibrasyon hatırlatma."""
    from .system_alarms import run_daily
    return run_daily()
