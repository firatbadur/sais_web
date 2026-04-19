"""ProtocolWriter abstract sınıfı + WriteResult dataclass."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    success: bool = False
    response: Any = None     # cihazdan dönen onay (varsa)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.success and not self.error


class ProtocolWriter(ABC):
    """Bir Connection için protokol-spesifik yazıcı.

    Writer ve Reader genelde aynı bağlantıyı (TCP socket / serial port)
    paylaşmaz çünkü Celery task'larında ayrı invocation'larda çalışırlar.
    Polling cycle'da reader açar/kapar; command executor ayrı çağrıda
    writer açar/kapar.
    """

    def __init__(self, connection):
        self.connection = connection
        self.connected: bool = False
        self.last_error: str = ""

    @abstractmethod
    def open(self) -> bool: ...

    @abstractmethod
    def write(self, sensor, value: Any, value_type: str) -> WriteResult:
        """Tek sensöre yazma uygula. value_type:
        bool/int/float/string. Decoder katmanı `Sensor.data_type`'a göre
        encode eder.
        """

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            logger.exception("Writer close hatası")
        return False
