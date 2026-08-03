"""Lisans enforcement güvenlik testleri.

Kapsam: imzalı token akışı + kurcalama senaryoları. `license_active()` kapısının
DB kolonlarına DEĞİL imzalı payload'a dayandığını doğrular (valid_until / created_at
manipülasyonu ile bypass edilemez), node-lock ve bootstrap grace davranışını test eder.
"""
import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from django.test import TestCase, override_settings
from django.utils import timezone

from api import licensing
from api.models import License


def _keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    return priv, pub_hex


# Test için sabit bir issuer çifti (public key setting'e override edilir).
_PRIV, _PUB_HEX = _keypair()


def _sign(payload: dict) -> dict:
    return {"payload": payload, "signature": _PRIV.sign(licensing.canonical(payload)).hex()}


@override_settings(
    LICENSE_ENFORCE=True,
    LICENSE_KEY="test-site",
    LICENSE_PUBLIC_KEY=_PUB_HEX,
    LICENSE_BOOTSTRAP_GRACE_HOURS=168,
    BUILD_EPOCH=0,
    MACHINE_FINGERPRINT="",
)
class LicenseEnforcementTests(TestCase):
    def _iso(self, **delta):
        return (timezone.now() + datetime.timedelta(**delta)).isoformat()

    def _apply(self, expires_iso, machine=""):
        payload = {
            "key": "test-site",
            "customer": "Test",
            "issued_at": self._iso(days=-400),
            "expires_at": expires_iso,
        }
        if machine:
            payload["machine"] = machine
        return licensing.apply_token(_sign(payload), source="test")

    # --- Temel akış ---
    def test_valid_token_active(self):
        self._apply(self._iso(days=30))
        self.assertTrue(licensing.license_active())

    def test_expired_token_locked(self):
        self._apply(self._iso(days=-1))
        self.assertFalse(licensing.license_active())

    def test_enforce_off_always_active(self):
        with override_settings(LICENSE_ENFORCE=False):
            self.assertTrue(licensing.license_active())

    # --- KRİTİK: DB kurcalama bypass'ı olmamalı ---
    def test_tampered_valid_until_still_locked(self):
        """Süresi geçmiş token + DB valid_until=2099 → yine kilit (imzalı süre esas)."""
        self._apply(self._iso(days=-1))
        lic = License.load()
        lic.valid_until = timezone.now() + datetime.timedelta(days=3650)
        lic.status = "active"
        lic.save()
        self.assertFalse(licensing.license_active())

    def test_tampered_raw_token_payload_locked(self):
        """raw_token payload'ı elle uzatılırsa imza bozulur → kilit."""
        self._apply(self._iso(days=-1))
        lic = License.load()
        lic.raw_token["payload"]["expires_at"] = self._iso(days=3650)
        lic.save()
        self.assertFalse(licensing.license_active())

    # --- Node-lock ---
    def test_node_lock_mismatch_locked(self):
        self._apply(self._iso(days=30), machine="AAAAFP")
        with override_settings(MACHINE_FINGERPRINT="BBBBFP"):
            self.assertFalse(licensing.license_active())

    def test_node_lock_match_active(self):
        self._apply(self._iso(days=30), machine="MATCHFP")
        with override_settings(MACHINE_FINGERPRINT="MATCHFP"):
            self.assertTrue(licensing.license_active())

    # --- Bootstrap grace ---
    def test_grace_new_install_active(self):
        License.objects.filter(pk=1).delete()  # queryset delete → gerçek silme
        License.load()  # taze created_at
        self.assertTrue(licensing.license_active())

    def test_grace_reset_but_old_image_locked(self):
        """created_at sıfırlansa bile imaj BUILD_EPOCH grace'ten eskiyse → kilit."""
        License.objects.filter(pk=1).delete()
        License.load()
        old_build = int((timezone.now() - datetime.timedelta(hours=200)).timestamp())
        with override_settings(BUILD_EPOCH=old_build):
            self.assertFalse(licensing.license_active())

    def test_grace_env_huge_capped(self):
        """`.env`'den devasa grace verilse bile MAX tavanı aşılamaz → eski kurulum kilit."""
        License.objects.filter(pk=1).delete()
        lic = License.load()
        # created_at'i tavandan (744s) eski yap → grace huge olsa da kilit.
        License.objects.filter(pk=1).update(
            created_at=timezone.now() - datetime.timedelta(hours=800)
        )
        with override_settings(LICENSE_BOOTSTRAP_GRACE_HOURS=999999, BUILD_EPOCH=0):
            self.assertFalse(licensing.license_active())


# ---------------------------------------------------------------------------
# Seri köprü config renderer (api/serial_bridge.py)
# ---------------------------------------------------------------------------

import json
import os
import tempfile

from api import serial_bridge
from api.models import Connection, Station


class SerialBridgeRenderTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="Test Tesis")
        self.tmpdir = tempfile.mkdtemp()

    def _conn(self, name, **kw):
        defaults = dict(
            station=self.station, name=name, protocol="modbus_rtu",
            transport="serial", serial_port="COM5", baudrate=9600,
            parity=2, stop_bits=1, byte_size=8, timeout_ms=3000,
            is_enabled=True,
        )
        defaults.update(kw)
        return Connection.objects.create(**defaults)

    def _read_config(self):
        path = os.path.join(self.tmpdir, serial_bridge.CONFIG_FILENAME)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_only_enabled_serial_connections_rendered(self):
        keep = self._conn("seri-acik")
        self._conn("seri-kapali", is_enabled=False)
        self._conn("tcp-baglanti", protocol="modbus_tcp", transport="tcp",
                   serial_port="", host="10.0.0.1")
        with override_settings(SERIAL_BRIDGE_DIR=self.tmpdir):
            ok, err = serial_bridge.apply()
        self.assertTrue(ok)
        cfg = self._read_config()
        self.assertEqual(len(cfg["bridges"]), 1)
        entry = cfg["bridges"][0]
        self.assertEqual(entry["connection_id"], keep.pk)
        self.assertEqual(entry["com_port"], "COM5")
        self.assertEqual(entry["parity"], "E")  # 2 → Even
        self.assertEqual(entry["timeout_ms"], 3000)
        self.assertEqual(entry["listen_port"],
                         serial_bridge.listen_port_for(keep.pk))

    def test_defaults_applied_for_null_fields(self):
        conn = self._conn("bos-alanlar", baudrate=None, parity=None,
                          stop_bits=None, byte_size=None)
        with override_settings(SERIAL_BRIDGE_DIR=self.tmpdir):
            serial_bridge.apply()
        entry = self._read_config()["bridges"][0]
        self.assertEqual(entry["baudrate"], 9600)
        self.assertEqual(entry["parity"], "N")
        self.assertEqual(entry["stopbits"], 1)
        self.assertEqual(entry["bytesize"], 8)
        self.assertEqual(entry["connection_id"], conn.pk)

    def test_missing_dir_skips_silently(self):
        self._conn("seri")
        with override_settings(SERIAL_BRIDGE_DIR="/olmayan/dizin/xyz"):
            ok, err = serial_bridge.apply()
        self.assertTrue(ok)
        self.assertEqual(err, "skipped")

    def test_empty_bridges_still_written(self):
        with override_settings(SERIAL_BRIDGE_DIR=self.tmpdir):
            ok, _ = serial_bridge.apply()
        self.assertTrue(ok)
        self.assertEqual(self._read_config()["bridges"], [])

    def test_signal_rerenders_on_save(self):
        with override_settings(SERIAL_BRIDGE_DIR=self.tmpdir):
            conn = self._conn("sinyal-testi")
            cfg = self._read_config()
            self.assertEqual(len(cfg["bridges"]), 1)
            conn.is_enabled = False
            conn.save()
            self.assertEqual(self._read_config()["bridges"], [])
            conn.delete()  # post_delete de render etmeli (dosya güncel kalır)
            self.assertEqual(self._read_config()["bridges"], [])

    def test_listen_port_formula(self):
        with override_settings(SERIAL_BRIDGE_PORT_BASE=8900):
            self.assertEqual(serial_bridge.listen_port_for(5), 8905)
            self.assertEqual(serial_bridge.listen_port_for(70005), 8905)
            self.assertEqual(serial_bridge.listen_port_for(9999), 18899)
