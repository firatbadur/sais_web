"""Modbus TCP reader (pymodbus 3.11+ — device_id= API).

Protokol varyantları aynı sınıfta:
  - modbus_tcp           → MBAP framer (default, pymodbus auto-select)
  - modbus_rtu_over_tcp  → RTU framer TCP socket üzerinde (gateway transparent)
  - modbus_ascii_over_tcp → ASCII framer TCP socket üzerinde

Not: pymodbus 3.13+ `slave=` parametresini `device_id=` olarak yeniden
adlandırdı — reader/writer helper'ları bu yeni isimlendirmeyi kullanır.
"""
from __future__ import annotations

import logging
from typing import Any

from ..decoders import decode_registers
from .base import ProtocolReader, ReadResult


logger = logging.getLogger(__name__)


def _framer_for_protocol(protocol: str):
    """Connection.protocol → pymodbus FramerType (None = default MBAP)."""
    from pymodbus.framer import FramerType

    return {
        "modbus_tcp": None,
        "modbus_rtu_over_tcp": FramerType.RTU,
        "modbus_ascii_over_tcp": FramerType.ASCII,
    }.get(protocol)


class ModbusTcpReader(ProtocolReader):
    """Modbus TCP üzerinden tek connection için okuma."""

    def __init__(self, connection):
        super().__init__(connection)
        self._client = None

    def open(self) -> bool:
        from pymodbus.client import ModbusTcpClient

        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)
        _rc = self.connection.retry_count
        kwargs = dict(
            host=self.connection.host,
            port=self.connection.port or 502,
            timeout=timeout_sec,
            # Connection.retry_count → pymodbus ek deneme sayısı (cevapsız okumada).
            # Toplam deneme = 1 + retries; her deneme timeout kadar bekler.
            retries=3 if _rc is None else max(0, _rc),
        )
        framer = _framer_for_protocol(self.connection.protocol)
        if framer is not None:
            kwargs["framer"] = framer

        self._client = ModbusTcpClient(**kwargs)
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

    def read_raw(self, *, slave_id, function, address, count):
        if not self.connected or self._client is None:
            return None, "bağlantı yok"
        return _modbus_read_raw(self._client, slave_id, function, address, count)


def _modbus_read_raw(client, slave_id: int, function: int,
                     address: int, count: int) -> tuple[list[int] | None, str]:
    """pymodbus client üzerinden ham register/bit listesi oku.

    Scan group batch read için ortak helper. Dönen değer:
      (regs, error)  →  regs=list[int], error="" (başarı)
      (None, error)  →  başarısızlık sebebi
    """
    try:
        if function == 3:
            rr = client.read_holding_registers(address=address, count=count, device_id=slave_id)
        elif function == 4:
            rr = client.read_input_registers(address=address, count=count, device_id=slave_id)
        elif function == 2:
            rr = client.read_discrete_inputs(address=address, count=count, device_id=slave_id)
        elif function == 1:
            rr = client.read_coils(address=address, count=count, device_id=slave_id)
        else:
            return None, f"Desteklenmeyen function code: {function}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    if rr is None or (hasattr(rr, "isError") and rr.isError()):
        return None, f"Modbus hata: {rr}"

    # pymodbus 3.x'te ReadDiscreteInputsResponse hem `bits` hem `registers`
    # attribute'larına sahip — discrete input yanıtında `registers=[]` boş ama
    # `hasattr(rr, "registers")` True döner. Bit/word seçimini function code
    # üzerinden yapmak zorundayız.
    if function in (1, 2):  # Read Coils / Read Discrete Inputs → bit
        if hasattr(rr, "bits"):
            return [int(b) for b in rr.bits[:count]], ""
    elif function in (3, 4):  # Read Holding/Input Registers → word
        if hasattr(rr, "registers"):
            return list(rr.registers), ""
    return None, f"Anlaşılmaz response: {rr}"


def _modbus_read(client, sensor) -> ReadResult:
    """Tek sensör için Modbus okuma — ham okur + decode + engineering dönüşümü.

    Scan group kullanmayan legacy mod için. `_modbus_read_raw` üzerine kurulu.
    """
    fn = sensor.function or 3
    addr = sensor.address or 0
    qty = sensor.quantity or 2
    slave = sensor.slave_id or 1

    raw_words, err = _modbus_read_raw(client, slave, fn, addr, qty)
    if err or raw_words is None:
        return ReadResult(quality="bad", error=err or "raw read failed")

    decoded = decode_registers(
        raw_words,
        data_type=sensor.data_type or "uint16",
        byte_order=sensor.byte_order or "big",
        word_order=sensor.word_order or "big",
        bit_position=sensor.bit_position,
    )

    value: Any
    if isinstance(decoded, (int, float)) and not isinstance(decoded, bool):
        scale = sensor.scale if sensor.scale is not None else 1.0
        offset = sensor.offset if sensor.offset is not None else 0.0
        value = decoded * scale + offset
        if sensor.digital_inverse and sensor.data_type in ("bool", "bit"):
            value = not bool(value)
    else:
        value = decoded

    return ReadResult(value=value, quality="good", raw=raw_words)
