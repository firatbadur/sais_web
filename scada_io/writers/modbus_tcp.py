"""Modbus TCP writer (pymodbus 3.x).

Reader ile aynı protokol varyantlarını destekler:
modbus_tcp / modbus_rtu_over_tcp / modbus_ascii_over_tcp.
"""
from __future__ import annotations

import logging
from typing import Any

from ..decoders import REGISTER_COUNT, encode_value
from ..readers.modbus_tcp import _framer_for_protocol
from .base import ProtocolWriter, WriteResult


logger = logging.getLogger(__name__)


class ModbusTcpWriter(ProtocolWriter):
    def __init__(self, connection):
        super().__init__(connection)
        self._client = None

    def open(self) -> bool:
        from pymodbus.client import ModbusTcpClient

        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)
        kwargs = dict(
            host=self.connection.host,
            port=self.connection.port or 502,
            timeout=timeout_sec,
        )
        framer = _framer_for_protocol(self.connection.protocol)
        if framer is not None:
            kwargs["framer"] = framer

        self._client = ModbusTcpClient(**kwargs)
        try:
            self.connected = bool(self._client.connect())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
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
                logger.exception("Modbus TCP writer close hatası")
        self._client = None
        self.connected = False

    def write(self, sensor, value: Any, value_type: str) -> WriteResult:
        if not self.connected or self._client is None:
            return WriteResult(success=False, error="bağlantı yok")

        try:
            return _modbus_write(self._client, sensor, value, value_type)
        except Exception as exc:  # noqa: BLE001
            return WriteResult(success=False, error=f"{type(exc).__name__}: {exc}")


def _modbus_write(client, sensor, value: Any, value_type: str) -> WriteResult:
    """Modbus TCP/RTU/ASCII için ortak yazma yardımcısı.

    Yazma stratejisi:
      - bool/coil → write_coil veya write_coils
      - bit (register içinde) → read_holding_register, bit'i değiştir, write_register
      - int/float (1 register) → write_register
      - int/float (2-4 register) → write_registers
      - string/raw → write_registers (encoded bytes)
    """
    addr = sensor.address or 0
    slave = sensor.slave_id or 1
    fn = sensor.function or 6  # write fonksiyonu
    data_type = (sensor.data_type or value_type or "uint16").lower()
    byte_order = sensor.byte_order or "big"
    word_order = sensor.word_order or "big"

    # Boolean (dijital output Start/Stop) yazımı — sensörün data_type'ı ne
    # olursa olsun sağlam çalışsın; yapılandırma hatası exception'a dönmesin
    # ("hataya düşmeyecek" gereksinimi). Hangi Modbus fonksiyonunun kullanılacağı
    # data_type'tan türetilir (sensor.function bir okuma kodu olsa bile önemsiz):
    #   - bool        → write_coil (fn5)
    #   - bit         → read_holding + bit değiştir + write_register
    #   - sayısal reg → write_register ile 0/1
    if value_type == "bool":
        bval = 1 if bool(value) else 0
        if data_type == "bit":
            rr = client.read_holding_registers(address=addr, count=1, device_id=slave)
            if rr is None or (hasattr(rr, "isError") and rr.isError()):
                return WriteResult(success=False, error=f"bit-write için read_holding hata: {rr}")
            current = rr.registers[0]
            regs = encode_value(bval, "bit", byte_order=byte_order, word_order=word_order,
                                bit_position=sensor.bit_position, current_register=current)
            wr = client.write_register(address=addr, value=regs[0], device_id=slave)
            if wr is None or (hasattr(wr, "isError") and wr.isError()):
                return WriteResult(success=False, error=f"write_register hata: {wr}")
            return WriteResult(success=True, response=str(wr))
        if data_type in ("int16", "uint16", "int32", "uint32", "int64", "uint64",
                         "float32", "float64", "raw"):
            # Register tabanlı dijital output → 0/1 değerini register'a yaz.
            wr = client.write_register(address=addr, value=bval, device_id=slave)
            if wr is None or (hasattr(wr, "isError") and wr.isError()):
                return WriteResult(success=False, error=f"write_register hata: {wr}")
            return WriteResult(success=True, response=str(wr))
        # Varsayılan: coil (data_type="bool" veya bilinmeyen)
        rr = client.write_coil(address=addr, value=bool(bval), device_id=slave)
        if rr is None or (hasattr(rr, "isError") and rr.isError()):
            return WriteResult(success=False, error=f"write_coil hata: {rr}")
        return WriteResult(success=True, response=str(rr))

    # Bit-in-register: önce mevcut register'ı oku, ilgili bit'i değiştir, geri yaz
    if data_type == "bit":
        rr = client.read_holding_registers(address=addr, count=1, device_id=slave)
        if rr is None or (hasattr(rr, "isError") and rr.isError()):
            return WriteResult(success=False, error=f"bit-write için read_holding hata: {rr}")
        current = rr.registers[0]
        regs = encode_value(value, "bit", byte_order=byte_order, word_order=word_order,
                            bit_position=sensor.bit_position, current_register=current)
        wr = client.write_register(address=addr, value=regs[0], device_id=slave)
        if wr is None or (hasattr(wr, "isError") and wr.isError()):
            return WriteResult(success=False, error=f"write_register hata: {wr}")
        return WriteResult(success=True, response=str(wr))

    # Sayısal/string/raw: encode → tek veya çoklu register
    # Mühendislik dönüşümü tersi (eng → raw): raw = (value - offset) / scale
    if value_type in ("int", "float") and isinstance(value, (int, float)):
        scale = sensor.scale if sensor.scale is not None else 1.0
        offset = sensor.offset if sensor.offset is not None else 0.0
        raw_value = (value - offset) / scale if scale else value
        # Tip korumayı uygula: int data_type için round → int
        if data_type in ("int16", "uint16", "int32", "uint32", "int64", "uint64"):
            raw_value = int(round(raw_value))
        encoded = encode_value(raw_value, data_type, byte_order=byte_order, word_order=word_order)
    elif value_type == "string":
        encoded = encode_value(str(value), "string", byte_order=byte_order, word_order=word_order)
    else:
        encoded = encode_value(value, data_type, byte_order=byte_order, word_order=word_order)

    if len(encoded) == 1:
        wr = client.write_register(address=addr, value=encoded[0], device_id=slave)
    else:
        wr = client.write_registers(address=addr, values=encoded, device_id=slave)

    if wr is None or (hasattr(wr, "isError") and wr.isError()):
        return WriteResult(success=False, error=f"Modbus write hata: {wr}")
    return WriteResult(success=True, response=str(wr))
