"""
SAIS Bakanlık ticket / cookie cache yardımcıları.

Orijinal entegrasyon kodu ticket'ı ``C:/Logs/Factory Server/header.txt`` ve
cookie'leri ``cookie.txt`` dosyalarında pickle'la tutuyordu. Web servisinde
süreç ölümünden bağımsız olarak — ve birden fazla worker'ın paylaşabilmesi
için — Django cache (Redis) backend'e taşındı.

Cache key'leri kabin (``SaisCabinet``) ID'si bazında izole edilmiştir;
bir istasyona ait login session başka istasyonu etkilemez.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from django.conf import settings
from django.core.cache import cache


def _ticket_ttl() -> int:
    """Ticket'ın cache'te kalacağı süre (saniye).

    Bakanlık ticket'ının resmi geçerlilik süresi belgelenmemiş; default
    1 saat tutulur. ``SAIS_SIM_TICKET_TTL`` env/setting ile override edilir.
    Süre dolduğunda 401 yanıtı ile ``SaisSimClient`` zaten otomatik
    re-login yapar; TTL bunu önlemek için bir alt sınır.
    """
    return int(getattr(settings, "SAIS_SIM_TICKET_TTL", 3600))


def double_md5(plaintext: str) -> str:
    """Bakanlık login servisinin beklediği çift-MD5 hash.

    Servis spec'i: önce şifrenin MD5 hex digest'i alınır, sonra o digest
    string'inin tekrar MD5'i. Düz şifre asla hat üzerinden gönderilmez.
    """
    if plaintext is None:
        plaintext = ""
    first = hashlib.md5(plaintext.encode("utf-8")).hexdigest()
    second = hashlib.md5(first.encode("utf-8")).hexdigest()
    return second


def _ticket_key(cabinet_id: int) -> str:
    return f"sais:sim:ticket:{cabinet_id}"


def _cookies_key(cabinet_id: int) -> str:
    return f"sais:sim:cookies:{cabinet_id}"


def store_session(cabinet_id: int, ticket: str, cookies: dict) -> None:
    """Login sonrası ticket + cookie dict'ini cache'e yaz."""
    ttl = _ticket_ttl()
    cache.set(_ticket_key(cabinet_id), ticket, ttl)
    cache.set(_cookies_key(cabinet_id), dict(cookies or {}), ttl)


def load_session(cabinet_id: int) -> Tuple[Optional[str], dict]:
    """Cache'ten ticket + cookie dict'ini oku.

    Yoksa (``None``, ``{}``) döner; çağıran ``login()`` ile tetiklemeli.
    """
    ticket = cache.get(_ticket_key(cabinet_id))
    cookies = cache.get(_cookies_key(cabinet_id)) or {}
    return ticket, cookies


def clear_session(cabinet_id: int) -> None:
    """Login öncesi mevcut session bilgilerini temizle."""
    cache.delete_many([_ticket_key(cabinet_id), _cookies_key(cabinet_id)])
