"""
Bakanlık SIM ve Envisoft'a periyodik veri gönderim Celery task'ları.

Akış::

    publish_minute_data          (beat: cron */1 * * * *)
        │  dakika damgasını + yıkama override'ını SABİTLER
        └─ publish_cabinet_data.apply_async(..., expires=45)
                ├─ SIM      →  SimOutboxEntry (KUYRUĞA YAZAR, göndermez)
                └─ Envisoft →  EnvisoftClient.send_data  (gönder-unut, değişmedi)

    dispatch_sim_outbox          (beat: her 30 sn — emniyet ağı)
        │  takılı kayıtları kurtar + kabul penceresini aşanları düş
        └─ drain_sim_outbox.delay(cabinet_id)
                └─ sim_outbox.drain_cabinet()  →  SaisSimClient.send_data

    resend_missing_data          (beat: cron 0 */6 * * *)
        └─ resend_cabinet_missing.delay(cabinet_id)
                └─ GetMissingDates → historian → SimOutboxEntry (backfill şeridi)

**SIM gönderimi artık tek serileşmiş yoldan geçer:** kuyruk drenajı. Bakanlık
yanıt vermezse dakika kaybolmaz, kuyrukta bekler ve baştaki kayıt kabul
edilene kadar sonraki dakikalar gönderilmez (bkz. ``sais_domain.sim_outbox``).

Tek kabinin yavaş yanıtı diğerlerini geciktirmesin diye fan-out yapılır.
İki dış sistem (SIM + Envisoft) bağımsızdır. Tüm HTTP çağrıları
``BaseHttpClient`` üzerinden geçtiği için ``ApiLog(direction='out')``
satırları otomatik yazılır.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from . import sim_errors, sim_outbox
from .clients import EnvisoftClient, SaisClientError, SaisSimClient
from .models import SaisCabinet, SimOutboxEntry, SimValidDay, SystemSwitch
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

    **Dakika damgası burada sabitlenir** ve alt task'a parametre geçirilir.
    Eskiden ``readtime`` alt task'ın *çalıştığı* anda hesaplanıyordu; worker
    kuyruğu birikince task ait olduğu dakikayı değil koştuğu dakikayı
    damgalıyor, aynı dakika mükerrer yazılırken başka dakika hiç
    gönderilmemiş oluyordu.

    Damgayı sabitlemek ``expires`` OLMADAN güvenli değildir: geç çalışan bir
    task eski dakika damgasıyla *taze* sensör snapshot'ı yazardı. Bu yüzden
    mesajlara ömür verilir — bayat mesaj sessizce düşer.

    Yıkama/bakım override'ı da burada bir kez çözülür (eskiden her kabin
    task'ı ayrı ayrı okuyup aynı bayrağı eşzamanlı temizlemeye çalışıyordu).
    """
    # Lisans bitmişse veri yayını (SIM + Envisoft) durur.
    from api.licensing import license_active
    if not license_active():
        return {"dispatched": 0, "skipped": "license_inactive"}

    readtime = timezone.localtime().replace(second=0, microsecond=0)

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

    cabinet_ids = list(
        SaisCabinet.objects
        .filter(station__active=True)
        .values_list("id", flat=True)
    )
    expires = int(getattr(settings, "SAIS_PUBLISH_TASK_EXPIRES_SEC", 45))
    for cabinet_id in cabinet_ids:
        publish_cabinet_data.apply_async(
            args=[cabinet_id],
            kwargs={
                "readtime_iso": readtime.isoformat(),
                "force_status": force_status,
            },
            expires=expires,
        )
    logger.info("publish_minute_data: %d cabinet enqueue edildi", len(cabinet_ids))
    return {"dispatched": len(cabinet_ids), "readtime": readtime.isoformat()}


@shared_task(name="sais_domain.tasks.publish_cabinet_data")
def publish_cabinet_data(cabinet_id: int, readtime_iso: str | None = None,
                         force_status: int | None = None) -> dict:
    """Tek bir kabinin dakikalık verisini üretir.

    **SIM kolu artık göndermez, KUYRUĞA YAZAR** (``SimOutboxEntry``); asıl
    gönderimi ``drain_sim_outbox`` yapar. Böylece Bakanlık yanıt vermediğinde
    dakika kaybolmaz, kuyrukta bekler ve sıra korunur.

    Envisoft kolu değişmedi (gönder-unut) — kapsam yalnız SIM.

    ``readtime_iso``/``force_status`` normalde ``publish_minute_data``'dan
    gelir. Verilmezse (elle çağrı veya sürüm geçişi sırasında broker'da kalmış
    ESKİ mesaj) bugünkü davranışa düşülür: o an okunur. Bu geri-uyumluluk
    dalı zorunludur — Watchtower güncellemesinde uçuşta mesaj kalır.
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

    if readtime_iso:
        readtime = datetime.fromisoformat(readtime_iso)
        if timezone.is_naive(readtime):
            readtime = timezone.make_aware(readtime, timezone.get_current_timezone())
    else:
        # Eski mesaj / elle çağrı — bugünkü davranış.
        readtime = timezone.localtime().replace(second=0, microsecond=0)
        force_status = switch.active_wash_status_code()
    readtime = readtime.replace(second=0, microsecond=0)

    sim_result = _enqueue_sim(cabinet, readtime, force_status=force_status, switch=switch)
    envisoft_result = (
        _publish_envisoft(cabinet, readtime, force_status=force_status)
        if switch.envisoft_enabled
        else {"sent": False, "reason": "envisoft_disabled"}
    )

    # Kuyruğa yeni kayıt girdiyse hemen boşaltmayı dene (düşük gecikme yolu).
    # Kilit sayesinde beat dispatcher'ı ile yarışması zararsızdır.
    if sim_result.get("queued") and switch.sim_enabled:
        drain_sim_outbox.apply_async(args=[cabinet_id], expires=90)

    return {
        "cabinet_id": cabinet_id,
        "readtime": readtime.strftime("%Y-%m-%dT%H:%M:00"),
        "wash_status": force_status,
        "sim": sim_result,
        "envisoft": envisoft_result,
    }


def _enqueue_sim(cabinet: SaisCabinet, readtime, *, force_status: int | None = None,
                 switch=None) -> dict:
    """Dakikalık SIM payload'ını üretip kuyruğa yazar (göndermez).

    ``SystemSwitch.sim_enabled`` kapalıyken de kuyruğa yazılır (drenaj durur):
    aksi halde "SIM'i 10 dakikalığına kapatayım" denen sürede geçen dakikalar
    kalıcı olarak kaybolurdu. Kuyruk zaten kabul penceresi (48 saat) ile
    sınırlı olduğu için sınırsız büyümez. ``SAIS_SIM_OUTBOX_ENQUEUE_WHEN_DISABLED``
    ile eski davranışa dönülebilir.
    """
    switch = switch or SystemSwitch.load()
    if not switch.sim_enabled and not getattr(
        settings, "SAIS_SIM_OUTBOX_ENQUEUE_WHEN_DISABLED", True
    ):
        return {"queued": False, "reason": "sim_disabled"}

    payload = build_sim_payload(cabinet, readtime=readtime, force_status=force_status)
    if payload.is_empty:
        return {"queued": False, "reason": "no analog values"}

    entry, created = SimOutboxEntry.enqueue(
        cabinet,
        readtime=payload.readtime_dt or readtime,
        payload=payload.values,
        period=payload.period,
        priority=SimOutboxEntry.PRIORITY_LIVE,
        source=SimOutboxEntry.SOURCE_LIVE,
    )
    return {
        "queued": created,
        "values": len(payload.values),
        "entry_id": entry.id if entry else None,
        "sim_enabled": switch.sim_enabled,
    }


@shared_task(name="sais_domain.tasks.dispatch_sim_outbox")
def dispatch_sim_outbox() -> dict:
    """Kuyruk bakımını yapar + vadesi gelen kabinlere drenaj enqueue eder.

    Beat tarafından 30 sn'de bir çağrılır — üretici zaten her dakika drenaj
    zincirliyor; bu, kaybolan task / çökmüş worker / backoff sonrası uyanma
    durumlarını toparlayan emniyet ağıdır.
    """
    from api.licensing import license_active
    if not license_active():
        return {"skipped": "license_inactive"}
    if not getattr(settings, "SAIS_SIM_OUTBOX_ENABLED", True):
        return {"skipped": "outbox_disabled"}

    # Bakım adımları SIM kapalıyken de çalışmalı: aksi halde uzun süre kapalı
    # kalan bir sahada kuyruk sınırsız büyür ve açılınca çoktan değersizleşmiş
    # binlerce dakika gönderilmeye çalışılır.
    reaped = sim_outbox.reap_stuck_entries()
    expired = sim_outbox.expire_old_entries()

    switch = SystemSwitch.load()
    if not switch.sim_enabled:
        return {"skipped": "sim_disabled", "reaped": reaped, "expired": expired}

    cabinet_ids = sim_outbox.due_cabinet_ids()
    for cabinet_id in cabinet_ids:
        drain_sim_outbox.apply_async(args=[cabinet_id], expires=25)
    return {
        "reaped": reaped, "expired": expired, "dispatched": len(cabinet_ids),
    }


@shared_task(
    name="sais_domain.tasks.drain_sim_outbox",
    time_limit=900, soft_time_limit=840,
)
def drain_sim_outbox(cabinet_id: int) -> dict:
    """Bir kabinin gönderim kuyruğunu sıkı FIFO ile boşaltır.

    Kendi ``time_limit``'i vardır: en kötü tek gönderim (okuma timeout'u +
    bağlantı tekrarları) global ``CELERY_TASK_TIME_LIMIT=300``'e sığmayabilir.
    Bütçe kontrolü döngü içinde yapılır — süren istek kesilmez, yalnız yeni
    gönderim başlatılmaz.
    """
    from api.licensing import license_active
    if not license_active():
        return {"cabinet_id": cabinet_id, "skipped": "license_inactive"}
    if not getattr(settings, "SAIS_SIM_OUTBOX_ENABLED", True):
        return {"cabinet_id": cabinet_id, "skipped": "outbox_disabled"}
    if not SystemSwitch.load().sim_enabled:
        return {"cabinet_id": cabinet_id, "skipped": "sim_disabled"}

    cabinet = (
        SaisCabinet.objects
        .select_related("station")
        .filter(pk=cabinet_id)
        .first()
    )
    if cabinet is None:
        return {"cabinet_id": cabinet_id, "skipped": "cabinet bulunamadı"}

    return sim_outbox.drain_cabinet(cabinet)


def _publish_sim(cabinet: SaisCabinet, readtime, *, force_status: int | None = None) -> dict:
    """DOĞRUDAN gönderim (kuyruk devre dışıyken / debug için).

    Normal akış artık kuyruk üzerindendir (``_enqueue_sim`` + ``drain_sim_outbox``).
    Bu fonksiyon geriye dönük kaçış yolu olarak korunur; ``send_data`` artık ham
    zarf döndürdüğü için kabul/ret ayrımı ``sim_errors`` ile yapılır.
    """
    payload = build_sim_payload(cabinet, readtime=readtime, force_status=force_status)
    if payload.is_empty:
        return {"sent": False, "reason": "no analog values"}
    try:
        with SaisSimClient(cabinet) as client:
            envelope = client.send_data(
                readtime=payload.readtime,
                values=payload.values,
                period=payload.period,
            )
    except SaisClientError as exc:
        logger.warning("SIM SendData başarısız (cabinet=%s): %s", cabinet.id, exc)
        return {"sent": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("SIM SendData beklenmedik hata (cabinet=%s)", cabinet.id)
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}

    outcome, category, message = sim_errors.classify_send_result(envelope=envelope)
    if outcome == sim_errors.ACCEPTED:
        SystemSwitch.mark_sim_success(payload.readtime)
        return {"sent": True, "values": len(payload.values)}
    return {"sent": False, "error": f"{category}: {message}"}


@shared_task(name="sais_domain.tasks.resend_missing_data")
def resend_missing_data() -> dict:
    """Bakanlık'ın eksik bildirdiği verileri **kuyruğa alır** (fan-out).

    Beat tarafından **6 saatte bir** çağrılır. Her aktif kabin için ayrı bir
    ``resend_cabinet_missing`` task'ı enqueue edilir.

    **Artık doğrudan GÖNDERMEZ.** Eski hâli tek task içinde 720'ye kadar seri
    ``SendData`` çağırıyordu; her biri timeout'a kadar bloklayabildiği için
    ``CELERY_TASK_TIME_LIMIT=300`` gerçek bir birikimde döngüyü ortasından
    kesiyordu. Artık eksik dakikalar ``SimOutboxEntry``'ye **backfill**
    önceliğiyle yazılır; gönderimi tek serileşmiş yol olan drenaj yapar. Bu
    sayede eksik veri yığını canlı dakikaların önüne geçemez ve gönderim yine
    sıkı FIFO + tıkanma kurallarına uyar.

    Gate'ler (ikisi de ``publish_minute_data`` ile aynı mantık):

    - Lisans aktif değilse → no-op.
    - ``SystemSwitch.sim_enabled`` kapalıysa → no-op.
    """
    from api.licensing import license_active
    if not license_active():
        return {"skipped": "license_inactive"}

    switch = SystemSwitch.load()
    if not switch.sim_enabled:
        return {"skipped": "sim_disabled"}

    cabinet_ids = list(
        SaisCabinet.objects
        .filter(station__active=True)
        .values_list("id", flat=True)
    )
    # Sayaçları sıfırla + kontrol damgasını at; alt task'lar F() ile artırır.
    SystemSwitch.mark_missing_run(found=0, resent=0, error="")
    for cabinet_id in cabinet_ids:
        resend_cabinet_missing.apply_async(args=[cabinet_id], expires=1800)
    logger.info("resend_missing_data: %d kabin enqueue edildi", len(cabinet_ids))
    return {"dispatched": len(cabinet_ids)}


@shared_task(
    name="sais_domain.tasks.resend_cabinet_missing",
    time_limit=600, soft_time_limit=540,
)
def resend_cabinet_missing(cabinet_id: int) -> dict:
    """Tek kabin için ``GetMissingDates`` → historian backfill → **kuyruğa yaz**.

    Bakanlık'ın eksik dakika listesini çeker, kabul penceresiyle sınırlar, en
    eski dakikalardan başlayarak ``SAIS_MISSING_RESEND_MAX_MINUTES`` kadarını
    ``Reading`` historian'ından doldurup kuyruğa ekler.

    ``found`` = işlenmeye aday eksik dakika, ``enqueued`` = kuyruğa yeni
    eklenen dakika (zaten kuyrukta bekleyen dakikalar tekrar eklenmez —
    ``SimOutboxEntry.enqueue`` kısmi unique kısıtla bunu garanti eder).
    """
    from api.licensing import license_active
    if not license_active():
        return {"cabinet_id": cabinet_id, "skipped": "license_inactive"}

    switch = SystemSwitch.load()
    if not switch.sim_enabled:
        return {"cabinet_id": cabinet_id, "skipped": "sim_disabled"}

    cabinet = (
        SaisCabinet.objects
        .select_related("station")
        .filter(pk=cabinet_id)
        .first()
    )
    if cabinet is None:
        return {"cabinet_id": cabinet_id, "skipped": "cabinet bulunamadı"}

    max_minutes = int(getattr(settings, "SAIS_MISSING_RESEND_MAX_MINUTES", 720))
    lookback = int(getattr(settings, "SAIS_MISSING_BACKFILL_LOOKBACK_MIN", 10))

    try:
        with SaisSimClient(cabinet) as client:
            objects = client.get_missing_dates()
    except Exception as exc:  # noqa: BLE001 — bir kabin diğerlerini durdurmasın
        logger.exception("resend_cabinet_missing: cabinet=%s başarısız", cabinet_id)
        SystemSwitch.objects.filter(pk=1).update(
            last_missing_error=f"{cabinet.device_id}: {type(exc).__name__}: {exc}"[:300],
        )
        return {"cabinet_id": cabinet_id, "error": str(exc)}

    raw_dates = objects.get("MissingDates") or [] if isinstance(objects, dict) else []
    targets = _parse_missing_dates(raw_dates)
    if not targets:
        return {"cabinet_id": cabinet_id, "found": 0, "enqueued": 0}

    # En eskiden başla; cap aşılırsa kalan sonraki run'da toparlanır.
    targets = targets[:max_minutes]

    payloads = build_sim_payloads_for_times(
        cabinet, targets, lookback_minutes=lookback,
    )

    enqueued = 0
    for payload in payloads:
        if payload.is_empty or not payload.readtime_dt:
            continue
        _, created = SimOutboxEntry.enqueue(
            cabinet,
            readtime=payload.readtime_dt,
            payload=payload.values,
            period=payload.period,
            priority=SimOutboxEntry.PRIORITY_BACKFILL,
            source=SimOutboxEntry.SOURCE_MISSING,
        )
        enqueued += int(created)

    SystemSwitch.objects.filter(pk=1).update(
        last_missing_found_count=F("last_missing_found_count") + len(targets),
        last_missing_resent_count=F("last_missing_resent_count") + enqueued,
    )
    if enqueued:
        drain_sim_outbox.apply_async(args=[cabinet_id], expires=120)
    logger.info(
        "resend_cabinet_missing: cabinet=%s, %d eksik dakika, %d kuyruğa alındı",
        cabinet_id, len(targets), enqueued,
    )
    return {"cabinet_id": cabinet_id, "found": len(targets), "enqueued": enqueued}


def _parse_missing_dates(raw_dates, window_hours=None) -> list[datetime]:
    """Bakanlık ISO tarih string'lerini TZ-aware datetime listesine çevirir.

    Bakanlık naive yerel saat verir (``2020-11-24T00:55:00``). Yalnız kabul
    penceresi içindeki dakikalar tutulur — bu pencere normalde 48 saattir ama
    Bakanlık bakım vb. durumlarda genişletebildiği için ``SystemSwitch``
    üzerinden operatör tarafından ayarlanabilir (Sistem Kontrol sayfası).
    Sıralı + tekilleştirilmiş döner (en eski → en yeni).
    """
    if window_hours is None:
        window_hours = sim_outbox.accept_window_hours()
    cutoff = timezone.now() - timedelta(hours=int(window_hours))
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


@shared_task(name="sais_domain.tasks.prune_sim_outbox_task")
def prune_sim_outbox_task() -> dict:
    """SİM gönderim kuyruğu retention'ı (gecelik).

    Bekleyen kayıtlara dokunmaz; yalnız sonuçlanmış kayıtları temizler.
    Lisans-gate'siz (housekeeping) — kuyruk lisans bitse de şişmemeli.
    """
    from django.core.management import call_command
    call_command("prune_sim_outbox")
    return {"ok": True}
