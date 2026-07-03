"""Kabin sırları için şeffaf Fernet şifreleme (at-rest).

Amaç: `SaisCabinet.auth_secret` (Bakanlık SIM şifresi) DB'de + `pg_dump` yedeklerinde
DÜZ METİN durmasın. `EncryptedCharField` Python tarafında düz metin sunar (okuma/yazma
kodu değişmez), DB'ye Fernet ile şifreli yazar.

Anahtar: `CABINET_FERNET_KEY` (urlsafe base64, 32 byte) env'den; verilmezse kuruluma
özel stabil `SECRET_KEY`'den türetilir (SECRET_KEY zaten .env'de sabit bir sırdır).
Tehdit modeli: sızan DB/yedek dosyası. `.env`'e erişimi olan zaten anahtara da erişir —
bu katman ondan korumaz, at-rest sızıntısından korur.

Not: `SECRET_KEY` DÖNDÜRÜLÜRSE (rotate) türetilen anahtar değişir → eski şifreli
değerler okunamaz. Kalıcı kurulumlarda `CABINET_FERNET_KEY`'i açıkça set edip sabit tutun.
"""
from __future__ import annotations

import base64
import hashlib

from django.conf import settings
from django.db import models

PREFIX = "fernet:"  # şifreli değer işareti (legacy düz metinden ayırt etmek için)


def _fernet():
    from cryptography.fernet import Fernet

    key = (getattr(settings, "CABINET_FERNET_KEY", "") or "").strip()
    if not key:
        # SECRET_KEY'den deterministik 32-byte anahtar türet → urlsafe base64.
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest).decode("ascii")
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


def encrypt(value):
    """Düz metin → 'fernet:<token>'. Boş/None ve zaten şifreli değer olduğu gibi döner."""
    if value is None or value == "":
        return value
    if isinstance(value, str) and value.startswith(PREFIX):
        return value  # idempotent
    token = _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(value):
    """'fernet:<token>' → düz metin. Prefix yoksa legacy düz metin kabul edilir."""
    if not value or not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value  # çözülemezse ham değeri ver (fail-safe; erişimi bloklamaz)


class EncryptedCharField(models.CharField):
    """DB'de Fernet-şifreli, Python'da düz metin sunan CharField.

    `max_length` şifreli token'ı barındıracak kadar geniş olmalı (Fernet token ~
    baz64; kısa sırlar için ~120 char). Model'de 512 kullanılıyor.
    """

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def to_python(self, value):
        return decrypt(value) if isinstance(value, str) else value

    def get_prep_value(self, value):
        return encrypt(super().get_prep_value(value))
