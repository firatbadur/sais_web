"""Container içinden açık TCP soketlerini sayma yardımcıları (teşhis amaçlı).

`netstat`/`ss` imajda kurulu olmayabilir; `/proc/net/tcp` her Linux
container'da vardır ve **yalnız o container'ın network namespace'ini** gösterir.
Bu yüzden worker'ların açık soketlerini görmek için bu modül worker process'i
içinden (control command ile) de çağrılır.

Windows/dev'de `/proc` yoktur → `None` döner (çağıran "ölçülemedi" der).
"""
from __future__ import annotations

import socket as sock_mod
from pathlib import Path


# /proc/net/tcp `st` kolonu → okunabilir TCP durumu.
TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
    "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
    "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING",
}


def resolve_ip(host: str) -> str | None:
    """Hostname → IPv4. Zaten IP ise olduğu gibi döner; çözülemezse None."""
    if not host:
        return None
    try:
        return sock_mod.gethostbyname(host)
    except OSError:
        return None


def _hex_to_ipv4(hex_addr: str) -> str:
    """`/proc/net/tcp` little-endian hex adresi → noktalı gösterim."""
    b = bytes.fromhex(hex_addr)
    return ".".join(str(x) for x in reversed(b))


def count_sockets(host: str, port: int) -> dict[str, int] | None:
    """Bu container'dan ``host:port``'a açık soketleri duruma göre sayar.

    Döner: ``{"ESTABLISHED": 3, "TIME_WAIT": 1, ...}`` veya ``/proc`` yoksa None.
    """
    path = Path("/proc/net/tcp")
    if not path.exists():
        return None

    ip = resolve_ip(host)
    if ip is None:
        return None

    want_port = f"{int(port):04X}"
    counts: dict[str, int] = {}
    try:
        lines = path.read_text().splitlines()[1:]
    except OSError:
        return None

    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        rem = parts[2]
        if ":" not in rem:
            continue
        rem_ip_hex, rem_port_hex = rem.split(":", 1)
        if rem_port_hex.upper() != want_port:
            continue
        try:
            if _hex_to_ipv4(rem_ip_hex) != ip:
                continue
        except ValueError:
            continue
        state = TCP_STATES.get(parts[3].upper(), parts[3])
        counts[state] = counts.get(state, 0) + 1
    return counts
