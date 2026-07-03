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
