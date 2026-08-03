"""Özel ASCII (request-response) protokolü reader.

Cihaza `Sensor.ascii_request` komutunu gönderir, satır sonu
(`Sensor.ascii_line_terminator`) ile sonlanan yanıtı bekler ve
`Sensor.ascii_response_regex` ile değer çıkarır. Çıkarılan değer
`Sensor.scale * x + Sensor.offset` ile mühendislik birime çevrilir.

Genelde tek-cihazlı bus'lar için (ADAM-4017, NMEA, basit pH probe vb.).
Multi-drop ASCII'de `Sensor.ascii_code` cihaz adresi olarak request'in
parçası olabilir; bu plan'da template substitution yapılmaz, caller
istek string'inde adresi yazmış olmalıdır (örn. "#01\\r" gibi).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .base import ProtocolReader, ReadResult


logger = logging.getLogger(__name__)


_PARITY_MAP = {0: "N", 1: "O", 2: "E"}


class AsciiCustomReader(ProtocolReader):
    def __init__(self, connection):
        super().__init__(connection)
        self._serial = None

    def open(self) -> bool:
        try:
            import serial  # pyserial
        except ImportError:
            self.last_error = "pyserial kurulu değil"
            return False

        timeout_sec = max(0.1, (self.connection.timeout_ms or 2000) / 1000.0)
        try:
            if self.connection.transport == "tcp":
                # Köprülenmiş seri (bridge_redirect) VEYA gerçek ascii-over-TCP
                # (terminal server). pyserial socket handler'ı read_until /
                # reset_input_buffer dahil aynı API'yi sunar; UART parametreleri
                # karşı uçta (köprü/terminal server) uygulanır.
                url = f"socket://{self.connection.host}:{self.connection.port}"
                self._serial = serial.serial_for_url(url, timeout=timeout_sec)
            else:
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
            logger.warning("ASCII custom open hatası %s — %s",
                           self.connection.serial_port or self.connection.host,
                           self.last_error)
            self.connected = False
        return self.connected

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # noqa: BLE001
                logger.exception("ASCII custom close hatası")
        self._serial = None
        self.connected = False

    def read(self, sensor) -> ReadResult:
        if not self.connected or self._serial is None:
            return ReadResult(quality="bad", error="bağlantı yok")

        request = sensor.ascii_request or ""
        terminator = sensor.ascii_line_terminator or "\r\n"
        if not request:
            return ReadResult(quality="bad", error="ascii_request boş")

        try:
            self._serial.reset_input_buffer()
            payload = (request + terminator).encode("ascii", errors="replace")
            self._serial.write(payload)
            raw_line = self._serial.read_until(terminator.encode("ascii"))
        except Exception as exc:  # noqa: BLE001
            return ReadResult(quality="bad", error=f"{type(exc).__name__}: {exc}")

        if not raw_line:
            return ReadResult(quality="bad", error="cihazdan yanıt yok (timeout)")

        line = raw_line.decode("ascii", errors="replace").strip()
        regex = sensor.ascii_response_regex
        if not regex:
            # Regex yoksa tüm yanıt string olarak döner.
            value: Any = line
        else:
            try:
                m = re.search(regex, line)
            except re.error as exc:
                return ReadResult(quality="bad", error=f"regex hata: {exc}", raw=line)
            if not m:
                return ReadResult(quality="bad", error="regex eşleşmedi", raw=line)
            value = m.group(1) if m.groups() else m.group(0)
            # Sayısal dönüşüm denemesi
            try:
                value = float(value)
            except (TypeError, ValueError):
                pass  # string olarak kalsın

        # Mühendislik dönüşümü (numeric ise)
        if isinstance(value, (int, float)):
            scale = sensor.scale if sensor.scale is not None else 1.0
            offset = sensor.offset if sensor.offset is not None else 0.0
            value = value * scale + offset

        return ReadResult(value=value, quality="good", raw=line)
