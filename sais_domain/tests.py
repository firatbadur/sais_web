"""sais_domain birim testleri."""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone


class CabinetSecretEncryptionTests(TestCase):
    """SaisCabinet.auth_secret at-rest Fernet şifrelemesi."""

    def _cabinet(self, secret):
        from api.models import Station
        from sais_domain.models import SaisCabinet
        station = Station.objects.create(name="Test Tesis")
        return SaisCabinet.objects.create(
            station=station, device_id="SIM1", code="30060001",
            name="Kabin", auth_username="kullanici", auth_secret=secret,
        )

    def test_roundtrip_plaintext_in_python(self):
        from sais_domain.models import SaisCabinet
        cab = self._cabinet("GizliSifre!123")
        self.assertEqual(SaisCabinet.objects.get(pk=cab.pk).auth_secret, "GizliSifre!123")

    def test_stored_ciphertext_encrypted(self):
        cab = self._cabinet("GizliSifre!123")
        with connection.cursor() as cur:
            cur.execute("SELECT auth_secret FROM sais_cabinet WHERE id=%s", [cab.pk])
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith("fernet:"), raw)
        self.assertNotIn("GizliSifre", raw)


class ParseMissingDatesTests(TestCase):
    """`resend_missing_data` job'ının Bakanlık tarih listesi parse'ı."""

    def _parse(self, raw):
        from sais_domain.tasks import _parse_missing_dates
        return _parse_missing_dates(raw)

    def test_parses_naive_iso_to_aware_sorted_unique(self):
        now = timezone.localtime()
        a = (now - timedelta(hours=2)).replace(second=0, microsecond=0)
        b = (now - timedelta(hours=1)).replace(second=0, microsecond=0)
        raw = [
            b.strftime("%Y-%m-%dT%H:%M:00"),
            a.strftime("%Y-%m-%dT%H:%M:00"),
            a.strftime("%Y-%m-%dT%H:%M:00"),  # tekrar — tekilleşmeli
        ]
        out = self._parse(raw)
        self.assertEqual(len(out), 2)  # dedupe
        self.assertTrue(all(timezone.is_aware(dt) for dt in out))
        self.assertLess(out[0], out[1])  # en eski -> en yeni

    def test_drops_older_than_48h_and_invalid(self):
        now = timezone.localtime()
        old = (now - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:00")
        recent = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:00")
        out = self._parse([old, recent, "", "not-a-date", None])
        self.assertEqual(len(out), 1)

    def test_empty_input(self):
        self.assertEqual(self._parse([]), [])
