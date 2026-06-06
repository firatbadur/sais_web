"""Pure-Python .po -> .mo compiler.

Windows'ta GNU gettext bulunmadığı için Django'nun `compilemessages` komutu
çalışmıyor. Bu script PO dosyasını okuyup .mo binary'sini yazar. Sadece
single-msgid (plural olmayan) entry'leri destekler — projemizin kullandığı
yegane biçim bu.

Kullanım:
    python scripts/compile_po.py dashboard/locale/en/LC_MESSAGES/django.po
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path


def parse_po(po_text: str) -> dict[str, str]:
    """PO içeriğini msgid -> msgstr dict'ine çevir.

    GNU gettext'in PO formatının basit alt-kümesi:
      - `#` ile başlayan yorum satırları
      - msgid "..." [ \n "..."] ...
      - msgstr "..." [ \n "..."] ...
      - Boş satırla ayrılmış entry blokları
    """
    entries: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    mode: str | None = None

    def flush():
        if msgid is not None and msgstr is not None:
            key = "".join(msgid)
            val = "".join(msgstr)
            entries[key] = val

    string_re = re.compile(r'^"((?:[^"\\]|\\.)*)"$')

    def decode(s: str) -> str:
        # \n, \t, \", \\ kaçışlarını işle
        return (s.replace('\\\\', '\x00')
                .replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
                .replace('\x00', '\\'))

    for raw_line in po_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if mode == "msgstr":
                flush()
            msgid = msgstr = None
            mode = None
            continue
        if line.startswith("msgid "):
            if mode == "msgstr":
                flush()
            msgid = []
            msgstr = None
            m = string_re.match(line[6:].strip())
            if m:
                msgid.append(decode(m.group(1)))
            mode = "msgid"
            continue
        if line.startswith("msgstr "):
            msgstr = []
            m = string_re.match(line[7:].strip())
            if m:
                msgstr.append(decode(m.group(1)))
            mode = "msgstr"
            continue
        # Continuation: "..."
        m = string_re.match(line)
        if m:
            piece = decode(m.group(1))
            if mode == "msgid" and msgid is not None:
                msgid.append(piece)
            elif mode == "msgstr" and msgstr is not None:
                msgstr.append(piece)

    # Dosya sonunda son entry'yi flush et
    if mode == "msgstr":
        flush()

    return entries


def write_mo(entries: dict[str, str], mo_path: Path) -> None:
    """GNU .mo binary'sini yaz.

    Spec: https://www.gnu.org/software/gettext/manual/html_node/MO-Files.html
    """
    keys = sorted(entries.keys())
    encoded = [(k.encode("utf-8"), entries[k].encode("utf-8")) for k in keys]

    keystart = 7 * 4
    valuestart = keystart + len(encoded) * 8
    koffsets: list[tuple[int, int]] = []
    voffsets: list[tuple[int, int]] = []

    string_section = b""
    offset = valuestart + len(encoded) * 8
    for k, v in encoded:
        koffsets.append((len(k), offset))
        offset += len(k) + 1
    for k, v in encoded:
        voffsets.append((len(v), offset))
        offset += len(v) + 1
    for k, _ in encoded:
        string_section += k + b"\x00"
    for _, v in encoded:
        string_section += v + b"\x00"

    output = struct.pack(
        "<Iiiiiii",
        0x950412DE,           # magic
        0,                    # version
        len(encoded),         # nstrings
        keystart,             # offset of key table
        valuestart,           # offset of value table
        0,                    # hash table size
        0,                    # hash table offset (no hash)
    )
    for length, off in koffsets:
        output += struct.pack("<ii", length, off)
    for length, off in voffsets:
        output += struct.pack("<ii", length, off)
    output += string_section

    mo_path.write_bytes(output)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python compile_po.py <path/to/django.po>")
        return 2
    po_path = Path(argv[1])
    if not po_path.is_file():
        print(f"PO not found: {po_path}")
        return 1
    po_text = po_path.read_text(encoding="utf-8")
    entries = parse_po(po_text)
    mo_path = po_path.with_suffix(".mo")
    write_mo(entries, mo_path)
    print(f"Wrote {mo_path} ({len(entries)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
