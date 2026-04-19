"""Özel ASCII writer — cihaza ham ASCII komut gönderir.

Kullanım: bir Command'ın `value_text` alanı doğrudan cihaza gönderilecek
komut olarak kullanılır. Reader'daki `ascii_request`/`ascii_response_regex`
mantığı yazma için yoktur — caller komutun tam içeriğini hazırlamış olmalıdır.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import ProtocolWriter, WriteResult


logger = logging.getLogger(__name__)


_PARITY_MAP = {0: "N", 1: "O", 2: "E"}


class AsciiCustomWriter(ProtocolWriter):
    def __init__(self, connection):
        super().__init__(connection)
        self._serial = None

    def open(self) -> bool:
        try:
            import serial
        except ImportError:
            self.last_error = "pyserial kurulu değil"
            return False

        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)
        try:
            self._serial = serial.Serial(
                port=self.connection.serial_port,
                baudrate=self.connection.baudrate or 9600,
                parity=_PARITY_MAP.get(self.connection.parity or 0, "N"),
                stopbits=self.connection.stop_bits or 1,
                bytesize=self.connection.byte_size or 8,
                timeout=timeout_sec,
                xonxoff=bool(self.connection.xonxoff),
                rtscts=bool(self.connection.rtscts),
                dsrdtr=bool(self.connection.dsrdtr),
            )
            self.connected = self._serial.is_open
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.connected = False
        return self.connected

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # noqa: BLE001
                logger.exception("ASCII custom writer close hatası")
        self._serial = None
        self.connected = False

    def write(self, sensor, value: Any, value_type: str) -> WriteResult:
        if not self.connected or self._serial is None:
            return WriteResult(success=False, error="bağlantı yok")

        terminator = sensor.ascii_line_terminator or "\r\n"
        # value_text öncelikli; numeric değerse string'e dönüştür
        if value_type == "string":
            cmd = str(value)
        else:
            cmd = str(value)

        try:
            payload = (cmd + terminator).encode("ascii", errors="replace")
            self._serial.reset_output_buffer()
            written = self._serial.write(payload)
            self._serial.flush()
        except Exception as exc:  # noqa: BLE001
            return WriteResult(success=False, error=f"{type(exc).__name__}: {exc}")

        # Opsiyonel: cihaz yanıtı oku (timeout süresince)
        try:
            response = self._serial.read_until(terminator.encode("ascii"))
            response_text = response.decode("ascii", errors="replace").strip() if response else ""
        except Exception:  # noqa: BLE001
            response_text = ""

        return WriteResult(success=bool(written), response=response_text)
