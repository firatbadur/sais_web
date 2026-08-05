"""
Modbus register listesi ↔ Python değer dönüşümü.

`Sensor.data_type` (int16/uint16/int32/uint32/int64/uint64/float32/float64/
bool/bit/string/raw) ile `Sensor.byte_order` (big/little) ve `word_order`
(big/little) parametrelerini dikkate alır.

byte_order = HER BİR register (16-bit word) İÇİNDEKİ byte sıralaması.
  'big' = wire'daki doğal Modbus sırası (high byte önce), 'little' = register
  içi byte swap.
word_order = çok-register'lı tipler için register'ların sıralaması.
  'big' = high word önce (doğal), 'little' = word swap.

32-bit float için 4 kombinasyon ↔ Modbus Poll gösterim eşlemesi:

  | Wire formatı | Modbus Poll adı            | byte_order | word_order |
  |--------------|----------------------------|------------|------------|
  | ABCD         | Big-endian                 | big        | big        |
  | CDAB         | Little-endian byte swap    | big        | little     |
  | BADC         | Big-endian byte swap       | little     | big        |
  | DCBA         | Little-endian              | little     | little     |

Not: byte_order tek-register tiplerde de (int16/uint16) uygulanır — 'little'
register'ın iki byte'ını takas eder (nadir cihazlarda görülür).
"""
from __future__ import annotations

import struct
from typing import Any, Iterable


# Her data_type için kaç register (16-bit word) tüketir.
REGISTER_COUNT = {
    "int16": 1, "uint16": 1, "bool": 1, "bit": 1,
    "int32": 2, "uint32": 2, "float32": 2,
    "int64": 4, "uint64": 4, "float64": 4,
    # string ve raw değişken; caller sensor.quantity kullanır
}

# struct format karakterleri (single value)
_STRUCT_FMT = {
    "int16":   "h", "uint16": "H",
    "int32":   "i", "uint32": "I",
    "int64":   "q", "uint64": "Q",
    "float32": "f", "float64": "d",
}


def _ordered_words(regs: list[int], word_order: str) -> list[int]:
    """Word swap. word_order='big' (high word first) doğal Modbus sırası;
    'little' yaygın CDAB swap'ı."""
    if word_order == "little":
        return list(reversed(regs))
    return list(regs)


def _apply_byte_order(words: list[int], byte_order: str) -> list[int]:
    """byte_order='little' ise her register'ın iki byte'ını takas et."""
    if byte_order == "little":
        return [((w & 0xFF) << 8) | ((w >> 8) & 0xFF) for w in words]
    return list(words)


def _words_to_bytes(words: list[int]) -> bytes:
    return b"".join(struct.pack(">H", w & 0xFFFF) for w in words)


def _bytes_to_words(data: bytes) -> list[int]:
    return [struct.unpack(">H", data[i:i + 2])[0] for i in range(0, len(data), 2)]


def decode_registers(
    regs: Iterable[int],
    data_type: str,
    *,
    byte_order: str = "big",
    word_order: str = "big",
    bit_position: int | None = None,
) -> Any:
    """Bir register listesinden tipli değer üret.

    coil/discrete-input okumalarında `regs` aslında bit listesi gelir;
    `data_type='bool'` veya `'bit'` durumunda buna göre ele alınır.
    """
    regs = list(regs)
    if not regs and data_type not in ("string", "raw"):
        return None

    dt = (data_type or "uint16").lower()

    # Coil/discrete tek-bit değerler
    if dt == "bool":
        return bool(regs[0])

    # Holding/input register'dan belirli bit
    if dt == "bit":
        if bit_position is None:
            raise ValueError("data_type='bit' için bit_position zorunlu")
        return bool((regs[0] >> bit_position) & 1)

    # String: registers[0..N] → 2 byte/register; null-byte'lar trim'lenir
    if dt == "string":
        words = _apply_byte_order(_ordered_words(regs, word_order), byte_order)
        return _words_to_bytes(words).rstrip(b"\x00").decode("ascii", errors="replace")

    if dt == "raw":
        words = _apply_byte_order(_ordered_words(regs, word_order), byte_order)
        return _words_to_bytes(words).hex()

    if dt not in _STRUCT_FMT:
        raise ValueError(f"Desteklenmeyen data_type: {data_type}")

    needed = REGISTER_COUNT[dt]
    if len(regs) < needed:
        raise ValueError(f"{dt} {needed} register gerektirir; {len(regs)} verildi")

    words = _apply_byte_order(_ordered_words(regs[:needed], word_order), byte_order)
    return struct.unpack(">" + _STRUCT_FMT[dt], _words_to_bytes(words))[0]


def decode_sensor_from_batch(
    batch_regs: list[int],
    batch_start_address: int,
    sensor,
) -> Any:
    """Bir scan group batch okumasından tek sensörün mühendislik değerini çıkar.

    Sensör `batch_start_address` ofsetindeki `sensor.address` pozisyonundan
    başlayan register'ları kullanır. data_type'a göre kaç register gerektiği
    `REGISTER_COUNT`'tan belirlenir. Decode sonrası `scale` * x + `offset`
    ve `digital_inverse` uygulanır.

    Raises:
        ValueError — aralık sınırları aşıldıysa veya data_type desteklenmiyorsa.
    """
    offset = (sensor.address or 0) - batch_start_address
    if offset < 0:
        raise ValueError(
            f"sensor.address ({sensor.address}) batch start_address ({batch_start_address})'ten küçük"
        )

    dt = (sensor.data_type or "uint16").lower()
    # String/raw için sensör `quantity` kullan; diğerleri REGISTER_COUNT'a göre.
    if dt in ("string", "raw"):
        needed = int(sensor.quantity or 1)
    else:
        needed = REGISTER_COUNT.get(dt, int(sensor.quantity or 1))

    if offset + needed > len(batch_regs):
        raise ValueError(
            f"sensor aralığı ({offset}..{offset + needed}) batch boyutunu ({len(batch_regs)}) aşıyor"
        )

    slice_regs = batch_regs[offset : offset + needed]
    decoded = decode_registers(
        slice_regs,
        data_type=dt,
        byte_order=sensor.byte_order or "big",
        word_order=sensor.word_order or "big",
        bit_position=sensor.bit_position,
    )

    if isinstance(decoded, (int, float)) and not isinstance(decoded, bool):
        scale = sensor.scale if sensor.scale is not None else 1.0
        offset_val = sensor.offset if sensor.offset is not None else 0.0
        value = decoded * scale + offset_val
        if sensor.digital_inverse and dt in ("bool", "bit"):
            value = not bool(value)
        return value
    if sensor.digital_inverse and isinstance(decoded, bool):
        return not decoded
    return decoded


def encode_value(
    value: Any,
    data_type: str,
    *,
    byte_order: str = "big",
    word_order: str = "big",
    bit_position: int | None = None,
    current_register: int | None = None,
) -> list[int]:
    """Yazma için: tipli değer → register listesi.

    `bit` durumunda mevcut register değerinin (current_register) ilgili
    bit'ini değiştirir; geri kalan bitleri korur.
    """
    dt = (data_type or "uint16").lower()

    if dt == "bool":
        return [1 if bool(value) else 0]

    if dt == "bit":
        if bit_position is None:
            raise ValueError("data_type='bit' için bit_position zorunlu")
        base = (current_register or 0) & 0xFFFF
        if bool(value):
            base |= (1 << bit_position)
        else:
            base &= ~(1 << bit_position)
        return [base & 0xFFFF]

    if dt == "string":
        b = str(value).encode("ascii", errors="replace")
        if len(b) % 2:
            b += b"\x00"
        words = _apply_byte_order(_bytes_to_words(b), byte_order)
        return _ordered_words(words, word_order)

    if dt == "raw":
        if isinstance(value, str):
            raw_bytes = bytes.fromhex(value)
        elif isinstance(value, (bytes, bytearray)):
            raw_bytes = bytes(value)
        else:
            raise ValueError("raw değer hex-string veya bytes olmalı")
        if len(raw_bytes) % 2:
            raw_bytes += b"\x00"
        words = _apply_byte_order(_bytes_to_words(raw_bytes), byte_order)
        return _ordered_words(words, word_order)

    if dt not in _STRUCT_FMT:
        raise ValueError(f"Desteklenmeyen data_type: {data_type}")

    packed = struct.pack(">" + _STRUCT_FMT[dt], value)
    words = _apply_byte_order(_bytes_to_words(packed), byte_order)
    return _ordered_words(words, word_order)
