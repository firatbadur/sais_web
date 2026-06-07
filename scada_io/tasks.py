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
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from api.models import Command, Connection, Sensor

from . import connection_pool
from .decoders import decode_sensor_from_batch
from .persistence import persist_reading
from .readers import ReadResult, build_reader
from .writers import build_writer


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Polling
# --------------------------------------------------------------------------- #

@shared_task(name="scada_io.tasks.dispatch_polls")
def dispatch_polls():
    """Beat tarafından her 5 sn'de tetiklenir. Due olan bağlantıları enqueue eder.

    Bir bağlantı 'due' kabul edilir:
      last_polled_at == None  (hiç polling yapılmamış), VEYA
      now - last_polled_at >= poll_interval_sec

    Global `SystemSwitch.polling_enabled` kapalıysa hiçbir bağlantı enqueue
    edilmez (yönetici dashboard'dan kapatılabilir).
    """
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
            poll_connection.delay(conn.pk)
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
        logger.warning("poll_connection: %s — %s", conn, exc)
        return

    if reader is None:
        # Bağlantı açılamadı (pool içinde build + open başarısız)
        Connection.objects.filter(pk=conn_id).update(
            last_error_at=now,
            last_error_message="bağlantı açılamadı"[:500],
        )
        for sensor in real_sensors:
            persist_reading(sensor, value=None, quality="bad", origin="polled", status_code=8)
        return

    last_sensor_error = ""
    any_success = False
    connection_level_error = False

    # --- Adım 1: Scan group batch okumaları ---
    # Aynı Connection üzerindeki aktif scan group'lar tek Modbus request ile
    # okunur; sonuç bellekte tutulup bu grubu kullanan sensörler decode edilir.
    # `None` anahtarı scan_group'suz (legacy) sensörler için kullanılır.
    group_data: dict[int | None, tuple[list[int] | None, str]] = {}
    for sg in conn.scan_groups.filter(is_active=True):
        regs, err = reader.read_raw(
            slave_id=sg.slave_id,
            function=sg.function,
            address=sg.start_address,
            count=sg.quantity,
        )
        group_data[sg.pk] = (regs, err)
        if err:
            last_sensor_error = f"scan_group {sg.name}: {err}"
            err_lower = err.lower()
            if any(kw in err_lower for kw in (
                "connection lost", "broken", "reset", "no route",
                "bağlantı yok", "socket", "disconnected",
            )):
                connection_level_error = True

    # --- Adım 2: Sensörleri decode et + persist ---
    try:
        for sensor in real_sensors:
            result = _read_sensor_with_groups(reader, sensor, group_data)

            if result.ok:
                any_success = True
                fail_status = None
            else:
                last_sensor_error = f"sensor {sensor.id}: {result.error or 'unknown read error'}"
                err_lower = (result.error or "").lower()
                is_conn_err = any(kw in err_lower for kw in (
                    "connection lost", "broken", "reset", "no route",
                    "bağlantı yok", "socket", "disconnected",
                    "timeout", "no response",
                ))
                if is_conn_err:
                    connection_level_error = True
                # 8 = İletişim Hatası (comm/timeout); 4 = Geçersiz Veri
                # (decode/parse failure — bağlantı ok ama veri yorumlanamadı).
                fail_status = 8 if is_conn_err else 4

            persist_reading(
                sensor,
                value=result.value,
                quality=result.quality,
                origin="polled",
                status_code=1 if result.ok else fail_status,
            )
    except Exception as exc:  # noqa: BLE001
        # Reader.read kendi içinde yakalar; buraya düşüyorsa beklenmedik bir hata
        logger.exception("poll_connection: beklenmedik exception conn=%s", conn_id)
        last_sensor_error = f"{type(exc).__name__}: {exc}"
        connection_level_error = True

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
    """is_simulated=True sensörler için rastgele değer üret + persist et."""
    param = sensor.parameter
    lo = param.min_range if (param and param.min_range is not None) else 0.0
    hi = param.max_range if (param and param.max_range is not None) else 100.0
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


def _finalize_command(cmd: Command, *, status: str, error_message: str = "", response_data=None) -> None:
    now = timezone.now()
    Command.objects.filter(pk=cmd.pk).update(
        status=status,
        completed_at=now,
        error_message=(error_message or "")[:500],
        response_data=response_data,
    )


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
