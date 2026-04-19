"""Modbus RTU + ASCII (serial) reader."""
from __future__ import annotations

import logging

from .base import ProtocolReader, ReadResult
from .modbus_tcp import _modbus_read


logger = logging.getLogger(__name__)


_PARITY_MAP = {0: "N", 1: "O", 2: "E"}


class ModbusSerialReader(ProtocolReader):
    """Modbus RTU veya Modbus ASCII üzerinden tek serial bus için okuma.

    Hangi mode (RTU vs ASCII) `Connection.protocol` üzerinden seçilir.
    Read mantığı `modbus_tcp._modbus_read` ile paylaşılır.
    """

    def __init__(self, connection):
        super().__init__(connection)
        self._client = None

    def open(self) -> bool:
        from pymodbus.client import ModbusSerialClient

        # pymodbus 3.x: ModbusSerialClient mode'u sınıf seviyesinde belirler;
        # framer parametresiyle override edilebilir.
        from pymodbus.framer import FramerType

        framer = (
            FramerType.RTU
            if self.connection.protocol == "modbus_rtu"
            else FramerType.ASCII
        )
        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)

        self._client = ModbusSerialClient(
            port=self.connection.serial_port,
            framer=framer,
            baudrate=self.connection.baudrate or 9600,
            parity=_PARITY_MAP.get(self.connection.parity or 0, "N"),
            stopbits=self.connection.stop_bits or 1,
            bytesize=self.connection.byte_size or 8,
            timeout=timeout_sec,
        )

        try:
            self.connected = bool(self._client.connect())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Modbus serial open hatası %s — %s",
                           self.connection.serial_port, self.last_error)
            self.connected = False
            return False

        if not self.connected:
            self.last_error = f"Serial açılamadı: {self.connection.serial_port}"
        return self.connected

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.exception("Modbus serial close hatası")
        self._client = None
        self.connected = False

    def read(self, sensor) -> ReadResult:
        if not self.connected or self._client is None:
            return ReadResult(quality="bad", error="bağlantı yok")
        try:
            return _modbus_read(self._client, sensor)
        except Exception as exc:  # noqa: BLE001
            return ReadResult(quality="bad", error=f"{type(exc).__name__}: {exc}")
