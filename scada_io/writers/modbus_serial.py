"""Modbus RTU + ASCII (serial) writer."""
from __future__ import annotations

import logging
from typing import Any

from .base import ProtocolWriter, WriteResult
from .modbus_tcp import _modbus_write


logger = logging.getLogger(__name__)


_PARITY_MAP = {0: "N", 1: "O", 2: "E"}


class ModbusSerialWriter(ProtocolWriter):
    def __init__(self, connection):
        super().__init__(connection)
        self._client = None

    def open(self) -> bool:
        from pymodbus.client import ModbusSerialClient
        from pymodbus.framer import FramerType

        framer = (
            FramerType.RTU
            if self.connection.protocol == "modbus_rtu"
            else FramerType.ASCII
        )
        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)
        _rc = self.connection.retry_count

        self._client = ModbusSerialClient(
            port=self.connection.serial_port,
            framer=framer,
            baudrate=self.connection.baudrate or 9600,
            parity=_PARITY_MAP.get(self.connection.parity or 0, "N"),
            stopbits=self.connection.stop_bits or 1,
            bytesize=self.connection.byte_size or 8,
            timeout=timeout_sec,
            retries=3 if _rc is None else max(0, _rc),
        )

        try:
            self.connected = bool(self._client.connect())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
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
                logger.exception("Modbus serial writer close hatası")
        self._client = None
        self.connected = False

    def write(self, sensor, value: Any, value_type: str) -> WriteResult:
        if not self.connected or self._client is None:
            return WriteResult(success=False, error="bağlantı yok")
        try:
            return _modbus_write(self._client, sensor, value, value_type)
        except Exception as exc:  # noqa: BLE001
            return WriteResult(success=False, error=f"{type(exc).__name__}: {exc}")
