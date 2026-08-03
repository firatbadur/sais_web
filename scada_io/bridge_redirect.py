"""Seri → TCP köprü yönlendirmesi (Windows/WSL2 saha kurulumları).

Container COM portunu açamaz; host'taki EnvisoftWebX-SerialBridge servisi seri
portu TCP'ye aynalar (config üretimi: api/serial_bridge.py). Burada seri
Connection'lar reader/writer factory'sinde ŞEFFAFÇA o TCP ucuna çevrilir —
kullanıcı dashboard'da "Modbus RTU (Serial) + COM5" tanımlar, motor köprü
üzerinden konuşur. Yalnız SERIAL_BRIDGE_HOST doluysa aktiftir; dev/Linux'ta
boş → seri port doğrudan açılır (davranış değişmez).
"""
from __future__ import annotations

import copy

# RTU/ASCII frame'i TCP üzerinden aynı byte'larla gider (gateway transparent
# mode) → mevcut *_over_tcp reader/writer'ları köprüyle birebir çalışır.
# ascii_custom map'te yok: kendi reader'ı transport=="tcp" iken socket:// açar.
_PROTOCOL_MAP = {
    "modbus_rtu": "modbus_rtu_over_tcp",
    "modbus_ascii": "modbus_ascii_over_tcp",
}


def maybe_redirect(connection):
    """transport=='serial' + SERIAL_BRIDGE_HOST dolu → TCP'ye çevrilmiş kopya.

    Orijinal model instance'ına asla dokunulmaz (shallow copy, kaydedilmez);
    pool keying (`_POOL[connection.pk]`) orijinal pk'yı kullanmaya devam eder.
    """
    if getattr(connection, "transport", "") != "serial":
        return connection
    try:
        from django.conf import settings
        host = (getattr(settings, "SERIAL_BRIDGE_HOST", "") or "").strip()
    except Exception:  # noqa: BLE001 — settings yok (test/CLI) → redirect kapalı
        return connection
    if not host:
        return connection

    from api.serial_bridge import listen_port_for  # port formülünün tek kaynağı

    bridged = copy.copy(connection)
    bridged.host = host
    bridged.port = listen_port_for(connection.pk)
    bridged.transport = "tcp"
    bridged.protocol = _PROTOCOL_MAP.get(connection.protocol, connection.protocol)
    return bridged
