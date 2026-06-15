"""
Worker process'e özel persistent connection pool.

Celery worker başına bir kez TCP/serial socket açılır ve polling cycle'ları
boyunca açık kalır. RUT906 gibi serial-to-TCP gateway'lerin rapid
connect/disconnect pattern'inde socket pool'u kilitleme sorununu çözer:
bizim tarafta kullanılan session sayısı 1'e sabitlenir.

Davranış:
- `get_reader(connection)` → cached + connected reader'ı döner veya yeni açar.
- Herhangi bir read sırasında connection-level hata olursa caller
  `invalidate(conn_id)` ile pool'u temizler; sonraki polling'de taze bağlantı.
- `record_failure` / `record_success` ile peş peşe N başarısız cycle sonrası
  otomatik invalidate yapılır (slow-bleed senaryolarına karşı watchdog).
- Her worker process'in kendi pool'u vardır (module-global dict). Prefork
  pool'da concurrency kadar aynı gateway'e session açılır — bu hala rapid
  churn'den çok daha iyi.

Worker shutdown'da `close_all()` çağrılır (Celery worker_shutdown sinyali).
"""
from __future__ import annotations

import logging
import socket as sock_mod
import threading
from typing import Dict, Optional

from .readers import ProtocolReader, build_reader


logger = logging.getLogger(__name__)


# Aynı bağlantıda peş peşe bu kadar başarısız polling cycle → pool invalidate.
# Bir cycle'da bir sensör fail edebilir ama bu sayaç "hiç başarılı okuma yoksa"
# koşuluna göre işler.
MAX_CONSECUTIVE_FAILED_CYCLES = 3

# Linux TCP keep-alive parametreleri. Windows'ta SIO_KEEPALIVE_VALS ioctl
# gerekir; şimdilik sadece SO_KEEPALIVE enable edilir (OS default idle süresi
# kullanılır — Win: 2 saat; kötü ama sıfırdan iyi).
KEEPALIVE_IDLE_SEC = 60       # ilk probe'dan önce idle süresi
KEEPALIVE_INTERVAL_SEC = 10   # probe'lar arası süre
KEEPALIVE_PROBES = 3          # kaç başarısız probe → ölü say


_POOL: Dict[int, ProtocolReader] = {}
_FAILURES: Dict[int, int] = {}
_LOCK = threading.Lock()


def get_reader(connection) -> Optional[ProtocolReader]:
    """Cached reader varsa döner; yoksa yeni aç + keep-alive + cache'le.

    Başarısızsa None döner (caller bağlantı kurulamadığını bilir, Connection
    tablosundaki last_error_message'ı günceller).
    """
    with _LOCK:
        reader = _POOL.get(connection.pk)
        if reader is not None and reader.connected:
            return reader

        # Cache'te var ama connected=False — kapat ve at
        if reader is not None:
            _safe_close(reader)
            _POOL.pop(connection.pk, None)

        # Taze bağlantı aç
        reader = build_reader(connection)
        if not reader.open():
            logger.warning(
                "connection_pool: open() başarısız conn=%s (%s): %s",
                connection.pk, connection.name, reader.last_error,
            )
            _safe_close(reader)
            return None

        _apply_keepalive(reader, connection)
        _POOL[connection.pk] = reader
        _FAILURES[connection.pk] = 0
        logger.info(
            "connection_pool: yeni bağlantı açıldı conn=%s (%s) %s",
            connection.pk, connection.name, connection.protocol,
        )
        return reader


def invalidate(conn_id: int, reason: str = "") -> None:
    """Pool'dan sil + socket'i zorla kapat. Sonraki get_reader çağrısı taze açar."""
    with _LOCK:
        reader = _POOL.pop(conn_id, None)
        _FAILURES.pop(conn_id, None)
        if reader is not None:
            _safe_close(reader)
            logger.info("connection_pool: invalidate conn=%s reason=%s", conn_id, reason or "-")


def record_success(conn_id: int) -> None:
    """Başarılı cycle sonrası failure sayacını sıfırla."""
    with _LOCK:
        _FAILURES[conn_id] = 0


def record_failure(conn_id: int) -> bool:
    """Başarısız cycle'ı sayar. Threshold'a ulaştıysa True döner (caller invalidate etmeli)."""
    with _LOCK:
        _FAILURES[conn_id] = _FAILURES.get(conn_id, 0) + 1
        return _FAILURES[conn_id] >= MAX_CONSECUTIVE_FAILED_CYCLES


def close_all(reason: str = "shutdown") -> int:
    """Tüm cached socket'leri kapatır. Kapatılan bağlantı sayısını döner.

    Worker shutdown'da (worker_shutdown sinyali) ve dashboard "Açık Bağlantıları
    Kapat" düğmesinden (`close_scada_pool` control command) çağrılır.
    """
    with _LOCK:
        count = len(_POOL)
        for conn_id, reader in list(_POOL.items()):
            _safe_close(reader)
            logger.info("connection_pool: %s close conn=%s", reason, conn_id)
        _POOL.clear()
        _FAILURES.clear()
        return count


def stats() -> dict:
    """Debug/monitoring için pool özetini döner."""
    with _LOCK:
        return {
            "open_count": len(_POOL),
            "connections": {
                conn_id: {
                    "protocol": r.connection.protocol,
                    "host": r.connection.host or r.connection.serial_port,
                    "connected": r.connected,
                    "failures": _FAILURES.get(conn_id, 0),
                }
                for conn_id, r in _POOL.items()
            },
        }


def _safe_close(reader: ProtocolReader) -> None:
    try:
        reader.close()
    except Exception:  # noqa: BLE001
        logger.exception("connection_pool: reader.close() hatası")


def _apply_keepalive(reader: ProtocolReader, connection) -> None:
    """TCP keep-alive aktif et (sadece TCP transport için anlamlı).

    ModbusTcpClient içindeki socket'e erişmek pymodbus sürümüne göre değişir —
    `_client.socket` denenip yoksa `_client.transport._sock` fallback'i.
    Başarısız olursa sadece log'lanır, polling devam eder.
    """
    if connection.transport != "tcp":
        return

    sock = _extract_socket(reader)
    if sock is None:
        logger.debug("connection_pool: TCP socket bulunamadı, keep-alive atlanıyor")
        return

    try:
        sock.setsockopt(sock_mod.SOL_SOCKET, sock_mod.SO_KEEPALIVE, 1)
        # Linux-only TCP tunables
        if hasattr(sock_mod, "TCP_KEEPIDLE"):
            sock.setsockopt(sock_mod.IPPROTO_TCP, sock_mod.TCP_KEEPIDLE, KEEPALIVE_IDLE_SEC)
        if hasattr(sock_mod, "TCP_KEEPINTVL"):
            sock.setsockopt(sock_mod.IPPROTO_TCP, sock_mod.TCP_KEEPINTVL, KEEPALIVE_INTERVAL_SEC)
        if hasattr(sock_mod, "TCP_KEEPCNT"):
            sock.setsockopt(sock_mod.IPPROTO_TCP, sock_mod.TCP_KEEPCNT, KEEPALIVE_PROBES)
        logger.debug("connection_pool: TCP keep-alive aktif conn=%s", connection.pk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("connection_pool: keep-alive setsockopt hatası: %s", exc)


def _extract_socket(reader: ProtocolReader):
    """pymodbus client nesnesinden underlying socket'i bul (sürüm farkına dayanıklı)."""
    client = getattr(reader, "_client", None)
    if client is None:
        return None
    for attr_chain in (("socket",), ("transport", "_sock"), ("transport", "transport", "_sock")):
        obj = client
        try:
            for attr in attr_chain:
                obj = getattr(obj, attr)
            if obj is not None and hasattr(obj, "setsockopt"):
                return obj
        except AttributeError:
            continue
    return None
