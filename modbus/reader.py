import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

from api.models import Connection, Reading, Sensor, StatusCode

logger = logging.getLogger(__name__)


class ModbusReader:
    def __init__(self, max_workers=10):
        # TCP bağlantılar için thread pool
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def read_all_connections(self):
        """
        Bütün bağlantıları sırayla tara.
        TCP bağlantılar paralel okunur.
        Serial bağlantılar seri okunur.
        """
        connections = Connection.objects.filter(status=True)

        futures = []
        for con in connections:
            if con.con_type == "tcp":
                futures.append(self.executor.submit(self._read_tcp_connection, con))
            elif con.con_type == "serial":
                self._read_serial_connection(con)

        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logger.error(f"TCP bağlantı hatası: {e}")

    def _read_tcp_connection(self, connection):
        """ TCP bağlantısındaki sensörleri paralel oku """
        client = ModbusTcpClient(connection.con_address, port=connection.port)
        if not client.connect():
            logger.error(f"TCP bağlantısı başarısız: {connection.con_address}:{connection.port}")
            return

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
        """ Serial (RS485) bağlantısındaki sensörleri sırayla oku """
        client = ModbusSerialClient(
            method=connection.con_mode,
            port=connection.con_address,
            baudrate=connection.baudrate,
            parity=self._map_parity(connection.parity),
            stopbits=connection.stop_bits + 1,
            bytesize=connection.byte_size,
            timeout=2,
        )

        if not client.connect():
            logger.error(f"Serial bağlantısı başarısız: {connection.con_address}")
            return

        sensors = Sensor.objects.filter(connection=connection, is_active=True)
        for sensor in sensors:
            try:
                self._read_sensor(client, sensor)
            except Exception as e:
                logger.error(f"Serial sensör okuma hatası ({sensor.id}): {e}")

        client.close()

    def _read_sensor(self, client, sensor):
        """ Tek sensör okuma işlemi """
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

            value = rr.registers[0] if hasattr(rr, "registers") else rr.bits[0]
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
    def _map_parity(parity_code):
        mapping = {0: "N", 1: "O", 2: "E"}
        return mapping.get(parity_code, "N")
