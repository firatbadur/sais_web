"""Saf-Python msgfmt — Windows'ta gettext binary olmadan .po -> .mo derler.

Sadece tekil (non-plural) msgid/msgstr destekler; bu projedeki django.po
yapısı bununla uyumlu. Kullanım:
    python _compile_po.py dashboard/locale/en/LC_MESSAGES/django.po
"""
import struct
import sys


def decode(s: str) -> str:
    out = []
    i = 0
    esc = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(esc.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_po(path: str) -> dict:
    entries = {}
    cur_id = cur_str = None
    state = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("msgid "):
                if cur_id is not None and cur_str is not None:
                    entries[cur_id] = cur_str
                cur_id = decode(line[7:-1])
                cur_str = None
                state = "id"
            elif line.startswith("msgstr "):
                cur_str = decode(line[8:-1])
                state = "str"
            elif line.startswith('"') and line.endswith('"'):
                seg = decode(line[1:-1])
                if state == "id":
                    cur_id += seg
                elif state == "str":
                    cur_str += seg
            else:
                state = None
    if cur_id is not None and cur_str is not None:
        entries[cur_id] = cur_str
    return entries


def write_mo(entries: dict, path: str) -> int:
    items = [(k, v) for k, v in entries.items() if v != "" or k == ""]
    items.sort(key=lambda kv: kv[0].encode("utf-8"))
    keys = [k.encode("utf-8") for k, _ in items]
    vals = [v.encode("utf-8") for _, v in items]
    n = len(items)
    blob = b""
    koff = []
    base = 7 * 4 + 16 * n
    t = base
    for k in keys:
        koff.append((len(k), t))
        blob += k + b"\x00"
        t += len(k) + 1
    voff = []
    for v in vals:
        voff.append((len(v), t))
        blob += v + b"\x00"
        t += len(v) + 1
    out = struct.pack("Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + 8 * n, 0, 0)
    for l, off in koff:
        out += struct.pack("ii", l, off)
    for l, off in voff:
        out += struct.pack("ii", l, off)
    out += blob
    with open(path, "wb") as f:
        f.write(out)
    return n


if __name__ == "__main__":
    po = sys.argv[1]
    mo = po[:-3] + ".mo"
    ents = parse_po(po)
    n = write_mo(ents, mo)
    print(f"MO yazildi: {mo} | {n} entry | Kapandi -> {ents.get(chr(75)+'apand'+chr(305))!r}")
