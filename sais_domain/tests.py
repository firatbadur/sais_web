"""sais_domain birim testleri."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone


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
