import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

from api.models import Connection, Reading, Sensor, SensorLatest, StatusCode

logger = logging.getLogger(__name__)


# Hangi Connection.protocol değerleri bu reader tarafından desteklenir.
SUPPORTED_TCP_PROTOCOLS = {"modbus_tcp"}
SUPPORTED_SERIAL_PROTOCOLS = {"modbus_rtu", "modbus_ascii"}


class ModbusReader:
    def __init__(self, max_workers=10):
        # TCP bağlantılar için thread pool
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def read_all_connections(self):
        """
        Aktif Modbus bağlantılarını tara.
        TCP bağlantılar paralel, serial bağlantılar sırayla okunur.
        """
        connections = Connection.objects.filter(is_enabled=True).filter(
            protocol__in=list(SUPPORTED_TCP_PROTOCOLS | SUPPORTED_SERIAL_PROTOCOLS)
        )

        futures = []
        for con in connections:
            if con.protocol in SUPPORTED_TCP_PROTOCOLS:
                futures.append(self.executor.submit(self._read_tcp_connection, con))
            elif con.protocol in SUPPORTED_SERIAL_PROTOCOLS:
                self._read_serial_connection(con)

        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logger.error(f"TCP bağlantı hatası: {e}")

    def _read_tcp_connection(self, connection):
        """ Modbus TCP bağlantısındaki sensörleri paralel oku. """
        timeout_sec = max(0.1, connection.timeout_ms / 1000.0)
        client = ModbusTcpClient(connection.host, port=connection.port, timeout=timeout_sec)
        if not client.connect():
            self._mark_error(connection, f"TCP bağlantısı başarısız: {connection.host}:{connection.port}")
            return

        self._mark_connected(connection)

        sensors = Sensor.objects.filter(connection=connection, is_active=True)

        futures = []
        with ThreadPoolExecutor(max_workers=5) as sensor_pool:
            for sensor in sensors:
                futures.append(sensor_pool.submit(self._read_sensor, client, sensor))

            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    logger.error(f"TCP sensör okuma hatası: {e}")

        client.close()

    def _read_serial_connection(self, connection):
        """ Modbus RTU/ASCII (RS-232/485) bağlantısındaki sensörleri sırayla oku. """
        mode = "rtu" if connection.protocol == "modbus_rtu" else "ascii"
        timeout_sec = max(0.1, connection.timeout_ms / 1000.0)

        client = ModbusSerialClient(
            method=mode,
            port=connection.serial_port,
            baudrate=connection.baudrate,
            parity=self._map_parity(connection.parity),
            stopbits=connection.stop_bits,
            bytesize=connection.byte_size,
            timeout=timeout_sec,
        )

        if not client.connect():
            self._mark_error(connection, f"Serial bağlantısı başarısız: {connection.serial_port}")
            return

        self._mark_connected(connection)

        sensors = Sensor.objects.filter(connection=connection, is_active=True)
        for sensor in sensors:
            try:
                self._read_sensor(client, sensor)
            except Exception as e:
                logger.error(f"Serial sensör okuma hatası ({sensor.id}): {e}")

        client.close()

    def _read_sensor(self, client, sensor):
        """ Tek sensör okuma işlemi. """
        # Simülasyon modu — gerçek cihaza dokunma, rastgele değer üret.
        if sensor.is_simulated:
            self._record_simulated(sensor)
            return

        try:
            if sensor.function == 3:
                rr = client.read_holding_registers(sensor.address, sensor.quantity, unit=sensor.slave_id)
            elif sensor.function == 4:
                rr = client.read_input_registers(sensor.address, sensor.quantity, unit=sensor.slave_id)
            elif sensor.function == 2:
                rr = client.read_discrete_inputs(sensor.address, sensor.quantity, unit=sensor.slave_id)
            elif sensor.function == 1:
                rr = client.read_coils(sensor.address, sensor.quantity, unit=sensor.slave_id)
            else:
                logger.warning(f"Desteklenmeyen fonksiyon kodu: {sensor.function}")
                return

            if rr.isError():
                logger.error(f"Slave {sensor.slave_id} hata: {rr}")
                self._persist_reading(sensor, value=None, status_code=8, quality="bad", origin="polled")
                return

            raw = rr.registers[0] if hasattr(rr, "registers") else rr.bits[0]
            value = raw * (sensor.scale or 1.0) + (sensor.offset or 0.0)
            self._persist_reading(sensor, value=value, status_code=1, quality="good", origin="polled")
            logger.info(f"Sensor {sensor.id} okundu: {value}")

        except Exception as e:
            logger.error(f"Sensor {sensor.id} okuma hatası: {e}")
            self._persist_reading(sensor, value=None, status_code=8, quality="bad", origin="polled")

    def _record_simulated(self, sensor):
        """is_simulated=True sensörler için rastgele değer üret + kaydet."""
        param = sensor.parameter
        lo = (param.min_range if param and param.min_range is not None else 0.0)
        hi = (param.max_range if param and param.max_range is not None else 100.0)
        if hi <= lo:
            hi = lo + 1.0
        value = random.uniform(lo, hi)
        self._persist_reading(sensor, value=value, status_code=1, quality="good", origin="simulated")

    @staticmethod
    def _persist_reading(sensor, *, value, status_code, quality, origin):
        """Reading satırı yarat ve SensorLatest snapshot'ını upsert et."""
        now = timezone.now()
        status = StatusCode.objects.filter(code=status_code).first()

        Reading.objects.create(
            sensor=sensor,
            value=value,
            status=status,
            quality=quality,
            origin=origin,
            time_iso=now,
        )

        # Snapshot upsert: değer değiştiyse last_change_at güncellensin.
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

    @staticmethod
    def _mark_connected(connection):
        Connection.objects.filter(pk=connection.pk).update(
            last_connected_at=timezone.now(),
            last_error_message="",
        )

    @staticmethod
    def _mark_error(connection, message):
        logger.error(message)
        Connection.objects.filter(pk=connection.pk).update(
            last_error_at=timezone.now(),
            last_error_message=message[:500],
        )

    @staticmethod
    def _map_parity(parity_code):
        mapping = {0: "N", 1: "O", 2: "E"}
        return mapping.get(parity_code, "N")
