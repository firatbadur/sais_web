import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

from api.models import Connection, Reading, Sensor, StatusCode

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
                return

            raw = rr.registers[0] if hasattr(rr, "registers") else rr.bits[0]
            value = raw * (sensor.scale or 1.0) + (sensor.offset or 0.0)
            status = StatusCode.objects.filter(code=1).first()  # 1 = Veri Geçerli

            Reading.objects.create(
                sensor=sensor,
                value=value,
                status=status,
                time_iso=timezone.now(),
            )

            logger.info(f"Sensor {sensor.id} okundu: {value}")

        except Exception as e:
            logger.error(f"Sensor {sensor.id} okuma hatası: {e}")

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
