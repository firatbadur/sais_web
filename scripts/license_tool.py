"""
Lisans imzalama aracı (issuer) — SENİN makinende çalışır, app'in PARÇASI DEĞİL.

Ed25519 ile saha lisansları imzalar. Private key gizli kalır (asla repo'ya/app'e
girmez); public key app'e gömülür (settings.LICENSE_PUBLIC_KEY). Sahalar imzalı
token'ı doğrulayıp süre dolana kadar çalışır — müşteri yerel kaydı değiştirse bile
geçerli imza üretemez.

Kullanım:
    python scripts/license_tool.py keygen
        → license_private_key.hex (GİZLİ, .gitignore) + public key (settings'e koy)

    python scripts/license_tool.py issue --key site-abc --customer "X Belediyesi" \
        --expires 2027-06-08 [--private license_private_key.hex] [--out site-abc.json]
        → imzalı token dosyası

    python scripts/license_tool.py manifest site-abc.json site-def.json --out manifest.json
        → {"licenses": {"site-abc": {...}, ...}} (GitHub'a koyacağın dosya)

    python scripts/license_tool.py verify site-abc.json --public <hex>
        → imza geçerli mi (test)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519


def canonical(payload: dict) -> bytes:
    """İmzalanan/doğrulanan kanonik gösterim — app ile BİREBİR aynı olmalı.

    api/licensing.py içindeki canonical() ile senkron tutulmalı.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def cmd_keygen(args):
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes_raw().hex()
    pub_hex = priv.public_key().public_bytes_raw().hex()
    out = Path(args.private)
    out.write_text(priv_hex, encoding="utf-8")
    print(f"Private key yazıldı: {out}  (GİZLİ TUT — .gitignore'a ekle, kimseyle paylaşma)")
    print(f"\nPublic key (settings.LICENSE_PUBLIC_KEY default'una / .env'e koy):\n{pub_hex}\n")


def _load_private(path: str) -> ed25519.Ed25519PrivateKey:
    hex_str = Path(path).read_text(encoding="utf-8").strip()
    return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_str))


def cmd_issue(args):
    priv = _load_private(args.private)
    # expires: 'YYYY-MM-DD' → o günün sonu (23:59:59) UTC, ya da tam ISO.
    expires = args.expires
    try:
        d = datetime.fromisoformat(expires)
    except ValueError:
        d = datetime.combine(datetime.strptime(expires, "%Y-%m-%d").date(), time(23, 59, 59))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "key": args.key,
        "customer": args.customer,
        "issued_at": now.isoformat(),
        "expires_at": d.isoformat(),
        "version": 1,
    }
    # Makine-bağlama (node-lock): verilmişse token bu donanım parmak izine kilitlenir.
    # Sahanın parmak izi dashboard → Yönetici → Lisans sayfasında gösterilir.
    if args.machine_fingerprint:
        payload["machine"] = args.machine_fingerprint.strip()
    signature = priv.sign(canonical(payload)).hex()
    token = {"payload": payload, "signature": signature}

    out = Path(args.out or f"{args.key}.json")
    out.write_text(json.dumps(token, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Token yazıldı: {out}")
    print(f"  key={args.key}  customer={args.customer}  expires={payload['expires_at']}"
          + (f"  machine={payload['machine']}" if payload.get("machine") else "  (makineye bağlı DEĞİL)"))


def cmd_manifest(args):
    licenses = {}
    for f in args.tokens:
        token = json.loads(Path(f).read_text(encoding="utf-8"))
        key = token["payload"]["key"]
        licenses[key] = token
    manifest = {"licenses": licenses, "schema": 1}
    out = Path(args.out)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Manifest yazıldı: {out}  ({len(licenses)} lisans). GitHub raw URL'i .env LICENSE_URL'e koy.")


def cmd_verify(args):
    token = json.loads(Path(args.token).read_text(encoding="utf-8"))
    pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(args.public))
    try:
        pub.verify(bytes.fromhex(token["signature"]), canonical(token["payload"]))
        print(f"[GECERLI]  key={token['payload']['key']}  expires={token['payload']['expires_at']}")
    except InvalidSignature:
        print("[GECERSIZ]  imza dogrulanamadi")
        return 1
    return 0


def main(argv):
    p = argparse.ArgumentParser(description="Ed25519 lisans imzalama aracı")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("keygen", help="Ed25519 anahtar çifti üret")
    g.add_argument("--private", default="license_private_key.hex")
    g.set_defaults(func=cmd_keygen)

    i = sub.add_parser("issue", help="Bir saha için imzalı lisans token'ı üret")
    i.add_argument("--key", required=True, help="Saha kimliği (LICENSE_KEY)")
    i.add_argument("--customer", required=True, help="Müşteri adı")
    i.add_argument("--expires", required=True, help="YYYY-MM-DD veya tam ISO tarih")
    i.add_argument("--machine-fingerprint", default=None,
                   help="Makine parmak izi (hex) — verilirse lisans o makineye kilitlenir "
                        "(node-lock). Sahanın parmak izi Lisans sayfasında gösterilir.")
    i.add_argument("--private", default="license_private_key.hex")
    i.add_argument("--out", default=None)
    i.set_defaults(func=cmd_issue)

    m = sub.add_parser("manifest", help="Tokenları tek manifest.json'a birleştir")
    m.add_argument("tokens", nargs="+", help="Token JSON dosyaları")
    m.add_argument("--out", default="manifest.json")
    m.set_defaults(func=cmd_manifest)

    v = sub.add_parser("verify", help="Token imzasını public key ile doğrula")
    v.add_argument("token", help="Token JSON dosyası")
    v.add_argument("--public", required=True, help="Public key (hex)")
    v.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
