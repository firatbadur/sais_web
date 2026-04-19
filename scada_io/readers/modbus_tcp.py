"""Modbus TCP reader (pymodbus 3.x — slave= API)."""
from __future__ import annotations

import logging
from typing import Any

from ..decoders import decode_registers
from .base import ProtocolReader, ReadResult


logger = logging.getLogger(__name__)


class ModbusTcpReader(ProtocolReader):
    """Modbus TCP üzerinden tek connection için okuma."""

    def __init__(self, connection):
        super().__init__(connection)
        self._client = None

    def open(self) -> bool:
        from pymodbus.client import ModbusTcpClient

        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)
        self._client = ModbusTcpClient(
            host=self.connection.host,
            port=self.connection.port or 502,
            timeout=timeout_sec,
        )
        try:
            self.connected = bool(self._client.connect())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Modbus TCP open hatası %s:%s — %s",
                           self.connection.host, self.connection.port, self.last_error)
            self.connected = False
            return False

        if not self.connected:
            self.last_error = f"TCP bağlantısı kurulamadı: {self.connection.host}:{self.connection.port}"
        return self.connected

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.exception("Modbus TCP close hatası")
        self._client = None
        self.connected = False

    def read(self, sensor) -> ReadResult:
        if not self.connected or self._client is None:
            return ReadResult(quality="bad", error="bağlantı yok")

        try:
            return _modbus_read(self._client, sensor)
        except Exception as exc:  # noqa: BLE001
            return ReadResult(quality="bad", error=f"{type(exc).__name__}: {exc}")


def _modbus_read(client, sensor) -> ReadResult:
    """Modbus TCP/RTU/ASCII için ortak okuma yardımcısı.

    `Sensor.function`'a göre register tipini seçer; `Sensor.data_type`'a göre
    decoder'ı uygular; `Sensor.scale`/`offset` mühendislik dönüşümü yapar.
    """
    fn = sensor.function or 3
    addr = sensor.address or 0
    qty = sensor.quantity or 2
    slave = sensor.slave_id or 1

    if fn == 3:
        rr = client.read_holding_registers(address=addr, count=qty, slave=slave)
    elif fn == 4:
        rr = client.read_input_registers(address=addr, count=qty, slave=slave)
    elif fn == 2:
        rr = client.read_discrete_inputs(address=addr, count=qty, slave=slave)
    elif fn == 1:
        rr = client.read_coils(address=addr, count=qty, slave=slave)
    else:
        return ReadResult(quality="bad", error=f"Desteklenmeyen function code: {fn}")

    if rr is None or (hasattr(rr, "isError") and rr.isError()):
        return ReadResult(quality="bad", error=f"Modbus hata: {rr}")

    raw_words: list[int]
    if hasattr(rr, "registers"):
        raw_words = list(rr.registers)
    elif hasattr(rr, "bits"):
        raw_words = [int(b) for b in rr.bits[:qty]]
    else:
        return ReadResult(quality="bad", error=f"Anlaşılmaz response: {rr}")

    decoded = decode_registers(
        raw_words,
        data_type=sensor.data_type or "uint16",
        byte_order=sensor.byte_order or "big",
        word_order=sensor.word_order or "big",
        bit_position=sensor.bit_position,
    )

    # Mühendislik dönüşümü — sayısal tipler için scale/offset uygula.
    if isinstance(decoded, (int, float)) and not isinstance(decoded, bool):
        scale = sensor.scale if sensor.scale is not None else 1.0
        offset = sensor.offset if sensor.offset is not None else 0.0
        value: Any = decoded * scale + offset
        # Dijital ters
        if sensor.digital_inverse and sensor.data_type in ("bool", "bit"):
            value = not bool(value)
    else:
        value = decoded

    return ReadResult(value=value, quality="good", raw=raw_words)
