"""Seri köprü (serial bridge) yapılandırma çekirdeği.

Docker/WSL2 saha kurulumlarında container Windows COM portunu açamaz. Host'taki
`EnvisoftWebX-SerialBridge` servisi (installer/scripts/serial-bridge.ps1) seri
portları TCP'ye aynalar; bu modül o servisin okuyacağı `serial-bridge.json`'ı
Connection tablosundan üretir (WebSettings → Caddyfile deseniyle aynı: DB tek
doğruluk kaynağı, dosya her `apply()` çağrısında idempotent yeniden üretilir).

Worker tarafındaki şeffaf yönlendirme `scada_io.bridge_redirect`'tedir ve port
numarasını buradaki `listen_port_for()` ile hesaplar — iki taraf aynı pure
fonksiyonu kullandığından ayrıca senkron dosya/DB bakışı gerekmez.

Generic SCADA infra → api/.
"""
from __future__ import annotations

import json
import logging
import os

from django.conf import settings
from django.utils import timezone

from .web_proxy import _atomic_write

logger = logging.getLogger(__name__)


# Sensör reader'larıyla aynı eşleme (scada_io/readers/modbus_serial.py).
_PARITY_MAP = {0: "N", 1: "O", 2: "E"}

# pk doğrudan porta eklenirse BigAutoField 65535'i aşabilir; modulo ile
# 8900-18899 aralığında kalınır (aynı sahada pk farkı tam 10000 olan iki canlı
# seri bağlantı pratikte imkânsız; renderer yine de duplicate guard'lıdır).
_PORT_MODULO = 10000

CONFIG_FILENAME = "serial-bridge.json"
STATUS_FILENAME = "status.json"


def listen_port_for(pk: int) -> int:
    """Bağlantının host köprüsündeki TCP dinleme portu (tek doğruluk kaynağı)."""
    base = int(getattr(settings, "SERIAL_BRIDGE_PORT_BASE", 8900))
    return base + (int(pk) % _PORT_MODULO)


def render_config() -> dict:
    """Aktif seri Connection'lardan köprü config dict'i üretir."""
    from .models import Connection

    bridges = []
    used_ports: dict[int, int] = {}  # listen_port → connection_id
    qs = (
        Connection.objects.filter(transport="serial", is_enabled=True)
        .order_by("pk")
    )
    for conn in qs:
        port = listen_port_for(conn.pk)
        if port in used_ports:
            logger.warning(
                "serial_bridge: port çakışması %s (conn=%s ile conn=%s) — sonraki atlandı",
                port, used_ports[port], conn.pk,
            )
            continue
        used_ports[port] = conn.pk
        bridges.append({
            "connection_id": conn.pk,
            "name": conn.name,
            "com_port": conn.serial_port or "",
            "baudrate": conn.baudrate or 9600,
            "parity": _PARITY_MAP.get(conn.parity or 0, "N"),
            "stopbits": conn.stop_bits or 1,
            "bytesize": conn.byte_size or 8,
            "xonxoff": bool(conn.xonxoff),
            "rtscts": bool(conn.rtscts),
            "dsrdtr": bool(conn.dsrdtr),
            "timeout_ms": conn.timeout_ms or 2000,
            "listen_port": port,
        })

    return {
        "version": 1,
        "generated_at": timezone.now().isoformat(),
        "port_base": int(getattr(settings, "SERIAL_BRIDGE_PORT_BASE", 8900)),
        "bridges": bridges,
    }


def apply() -> tuple[bool, str]:
    """Config'i SERIAL_BRIDGE_DIR/serial-bridge.json'a atomik yazar.

    Dizin yoksa sessizce atlanır (dev makinesi, worker container'ı, Linux saha —
    mount yalnız web'de). Boş `bridges` listesi de YAZILIR: host köprüsü için
    "tüm köprüleri kapat" sinyalidir. Hata hiçbir zaman çağıranın save'ini
    kırmaz.
    """
    directory = getattr(settings, "SERIAL_BRIDGE_DIR", "") or ""
    if not directory or not os.path.isdir(directory):
        return True, "skipped"
    try:
        cfg = render_config()
        path = os.path.join(directory, CONFIG_FILENAME)
        _atomic_write(path, json.dumps(cfg, ensure_ascii=False, indent=2))
        logger.info("serial_bridge: config üretildi (%s köprü) → %s",
                    len(cfg["bridges"]), path)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — render asla save'i kırmasın
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("serial_bridge: config üretilemedi")
        return False, msg


def _on_connection_change(sender, instance, **kwargs):
    apply()


def connect_signals() -> None:
    """Connection post_save/post_delete → config yeniden üret.

    Poller runtime alanlarını queryset `.update()` ile yazar (post_save tetiklemez)
    → her polling'de sahte render olmaz. Admin/dashboard/import kayıtlarının tümü
    kapsanır.
    """
    from django.db.models.signals import post_delete, post_save

    from .models import Connection

    post_save.connect(
        _on_connection_change, sender=Connection,
        dispatch_uid="serial_bridge_render_on_connection_save",
    )
    post_delete.connect(
        _on_connection_change, sender=Connection,
        dispatch_uid="serial_bridge_render_on_connection_delete",
    )
