"""ProtocolReader abstract sınıfı + ReadResult dataclass."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class ReadResult:
    """Tek sensör okuma sonucu."""
    value: Any = None
    quality: str = "good"        # good / bad / uncertain / stale
    raw: Any = None              # debug: orijinal register/byte verisi
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.quality == "good" and not self.error


class ProtocolReader(ABC):
    """Bir Connection için protokol-spesifik okuyucu.

    Yaşam döngüsü:
      reader = build_reader(connection)
      if reader.open():
          for sensor in connection.sensors.filter(is_active=True):
              result = reader.read(sensor)
              # result.value, result.quality, result.error
      reader.close()

    Scan group (batch) okuma:
      regs, err = reader.read_raw(slave_id=1, function=3, address=0, count=100)
      # regs → [int, int, ...] veya None; err → "" ya da hata mesajı

    Veya context manager olarak:
      with build_reader(connection) as reader:
          if not reader.connected: return
          for sensor in ...: reader.read(sensor)
    """

    def __init__(self, connection):
        self.connection = connection
        self.connected: bool = False
        self.last_error: str = ""

    @abstractmethod
    def open(self) -> bool:
        """Bağlantıyı kur. Başarılıysa True döner; aksi halde
        `self.last_error` doldurulur ve False döner."""
        raise NotImplementedError

    @abstractmethod
    def read(self, sensor) -> ReadResult:
        """Tek sensörü oku."""
        raise NotImplementedError

    def read_raw(self, *, slave_id: int, function: int,
                 address: int, count: int) -> tuple[list[int] | None, str]:
        """Scan group batch okuma için ham register/bit listesi döner.

        Dönen değer: (regs, error_message). Başarılıysa regs=list[int], error="".
        Başarısızsa regs=None, error=açıklama.

        Default implementation: "desteklenmiyor" hatası (ASCII custom vb. için).
        Modbus reader'ları bu metodu override eder.
        """
        return None, f"read_raw {type(self).__name__} tarafından desteklenmiyor"

    @abstractmethod
    def close(self) -> None:
        """Bağlantıyı kapat. Idempotent olmalı."""
        raise NotImplementedError

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            logger.exception("Reader close hatası")
        return False
