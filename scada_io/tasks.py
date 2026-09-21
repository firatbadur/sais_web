"""
SCADA I/O için Celery task'lar.

Beat şu task'ları periyodik olarak tetikler:
  - dispatch_polls       → due olan Connection'lara poll_connection enqueue
  - dispatch_commands    → pending Command'lara execute_command enqueue
  - expire_commands      → expires_at geçmiş Command'ları 'expired' yap

Worker tarafında çalışan task'lar:
  - poll_connection(conn_id)  → tek bağlantı için tüm aktif sensörleri oku
  - execute_command(cmd_id)   → tek Command'ı yürüt (state machine ile)
"""
from __future__ import annotations

import logging
import random
import time
from datetime import timedelta

from celery import shared_task
from celery.worker.control import control_command
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from api.models import Command, Connection, Sensor

from . import connection_pool
from .comm_errors import CONN_RESET, category_source, classify_comm_error
from .decoders import decode_sensor_from_batch
from .persistence import persist_reading
from .readers import ReadResult, build_reader
from .writers import build_writer


logger = logging.getLogger(__name__)


# İletişim hata olayı (CommErrorEvent) hacim throttle'ı — worker-process local.
# Aynı (connection, sensor, category) için COMM_ERROR_MIN_INTERVAL_SEC'te en
# fazla bir olay yazılır: kalıcı-hatalı bir bağlantı her polling cycle'ında
# sensör başına satır üretip tabloyu şişirmesin. Kategori değişimi yeni anahtar
# → hemen yazılır (patern değişimi yakalanır). Worker restart'ta sıfırlanır.
_COMM_ERR_LAST: dict[tuple, float] = {}


def _record_comm_error(conn_id, sensor_id, error_text, status_code) -> None:
    """İletişim hatasını `CommErrorEvent`'e teşhis amacıyla yaz (defansif + throttle'lı).

    SALT gözlem — `status_code` atamasını, SIM yayınını veya polling akışını
    değiştirmez. `log_event` gibi asla exception fırlatmaz (asıl polling'i kesmez).
    """
    try:
        from django.conf import settings

        category = classify_comm_error(error_text)
        min_interval = int(getattr(settings, "COMM_ERROR_MIN_INTERVAL_SEC", 300))
        key = (conn_id, sensor_id, category)
        now = time.monotonic()
        last = _COMM_ERR_LAST.get(key)
        if last is not None and (now - last) < min_interval:
            return
        _COMM_ERR_LAST[key] = now

        from api.models import CommErrorEvent
        CommErrorEvent.objects.create(
            connection_id=conn_id,
            sensor_id=sensor_id,
            category=category,
            source=category_source(category),
            status_code=status_code,
            detail=(error_text or "")[:500],
        )
    except Exception:  # noqa: BLE001 — teşhis kaydı asıl akışı kesmesin
        logger.debug("CommErrorEvent yazılamadı conn=%s sensor=%s", conn_id, sensor_id,
                     exc_info=True)


# --------------------------------------------------------------------------- #
# Remote control command — açık bağlantı pool'unu kapat
# --------------------------------------------------------------------------- #
@control_command()
def close_scada_pool(state, reason: str = "manual"):
    """Worker'ın persistent connection pool'undaki tüm socket'leri kapatır.

    Dashboard "Açık Bağlantıları Kapat" düğmesinden `app.control.broadcast(
    "close_scada_pool", ...)` ile çağrılır. Pool worker process'ine özel
    (module-global) olduğu için yalnız o process içinden kapatılabilir; bu
    komut worker'ın kendi belleğindeki socket'leri kapatır.

    Not: prefork pool'da (prod, --concurrency=4) bu komut yalnız MainProcess'te
    çalışır; child process'lerdeki socket'ler `pool_restart` ile process geri
    dönüştürülerek (OS socket'i kapatır) temizlenir. Solo pool'da (dev) ana
    process pool'u tuttuğu için komut doğrudan kapatır.
    """
    closed = connection_pool.close_all(reason=f"control:{reason}")
    logger.info("close_scada_pool control command: %d bağlantı kapatıldı", closed)
    return {"ok": True, "closed": closed}


# --------------------------------------------------------------------------- #
# Polling
# --------------------------------------------------------------------------- #

# Bağlantı başına dağıtık polling kilidi (Redis `SETNX` — `cache.add` atomiktir).
#
# NEDEN: `connection_pool` worker PROCESS'ine özeldir; prefork'ta
# (`--concurrency=4`) aynı Connection farklı child'larda eşzamanlı poll'lanırsa
# aynı cihaza aynı anda 2-4 TCP oturumu açılır. Küçük PLC'ler (Mikrodev vb.)
# yalnız 1-2 eşzamanlı Modbus TCP oturumu kabul eder; fazlası gelince eskisini
# RST ile düşürürler → `ConnectionResetError [Errno 104]` / `BrokenPipeError
# [Errno 32]` ve o cycle'ın TÜM sensörleri bad olur.
#
# Çakışma bir yarış değil, NORMAL bir sonuçtur: `last_polled_at` cycle'ın
# BAŞINDA damgalandığı için bir cycle `poll_interval_sec`'ten uzun sürerse
# (timeout × retry bloklaması) `dispatch_polls` aynı bağlantıyı bitmeden yeniden
# kuyruğa atar. Kilit bunu keser → cihaza aynı anda tek oturum.
_POLL_LOCK_PREFIX = "scada:poll:"

# Kilit TTL'i: task hard limit'i (120 sn) + marj. Worker SIGKILL yerse kilit en
# fazla bu kadar asılı kalır (normal akışta `finally` ile bırakılır).
POLL_LOCK_TTL_SEC = 150


def _poll_lock_key(conn_id: int) -> str:
    return f"{_POLL_LOCK_PREFIX}{conn_id}"


def _acquire_poll_lock(conn_id: int) -> bool:
    """Bağlantı için polling kilidini al (atomik). Alındıysa ``True``.

    Cache backend yoksa/erişilemezse kilit uygulanmaz (eski davranış) — teşhis
    altyapısı asıl polling'i durdurmamalı.
    """
    try:
        return bool(cache.add(_poll_lock_key(conn_id), "1", POLL_LOCK_TTL_SEC))
    except Exception:  # noqa: BLE001
        logger.debug("poll lock alınamadı (cache erişimi) conn=%s", conn_id, exc_info=True)
        return True


def _release_poll_lock(conn_id: int) -> None:
    try:
        cache.delete(_poll_lock_key(conn_id))
    except Exception:  # noqa: BLE001
        logger.debug("poll lock bırakılamadı conn=%s", conn_id, exc_info=True)


def _poll_lock_busy(conn_id: int) -> bool:
    """Kilit şu an tutuluyor mu? (dispatch tarafında boş yere enqueue etmemek için)"""
    try:
        return cache.get(_poll_lock_key(conn_id)) is not None
    except Exception:  # noqa: BLE001
        return False


@shared_task(name="scada_io.tasks.dispatch_polls")
def dispatch_polls():
    """Beat tarafından her 5 sn'de tetiklenir. Due olan bağlantıları enqueue eder.

    Bir bağlantı 'due' kabul edilir:
      last_polled_at == None  (hiç polling yapılmamış), VEYA
      now - last_polled_at >= poll_interval_sec

    Global `SystemSwitch.polling_enabled` kapalıysa hiçbir bağlantı enqueue
    edilmez (yönetici dashboard'dan kapatılabilir).
    """
    # Lisans bitmişse hiçbir iş yapılmaz (okuma durur).
    from api.licensing import license_active
    if not license_active():
        return 0

    # Lazy import — scada_io app sais_domain'a yapısal olarak bağımlı değil;
    # import cycle ve test izolasyonu için runtime'da yükleniyor.
    from sais_domain.models import SystemSwitch
    if not SystemSwitch.load().polling_enabled:
        return 0

    now = timezone.now()
    enqueued = 0
    for conn in Connection.objects.filter(is_enabled=True):
        interval = conn.poll_interval_sec or 10
        if conn.last_polled_at is None or (now - conn.last_polled_at) >= timedelta(seconds=interval):
            # Önceki cycle hâlâ sürüyorsa (kilit tutuluyor) kuyruğa atma:
            # hem cihaza ikinci oturum açılmasını hem de her 5 sn'de bir boşa
            # dönen task birikmesini önler.
            if _poll_lock_busy(conn.pk):
                logger.debug("dispatch_polls: conn=%s hâlâ pollanıyor, atlandı", conn.pk)
                continue
            # `expires`: kuyrukta bekleyip bayatlayan poll çalışmadan düşsün.
            # Aksi halde worker meşgulken biriken task'lar sonradan sırayla
            # koşar; operatör Sistem Kontrol'den polling'i KAPATSA bile birikmiş
            # kuyruk dakikalarca cihaza bağlanmaya devam eder (saha gözlemi:
            # "kapattım ama son poll 5 sn önce").
            poll_connection.apply_async(
                args=(conn.pk,), expires=max(30, interval * 2),
            )
            enqueued += 1
    if enqueued:
        logger.info("dispatch_polls: %d connection enqueued", enqueued)
    return enqueued


@shared_task(
    name="scada_io.tasks.poll_connection",
    bind=True, max_retries=0, time_limit=120, soft_time_limit=110,
)
def poll_connection(self, conn_id: int):
    """Tek bağlantıdaki tüm aktif sensörleri sırayla oku.

    Hata stratejisi:
      - Reader.open() başarısız → Connection.last_error_* günceller, retry yapmaz
        (bir sonraki dispatch_polls cycle'ı tekrar dener; auto_reconnect davranışı
         polling cycle'ı kendi içinde sağlanır).
      - Bireysel sensör hatası → o sensör için 'bad' kalite Reading yazılır,
        diğer sensörler okunmaya devam eder.
    """
    # Defansif: dispatch sonrası lisans bitmiş olabilir.
    from api.licensing import license_active
    if not license_active():
        return

    # Defansif: dispatch ile çalıştırma arasında operatör Sistem Kontrol'den
    # polling'i kapatmış olabilir. Anahtar YALNIZ dispatch'te kontrol edilseydi
    # kuyrukta bekleyen task'lar kapatıldıktan sonra da cihaza bağlanmaya devam
    # ederdi (saha gözlemi: SCADA kapalıyken "Son poll 5 sn önce").
    from sais_domain.models import SystemSwitch
    if not SystemSwitch.load().polling_enabled:
        logger.info("poll_connection: polling kapalı, conn=%s atlandı", conn_id)
        return

    # Aynı bağlantıya ikinci bir cycle girmesin — cihazda tek oturum kalsın.
    if not _acquire_poll_lock(conn_id):
        logger.info("poll_connection: conn=%s hâlâ pollanıyor, bu tur atlandı", conn_id)
        return
    try:
        _poll_connection_body(conn_id)
    finally:
        _release_poll_lock(conn_id)


def _poll_connection_body(conn_id: int):
    """`poll_connection`'ın gövdesi — polling kilidi alınmış halde çalışır."""
    try:
        conn = Connection.objects.get(pk=conn_id)
    except Connection.DoesNotExist:
        logger.warning("poll_connection: Connection %s bulunamadı", conn_id)
        return

    now = timezone.now()
    Connection.objects.filter(pk=conn_id).update(last_polled_at=now)

    sensors = list(
        Sensor.objects.filter(connection=conn, is_active=True)
        .select_related("parameter", "scan_group")
    )
    if not sensors:
        return

    # Simülasyon sensörleri ayrı işle (cihaza dokunma yok)
    sim_sensors = [s for s in sensors if s.is_simulated]
    real_sensors = [s for s in sensors if not s.is_simulated]

    for sensor in sim_sensors:
        _record_simulated(sensor)

    if not real_sensors:
        return

    # --- Persistent connection pool ---
    # Aynı worker process'indeki önceki polling cycle'dan kalan açık socket'i
    # tekrar kullan; yoksa yeni aç. RUT906 vs. gateway'lerde rapid connect/close
    # pattern'i socket pool'unu kilitlediği için persistent connection kritik.
    try:
        reader = connection_pool.get_reader(conn)
    except ValueError as exc:
        # Desteklenmeyen protokol vb.
        Connection.objects.filter(pk=conn_id).update(
            last_error_at=now,
            last_error_message=str(exc)[:500],
        )
        _record_comm_error(conn_id, None, str(exc), None)
        logger.warning("poll_connection: %s — %s", conn, exc)
        return

    if reader is None:
        # Bağlantı açılamadı (pool içinde build + open başarısız)
        Connection.objects.filter(pk=conn_id).update(
            last_error_at=now,
            last_error_message="bağlantı açılamadı"[:500],
        )
        _record_comm_error(conn_id, None, "bağlantı açılamadı", 8)
        for sensor in real_sensors:
            persist_reading(sensor, value=None, quality="bad", origin="polled", status_code=8)
        return

    # --- Okuma: ölü sokette tek seferlik reconnect-retry ---
    # PLC boşta kalan oturumu kapattıysa ilk yazımda `BrokenPipeError` alırız ve
    # o cycle'ın TÜM sensörleri bad olur (SIM'e bozuk dakika gider). Ölü soket
    # tespit edilirse (kategori `conn_reset`) pool'u tazeleyip cycle'ı BİR kez
    # tekrarlarız → veri kaybı olmaz. Timeout'ta retry YAPILMAZ: taze soket
    # cevap vermeyen bir cihazı konuşturmaz, yalnız bloklamayı ikiye katlar.
    results: list = []
    last_sensor_error = ""
    connection_level_error = False

    for attempt in (1, 2):
        try:
            results, last_sensor_error, connection_level_error, socket_dead = _read_cycle(
                reader, conn, real_sensors
            )
        except Exception as exc:  # noqa: BLE001
            # Reader.read kendi içinde yakalar; buraya düşüyorsa beklenmedik bir hata
            logger.exception("poll_connection: beklenmedik exception conn=%s", conn_id)
            results = []
            last_sensor_error = f"{type(exc).__name__}: {exc}"
            connection_level_error = True
            socket_dead = False

        if not socket_dead or attempt == 2:
            break

        # Ölü soket → pool'u tazele ve cycle'ı bir kez daha dene.
        logger.info(
            "poll_connection: ölü soket, taze bağlantıyla yeniden deneniyor conn=%s (%s)",
            conn_id, last_sensor_error,
        )
        connection_pool.invalidate(conn_id, reason=f"dead socket: {last_sensor_error}")
        reader = connection_pool.get_reader(conn)
        if reader is None:
            break  # taze bağlantı da açılamadı → eldeki sonuçlarla devam

    # --- Sonuçları persist et ---
    any_success = False
    for sensor, result in results:
        if result.ok:
            any_success = True
            fail_status = None
        else:
            is_conn_err = _is_conn_level(result.error, include_timeout=True)
            # 8 = İletişim Hatası (comm/timeout); 4 = Geçersiz Veri
            # (decode/parse failure — bağlantı ok ama veri yorumlanamadı).
            fail_status = 8 if is_conn_err else 4
            # Teşhis kaydı (throttle'lı, davranışı değiştirmez).
            _record_comm_error(conn_id, sensor.id, result.error, fail_status)

        persist_reading(
            sensor,
            value=result.value,
            quality=result.quality,
            origin="polled",
            status_code=1 if result.ok else fail_status,
        )

    # --- Pool state yönetimi ---
    if connection_level_error:
        connection_pool.invalidate(conn_id, reason=last_sensor_error)
    elif any_success:
        connection_pool.record_success(conn_id)
        Connection.objects.filter(pk=conn_id).update(
            last_connected_at=timezone.now(),
            last_error_message="",
        )
    else:
        # Tüm sensörler fail (muhtemelen gateway veya cihaz timeout). Failure
        # sayacını artır; threshold'a ulaşırsa pool'u sıfırla.
        if connection_pool.record_failure(conn_id):
            connection_pool.invalidate(
                conn_id, reason=f"consecutive failures: {last_sensor_error}"
            )
        Connection.objects.filter(pk=conn_id).update(
            last_error_at=timezone.now(),
            last_error_message=last_sensor_error[:500],
        )

    # --- Tek oturumlu cihaz: turu bitirir bitirmez soketi kapat ---
    # Pool worker process'ine özel olduğundan prefork'ta N process = N açık
    # soket demektir; cihaz tek oturum kabul ediyorsa bu soketler birbirini
    # düşürür. Polling kilidi aynı anda tek CYCLE garantisi verir, bu bayrak
    # da cycle dışında hiç açık soket KALMAMASINI garanti eder → cihazda her
    # an en fazla bir oturum (Modbus Poll'un davranışı).
    if getattr(conn, "single_session", False):
        connection_pool.invalidate(conn_id, reason="single_session")


# Bir hata metnini "bağlantı seviyesi" sayan anahtar kelimeler. Timeout ayrı
# tutulur: scan-group fazında timeout bağlantıyı ölü saymaz (cihaz cevap
# vermiyor olabilir, soket sağlam), sensör fazında sayar — mevcut davranış.
_CONN_KEYWORDS = (
    "connection lost", "broken", "reset", "no route",
    "bağlantı yok", "socket", "disconnected",
)
_TIMEOUT_KEYWORDS = ("timeout", "no response")


def _is_conn_level(err: str | None, *, include_timeout: bool) -> bool:
    text = (err or "").lower()
    keywords = _CONN_KEYWORDS + (_TIMEOUT_KEYWORDS if include_timeout else ())
    return any(kw in text for kw in keywords)


def _read_cycle(reader, conn, real_sensors):
    """Scan group batch'lerini + sensörleri oku. **Persist YOK** (retry edilebilsin).

    Döner: ``(results, last_error, connection_level_error, socket_dead)``
      - ``results``: ``[(sensor, ReadResult), ...]``
      - ``socket_dead``: hatalardan biri `conn_reset` (broken pipe/reset) mi —
        yani soket ölü mü? Caller bunu görünce taze soketle bir kez retry eder.
    """
    # Aynı Connection üzerindeki aktif scan group'lar tek Modbus request ile
    # okunur; sonuç bellekte tutulup bu grubu kullanan sensörler decode edilir.
    group_data: dict[int | None, tuple[list[int] | None, str]] = {}
    last_error = ""
    conn_level = False
    socket_dead = False

    for sg in conn.scan_groups.filter(is_active=True):
        regs, err = reader.read_raw(
            slave_id=sg.slave_id,
            function=sg.function,
            address=sg.start_address,
            count=sg.quantity,
        )
        group_data[sg.pk] = (regs, err)
        if err:
            last_error = f"scan_group {sg.name}: {err}"
            if _is_conn_level(err, include_timeout=False):
                conn_level = True
            if classify_comm_error(err) == CONN_RESET:
                socket_dead = True
                break  # ölü sokete kalan grupları sormanın anlamı yok

    results = []
    for sensor in real_sensors:
        if socket_dead:
            # Soket öldü (reset/broken pipe). pymodbus kırık soketi KENDİ
            # kapatmaz (`client.socket` set kalır, `connect()` True döner) →
            # kalan sensörleri sormak her biri için aynı hatayı ve boşuna
            # bekleme üretir. Hepsini hatayla işaretleyip çıkıyoruz; caller
            # taze soketle cycle'ı bir kez tekrarlayacak (sonuçlar oradan gelir).
            results.append((sensor, ReadResult(
                quality="bad", error=last_error or "socket dead")))
            continue

        result = _read_sensor_with_groups(reader, sensor, group_data)
        results.append((sensor, result))
        if not result.ok:
            last_error = f"sensor {sensor.id}: {result.error or 'unknown read error'}"
            if _is_conn_level(result.error, include_timeout=True):
                conn_level = True
            if classify_comm_error(result.error) == CONN_RESET:
                socket_dead = True

    return results, last_error, conn_level, socket_dead


def _read_sensor_with_groups(reader, sensor, group_data):
    """Sensörü oku — scan_group varsa batch verisinden decode, yoksa per-sensor.

    `group_data`: dict[scan_group_id -> (regs | None, error)].
    Scan group'lu sensör için batch cache'inden offset hesaplayıp decode eder.
    Scan group'suz sensör için legacy `reader.read(sensor)` yoluna düşer.
    """
    if sensor.scan_group_id and sensor.scan_group_id in group_data:
        regs, err = group_data[sensor.scan_group_id]
        if err or regs is None:
            return ReadResult(quality="bad", error=err or "scan group read failed")
        try:
            value = decode_sensor_from_batch(
                regs,
                sensor.scan_group.start_address,
                sensor,
            )
            return ReadResult(value=value, quality="good")
        except ValueError as exc:
            return ReadResult(quality="bad", error=f"decode: {exc}")

    # Legacy path — scan_group kullanmayan sensörler
    return reader.read(sensor)


def _record_simulated(sensor):
    """is_simulated=True sensörler için rastgele değer üret + persist et.

    Aralık önceliği: sensörün kendi sim_min/sim_max alanları → parametrenin
    min_range/max_range → (0, 100) varsayılanı.
    """
    param = sensor.parameter
    if sensor.sim_min is not None:
        lo = sensor.sim_min
    elif param and param.min_range is not None:
        lo = param.min_range
    else:
        lo = 0.0
    if sensor.sim_max is not None:
        hi = sensor.sim_max
    elif param and param.max_range is not None:
        hi = param.max_range
    else:
        hi = 100.0
    if hi <= lo:
        hi = lo + 1.0
    value = random.uniform(lo, hi)
    persist_reading(sensor, value=value, quality="good", origin="simulated", status_code=1)


# --------------------------------------------------------------------------- #
# Command executor
# --------------------------------------------------------------------------- #

# Tek dispatch turunda en fazla bu kadar pending Command alınır (queue spike koruması).
COMMAND_BATCH_SIZE = 50


@shared_task(name="scada_io.tasks.dispatch_commands")
def dispatch_commands():
    """Pending Command'ları sırayla executor'a enqueue eder.

    İlk olarak `expires_at` geçmiş pending'leri filtreler (expire_commands
    paralel çalışıyor olsa da burada da güvenli filter). Sonra priority +
    created_at sırasına göre ilk N kayıt için atomik `pending → queued`
    güncelleme yapar ve `execute_command.delay(cmd_id)` çağırır.
    """
    # Lisans bitmişse komut çalıştırılmaz.
    from api.licensing import license_active
    if not license_active():
        return 0

    now = timezone.now()
    candidate_ids = list(
        Command.objects
        .filter(status="pending")
        .exclude(expires_at__lt=now)
        .order_by("priority", "created_at")
        .values_list("pk", flat=True)[:COMMAND_BATCH_SIZE]
    )

    enqueued = 0
    for cmd_id in candidate_ids:
        # Atomik geçiş — başka worker aynı anda almasın
        rows = Command.objects.filter(pk=cmd_id, status="pending").update(status="queued")
        if rows:
            execute_command.delay(cmd_id)
            enqueued += 1
    if enqueued:
        logger.info("dispatch_commands: %d command enqueued", enqueued)
    return enqueued


@shared_task(
    name="scada_io.tasks.execute_command",
    bind=True, max_retries=0, time_limit=60, soft_time_limit=50,
)
def execute_command(self, cmd_id: int):
    """Tek bir Command'ı yürüt: status state machine ile."""
    # Defansif: lisans bitmişse komut yürütülmez.
    from api.licensing import license_active
    if not license_active():
        return

    try:
        cmd = Command.objects.select_related("sensor", "sensor__connection").get(pk=cmd_id)
    except Command.DoesNotExist:
        logger.warning("execute_command: Command %s bulunamadı", cmd_id)
        return

    now = timezone.now()

    # Expired kontrolü (queued sonrası bile geçerli)
    if cmd.expires_at and cmd.expires_at < now:
        Command.objects.filter(pk=cmd_id).update(
            status="expired", completed_at=now,
            error_message="expires_at geçti (executor)",
        )
        return

    # executing'e geç + attempt sayacını artır
    Command.objects.filter(pk=cmd_id).update(
        status="executing",
        executed_at=now,
        attempt_count=cmd.attempt_count + 1,
    )
    cmd.refresh_from_db()

    sensor = cmd.sensor
    if sensor is None:
        _finalize_command(cmd, status="failed", error_message="sensor null")
        return

    try:
        writer = build_writer(sensor.connection)
    except ValueError as exc:
        _finalize_command(cmd, status="failed", error_message=str(exc))
        return

    if not writer.open():
        _retry_or_fail(cmd, error=writer.last_error or "writer açılamadı")
        return

    try:
        # value_text öncelik: 'string' tipinde value_text kullanılır
        if cmd.value_type == "string":
            payload_value = cmd.value_text
        else:
            payload_value = cmd.value

        result = writer.write(sensor, payload_value, cmd.value_type)

        if result.ok:
            _finalize_command(
                cmd, status="completed",
                response_data={"response": str(result.response)} if result.response else None,
            )
        else:
            _retry_or_fail(cmd, error=result.error or "write başarısız")
    except Exception as exc:  # noqa: BLE001
        logger.exception("execute_command sensor=%s exception", sensor.id)
        _retry_or_fail(cmd, error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            logger.exception("Writer close hatası")


def _log_command_event(cmd: Command, *, status: str, message: str = "") -> None:
    """Komut terminal durumunu (completed/failed/expired) olay kaydına yazar."""
    from api.events import EventType, log_event

    sensor = getattr(cmd, "sensor", None)
    station = None
    if sensor is not None and getattr(sensor, "connection_id", None):
        station = getattr(sensor.connection, "station", None)
    sensor_label = getattr(sensor, "name", None) or (f"sensör#{sensor.pk}" if sensor else "?")
    severity = "info" if status == "completed" else "warning"
    desc = f"Komut {status}: {sensor_label}"
    if message:
        desc += f" — {message}"
    log_event(
        EventType.COMMAND, desc, severity=severity,
        user=getattr(cmd, "requested_by", None), station=station,
    )


def _retry_or_fail(cmd: Command, error: str) -> None:
    """attempt_count < max_attempts ise pending'e geri al, değilse failed."""
    now = timezone.now()
    if cmd.attempt_count < cmd.max_attempts:
        Command.objects.filter(pk=cmd.pk).update(
            status="pending",
            error_message=error[:500],
        )
    else:
        Command.objects.filter(pk=cmd.pk).update(
            status="failed",
            completed_at=now,
            error_message=error[:500],
        )
        _log_command_event(cmd, status="failed", message=error[:200])


def _finalize_command(cmd: Command, *, status: str, error_message: str = "", response_data=None) -> None:
    now = timezone.now()
    Command.objects.filter(pk=cmd.pk).update(
        status=status,
        completed_at=now,
        error_message=(error_message or "")[:500],
        response_data=response_data,
    )
    # Terminal durumları olay kaydına yaz (retry-olmayan completed/failed).
    if status in ("completed", "failed"):
        _log_command_event(cmd, status=status, message=(error_message or "")[:200])


@shared_task(name="scada_io.tasks.expire_commands")
def expire_commands():
    """expires_at geçmiş pending/queued/executing kayıtları 'expired' yapar."""
    now = timezone.now()
    with transaction.atomic():
        affected = Command.objects.filter(
            status__in=["pending", "queued", "executing"],
            expires_at__lt=now,
        ).update(
            status="expired",
            completed_at=now,
            error_message="expires_at geçti",
        )
    if affected:
        logger.info("expire_commands: %d komut expired", affected)
    return affected
