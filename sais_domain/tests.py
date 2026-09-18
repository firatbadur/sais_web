"""sais_domain birim testleri."""
import json
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.urls import reverse
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


class SimErrorClassifyTests(TestCase):
    """`sais_domain.sim_errors` — kabul / geçici hata / kalıcı ret ayrımı."""

    def _classify(self, **kw):
        from sais_domain import sim_errors
        return sim_errors.classify_send_result(**kw)

    def test_ministry_rejection_is_permanent(self):
        """REGRESYON: ret zarfı eskiden BAŞARI sayılıyordu.

        `_unwrap` "objects" anahtarını görüp içeriğini (null) döndürdüğü için
        çağıran `result: false`'u hiç göremiyordu.
        """
        from sais_domain import sim_errors
        outcome, category, message = self._classify(
            envelope={"result": False, "message": "Istasyon bulunamadi", "objects": None},
        )
        self.assertEqual(outcome, sim_errors.PERMANENT)
        self.assertEqual(category, sim_errors.REJECTED)
        self.assertIn("bulunamadi", message)

    def test_success_envelope_accepted(self):
        from sais_domain import sim_errors
        for env in ({"result": True}, {"result": True, "objects": None}, {}, None, []):
            outcome, _, _ = self._classify(envelope=env)
            self.assertEqual(outcome, sim_errors.ACCEPTED, env)

    def test_timeout_and_conn_are_retriable(self):
        import requests

        from sais_domain import sim_errors
        for exc, expected in (
            (requests.exceptions.ReadTimeout("timed out"), sim_errors.TIMEOUT),
            (requests.exceptions.ConnectTimeout("timed out"), sim_errors.TIMEOUT),
            (requests.exceptions.ConnectionError("Connection refused"), sim_errors.CONN_ERROR),
        ):
            outcome, category, _ = self._classify(exception=exc)
            self.assertEqual(outcome, sim_errors.RETRIABLE)
            self.assertEqual(category, expected)

    def test_http_status_split(self):
        from sais_domain import sim_errors
        cases = {
            503: (sim_errors.RETRIABLE, sim_errors.HTTP_5XX),
            500: (sim_errors.RETRIABLE, sim_errors.HTTP_5XX),
            429: (sim_errors.RETRIABLE, sim_errors.THROTTLED),
            401: (sim_errors.RETRIABLE, sim_errors.AUTH),
            400: (sim_errors.PERMANENT, sim_errors.HTTP_4XX),
            404: (sim_errors.PERMANENT, sim_errors.HTTP_4XX),
        }
        for status, (exp_outcome, exp_cat) in cases.items():
            outcome, category, _ = self._classify(
                exception=RuntimeError("x"), http_status=status,
            )
            self.assertEqual((outcome, category), (exp_outcome, exp_cat), status)

    def test_blocks_queue_only_for_retriable(self):
        from sais_domain import sim_errors
        self.assertTrue(sim_errors.blocks_queue(sim_errors.TIMEOUT))
        self.assertFalse(sim_errors.blocks_queue(sim_errors.REJECTED))
        self.assertFalse(sim_errors.blocks_queue(sim_errors.OK))


class OutboxBackoffTests(TestCase):
    """Kuyruk backoff merdiveni: 30/60/120/... tavanda sabitlenir."""

    def test_ladder_and_cap(self):
        from sais_domain import sim_outbox
        # ±%10 jitter var → aralık kontrolü.
        for attempt, expected in ((1, 30), (2, 60), (3, 120), (4, 240)):
            val = sim_outbox.backoff_seconds(attempt)
            self.assertGreaterEqual(val, int(expected * 0.9) - 1)
            self.assertLessEqual(val, int(expected * 1.1) + 1)

    def test_cap_respected(self):
        from sais_domain import sim_outbox
        self.assertLessEqual(sim_outbox.backoff_seconds(20), int(600 * 1.1) + 1)
        # Kalıcı ret için tavan daha kısa.
        self.assertLessEqual(sim_outbox.backoff_seconds(20, rejected=True),
                             int(60 * 1.1) + 1)


class _FakeClient:
    """`SaisSimClient` yerine geçen sahte istemci — gönderim sonuçlarını sıraya koyar."""

    def __init__(self, results):
        self.results = list(results)
        self.sent = []

    def send_data(self, *, readtime, values, period=1):
        self.sent.append(readtime)
        result = self.results.pop(0) if self.results else {"result": True}
        if isinstance(result, Exception):
            raise result
        return result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SimOutboxTestBase(TestCase):
    def setUp(self):
        from api.models import Station
        from sais_domain.models import SaisCabinet, SystemSwitch

        self.station = Station.objects.create(name="Test Tesis", active=True)
        self.cabinet = SaisCabinet.objects.create(
            station=self.station, device_id="SIM1", code="30060001",
            name="Kabin", auth_username="u", auth_secret="s",
        )
        SystemSwitch.objects.filter(pk=1).delete()
        self.switch = SystemSwitch.load()
        self.switch.sim_enabled = True
        self.switch.save()

    def _enqueue(self, minutes_ago, priority=None, **kw):
        from sais_domain.models import SimOutboxEntry
        priority = SimOutboxEntry.PRIORITY_LIVE if priority is None else priority
        rt = (timezone.now() - timedelta(minutes=minutes_ago)).replace(
            second=0, microsecond=0)
        entry, _ = SimOutboxEntry.enqueue(
            self.cabinet, readtime=rt, payload={"pH": 7.4, "pH_Status": 1},
            period=1, priority=priority, **kw)
        return entry

    def _drain(self, results, **kw):
        from unittest.mock import patch

        from sais_domain import sim_outbox
        client = _FakeClient(results)
        with patch.object(sim_outbox, "SaisSimClient", return_value=client):
            stats = sim_outbox.drain_cabinet(self.cabinet, use_lock=False, **kw)
        return stats, client


class SimOutboxEnqueueTests(SimOutboxTestBase):
    """Kuyruğa yazma: mükerrer koruması + dondurulmuş payload."""

    def test_same_minute_enqueued_once(self):
        from sais_domain.models import SimOutboxEntry
        e1 = self._enqueue(0)
        e2 = self._enqueue(0)
        self.assertIsNotNone(e1)
        self.assertEqual(SimOutboxEntry.objects.count(), 1)
        self.assertEqual(e2.pk, e1.pk)

    def test_minute_can_be_requeued_after_sent(self):
        """Bakanlık iletilmiş bir dakikayı 'eksik' bildirirse yeniden alınabilmeli."""
        from sais_domain.models import SimOutboxEntry
        e1 = self._enqueue(5)
        SimOutboxEntry.objects.filter(pk=e1.pk).update(
            status=SimOutboxEntry.STATUS_SENT)
        self._enqueue(5, priority=SimOutboxEntry.PRIORITY_BACKFILL)
        self.assertEqual(SimOutboxEntry.objects.count(), 2)

    def test_readtime_is_minute_truncated(self):
        entry = self._enqueue(0)
        self.assertEqual(entry.readtime.second, 0)
        self.assertEqual(entry.readtime.microsecond, 0)


class SimOutboxDrainTests(SimOutboxTestBase):
    """Drenaj: sıkı FIFO + baş tıkanması + kaçış valfleri."""

    def test_retriable_error_blocks_following_minutes(self):
        """ASIL GEREKSİNİM: hata alınca sonraki dakikalar GÖNDERİLMEZ."""
        import requests

        from sais_domain.models import SimOutboxEntry
        oldest = self._enqueue(3)
        self._enqueue(2)
        self._enqueue(1)

        stats, client = self._drain([requests.exceptions.ReadTimeout("timed out")])

        # Yalnız baştaki kayıt denendi; sonrakilere hiç dokunulmadı.
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.sent[0], oldest.readtime_str)
        self.assertTrue(stats["blocked"])
        self.assertEqual(stats["sent"], 0)

        oldest.refresh_from_db()
        self.assertEqual(oldest.status, SimOutboxEntry.STATUS_PENDING)
        self.assertEqual(oldest.attempts, 1)
        self.assertIsNotNone(oldest.first_error_at)
        self.assertGreater(oldest.next_attempt_at, timezone.now())
        # Diğerleri hiç denenmemiş olmalı.
        others = SimOutboxEntry.objects.exclude(pk=oldest.pk)
        self.assertTrue(all(o.attempts == 0 for o in others))

    def test_drains_in_readtime_order_after_recovery(self):
        from sais_domain.models import SimOutboxEntry
        for m in (3, 2, 1):
            self._enqueue(m)
        stats, client = self._drain([{"result": True}] * 3)
        self.assertEqual(stats["sent"], 3)
        self.assertEqual(client.sent, sorted(client.sent))
        self.assertEqual(
            SimOutboxEntry.objects.filter(status=SimOutboxEntry.STATUS_SENT).count(), 3)

    def test_mark_sim_success_only_on_acceptance(self):
        """REGRESYON: ret alınca 'SİM'e son iletim' damgası atılmamalı."""
        from sais_domain.models import SimOutboxEntry, SystemSwitch
        self._enqueue(1)
        self._drain([{"result": False, "message": "reddedildi", "objects": None}])
        self.assertIsNone(SystemSwitch.load().last_sim_success_at)

        # Ret sonrası kayıt backoff'a girdi; ikinci denemeyi hemen yapabilmek
        # için vadeyi öne çek (gerçekte backoff süresi beklenir).
        SimOutboxEntry.objects.all().update(next_attempt_at=timezone.now())
        self._drain([{"result": True}])
        self.assertIsNotNone(SystemSwitch.load().last_sim_success_at)

    def test_rejection_fails_after_max_attempts_and_advances(self):
        from django.test import override_settings

        from sais_domain.models import SimOutboxEntry
        first = self._enqueue(2)
        second = self._enqueue(1)
        reject = {"result": False, "message": "gecersiz", "objects": None}

        with override_settings(SAIS_SIM_OUTBOX_MAX_ATTEMPTS=2,
                               SAIS_SIM_OUTBOX_POISON_STREAK=0):
            # 1. deneme → bloke (henüz sınır dolmadı)
            self._drain([reject])
            first.refresh_from_db()
            self.assertEqual(first.status, SimOutboxEntry.STATUS_PENDING)
            self.assertEqual(first.attempts, 1)
            self.assertEqual(second.attempts if second else 0, 0)

            # Backoff'u sıfırla, 2. deneme → failed + kuyruk ilerler
            SimOutboxEntry.objects.filter(pk=first.pk).update(
                next_attempt_at=timezone.now())
            self._drain([reject, {"result": True}])

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, SimOutboxEntry.STATUS_FAILED)
        self.assertEqual(second.status, SimOutboxEntry.STATUS_SENT)

    def test_entry_past_accept_window_expires_and_advances(self):
        from sais_domain.models import SimOutboxEntry
        old = self._enqueue(60 * 60)   # 60 saat önce → 48 saatlik pencerenin dışında
        fresh = self._enqueue(1)
        stats, client = self._drain([{"result": True}])
        old.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(old.status, SimOutboxEntry.STATUS_EXPIRED)
        self.assertEqual(fresh.status, SimOutboxEntry.STATUS_SENT)
        self.assertEqual(stats["expired"], 1)

    def test_accept_window_is_operator_configurable(self):
        """Bakanlık pencereyi genişletirse eski veri süresi dolmuş sayılmamalı."""
        from sais_domain.models import SimOutboxEntry, SystemSwitch
        self.switch.sim_accept_window_hours = 96
        self.switch.save()
        old = self._enqueue(60 * 60)   # 60 saat — 96 saatlik pencerenin İÇİNDE
        stats, client = self._drain([{"result": True}])
        old.refresh_from_db()
        self.assertEqual(old.status, SimOutboxEntry.STATUS_SENT)
        self.assertEqual(stats["expired"], 0)
        self.assertEqual(SystemSwitch.load().accept_window_hours(), 96)

    def test_live_lane_drains_before_backfill(self):
        from sais_domain.models import SimOutboxEntry
        backfill = self._enqueue(120, priority=SimOutboxEntry.PRIORITY_BACKFILL)
        live = self._enqueue(1)
        _, client = self._drain([{"result": True}, {"result": True}])
        self.assertEqual(client.sent[0], live.readtime_str)
        self.assertEqual(client.sent[1], backfill.readtime_str)

    def test_blocked_live_lane_stops_backfill_too(self):
        import requests

        from sais_domain.models import SimOutboxEntry
        self._enqueue(120, priority=SimOutboxEntry.PRIORITY_BACKFILL)
        self._enqueue(1)
        _, client = self._drain([requests.exceptions.ReadTimeout("timed out")])
        self.assertEqual(len(client.sent), 1)

    def test_duplicate_message_counts_as_accepted(self):
        """Mükerrer gönderim (yeniden teslim) kuyruğu zehirlememeli."""
        from sais_domain.models import SimOutboxEntry
        entry = self._enqueue(1)
        self._drain([{"result": False, "message": "Tekrar veri", "objects": None}])
        entry.refresh_from_db()
        self.assertEqual(entry.status, SimOutboxEntry.STATUS_SENT)

    def test_lock_prevents_concurrent_drain(self):
        from django.core.cache import cache

        from sais_domain import sim_outbox
        self._enqueue(1)
        cache.set(f"sais:sim:drain:{self.cabinet.id}", "other", 60)
        try:
            stats = sim_outbox.drain_cabinet(self.cabinet)
            self.assertEqual(stats.get("skipped"), "locked")
        finally:
            cache.delete(f"sais:sim:drain:{self.cabinet.id}")

    def test_reaper_recovers_stuck_sending_entry(self):
        from sais_domain import sim_outbox
        from sais_domain.models import SimOutboxEntry
        entry = self._enqueue(1)
        SimOutboxEntry.objects.filter(pk=entry.pk).update(
            status=SimOutboxEntry.STATUS_SENDING,
            claimed_at=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(sim_outbox.reap_stuck_entries(), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, SimOutboxEntry.STATUS_PENDING)

    def test_cabinets_are_isolated(self):
        """Bir kabinin tıkanması diğerini durdurmamalı."""
        import requests

        from unittest.mock import patch

        from sais_domain import sim_outbox
        from sais_domain.models import SaisCabinet, SimOutboxEntry

        other = SaisCabinet.objects.create(
            station=self.station, device_id="SIM2", code="30060002",
            name="Kabin2", auth_username="u", auth_secret="s")
        blocked_entry = self._enqueue(1)
        rt = (timezone.now() - timedelta(minutes=1)).replace(second=0, microsecond=0)
        other_entry, _ = SimOutboxEntry.enqueue(
            other, readtime=rt, payload={"pH": 7.0, "pH_Status": 1})

        with patch.object(sim_outbox, "SaisSimClient",
                          return_value=_FakeClient([requests.exceptions.ReadTimeout("t")])):
            sim_outbox.drain_cabinet(self.cabinet, use_lock=False)
        with patch.object(sim_outbox, "SaisSimClient",
                          return_value=_FakeClient([{"result": True}])):
            sim_outbox.drain_cabinet(other, use_lock=False)

        blocked_entry.refresh_from_db()
        other_entry.refresh_from_db()
        self.assertEqual(blocked_entry.status, SimOutboxEntry.STATUS_PENDING)
        self.assertEqual(other_entry.status, SimOutboxEntry.STATUS_SENT)


class SimOutboxProducerTests(SimOutboxTestBase):
    """Üretici: gönderme, kuyruğa yaz + dakikayı planlanan ana sabitle."""

    def _sensor(self):
        from api.models import Connection, Parameter, Sensor, SensorLatest
        conn = Connection.objects.create(
            station=self.station, name="C1", protocol="modbus_tcp",
            host="127.0.0.1", port=502)
        param = Parameter.objects.create(parameter_name="pH", parameter_txt="pH")
        sensor = Sensor.objects.create(
            connection=conn, parameter=param, sensor_type=0, is_active=True)
        SensorLatest.objects.update_or_create(
            sensor=sensor, defaults={"value": 7.4, "readtime": timezone.now()})
        return sensor

    def test_publish_writes_to_queue_not_network(self):
        from sais_domain.models import SimOutboxEntry
        from sais_domain.tasks import publish_cabinet_data

        self._sensor()
        pinned = timezone.localtime().replace(second=0, microsecond=0) - timedelta(minutes=5)
        publish_cabinet_data(self.cabinet.id, readtime_iso=pinned.isoformat())

        entry = SimOutboxEntry.objects.get()
        # Dakika damgası ÇALIŞMA anı değil, planlanan dakika olmalı.
        self.assertEqual(entry.readtime, pinned)
        self.assertEqual(entry.status, SimOutboxEntry.STATUS_PENDING)
        self.assertIn("pH", entry.payload)

    def test_queue_grows_even_when_sim_disabled(self):
        """SIM kapalıyken de veri kaybolmamalı — kuyruğa yazılır, drenaj durur."""
        from sais_domain.models import SimOutboxEntry, SystemSwitch
        from sais_domain.tasks import publish_cabinet_data

        self._sensor()
        self.switch.sim_enabled = False
        self.switch.save()
        publish_cabinet_data(self.cabinet.id)
        self.assertEqual(SimOutboxEntry.objects.count(), 1)
        self.assertFalse(SystemSwitch.load().sim_enabled)


class SimOutboxPageTests(TestCase):
    """Kuyruk sayfası + AJAX uçları gerçekten render/çalışıyor mu (smoke)."""

    def setUp(self):
        from api.events import clear_logtype_cache
        from api.models import Station
        from sais_domain.models import SaisCabinet, SimOutboxEntry
        from users.models import CustomUser

        # LogType önbelleği TestCase rollback'lerinde bayatlar (bkz.
        # api.events.clear_logtype_cache docstring'i) — log_event FK ile düşer.
        clear_logtype_cache()
        self.station = Station.objects.create(name="Test Tesis", active=True)
        self.cabinet = SaisCabinet.objects.create(
            station=self.station, device_id="SIM1", code="30060001",
            name="Kabin", auth_username="u", auth_secret="s")
        self.admin = CustomUser.objects.create_user(
            username="admin1", password="pw12345!", rol=1)
        self.viewer = CustomUser.objects.create_user(
            username="viewer1", password="pw12345!", rol=3)
        SimOutboxEntry.enqueue(
            self.cabinet,
            readtime=timezone.now().replace(second=0, microsecond=0),
            payload={"pH": 7.4, "pH_Status": 1})

    def test_page_renders_for_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard:admin_sim_outbox"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Gönderim Kuyruğu")

    def test_page_forbidden_for_plain_user(self):
        self.client.force_login(self.viewer)
        resp = self.client.get(reverse("dashboard:admin_sim_outbox"))
        self.assertIn(resp.status_code, (302, 403))

    def test_status_endpoint(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard:api_sim_outbox_status"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["totals"]["pending"], 1)
        self.assertEqual(data["accept_window_hours"], 48)

    def test_skip_action_requires_admin(self):
        from sais_domain.models import SimOutboxEntry
        entry = SimOutboxEntry.objects.get()
        # Operatör değil, normal kullanıcı → reddedilmeli.
        self.client.force_login(self.viewer)
        resp = self.client.post(
            reverse("dashboard:api_sim_outbox_action"),
            data=json.dumps({"action": "skip", "ids": [entry.id]}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.status, SimOutboxEntry.STATUS_PENDING)

    def test_admin_can_skip_head(self):
        from sais_domain.models import SimOutboxEntry
        entry = SimOutboxEntry.objects.get()
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("dashboard:api_sim_outbox_action"),
            data=json.dumps({"action": "skip", "ids": [entry.id]}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.status, SimOutboxEntry.STATUS_SKIPPED)

    def test_system_control_page_and_status_include_queue(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard:admin_system_control"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "sim_accept_window_hours")

        resp = self.client.get(reverse("dashboard:api_system_control_status"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["outbox_pending"], 1)
        self.assertEqual(data["outbox_accept_window_hours"], 48)

    def test_accept_window_saved_from_system_control(self):
        from sais_domain.models import SystemSwitch
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("dashboard:admin_system_control"), {
            "action": "save_accept_window",
            "sim_accept_window_hours": "72",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SystemSwitch.load().sim_accept_window_hours, 72)


class SimChannelSelectionTests(TestCase):
    """Bakanlık'a iletilecek kolon seçimi (``SimChannel``).

    Regresyon bağlamı (saha, 2026-09-18): payload'da Bakanlık tanımında
    olmayan tek bir kolon ("Sicaklik") bulunması SendData'nın **tamamının**
    reddedilmesine yol açıyordu. Seçim dışı bırakılan parametre payload'a
    ne değer ne de ``_Status`` anahtarı olarak girmelidir.
    """

    def setUp(self):
        from api.models import (
            Connection, Parameter, Sensor, SensorLatest, Station,
        )
        from sais_domain.models import SaisCabinet

        self.station = Station.objects.create(name="Test Tesis", active=True)
        self.cabinet = SaisCabinet.objects.create(
            station=self.station, device_id="SIM1", code="30060001",
            name="Kabin", auth_username="u", auth_secret="s",
        )
        self.conn = Connection.objects.create(
            station=self.station, name="PLC", protocol="modbus_tcp",
            host="127.0.0.1", port=502, is_enabled=True,
        )
        self.sensors = {}
        for ad in ("Debi", "Sicaklik"):
            param = Parameter.objects.create(
                parameter_name=ad, parameter_txt=ad, station=self.station,
            )
            sensor = Sensor.objects.create(
                connection=self.conn, parameter=param, sensor_type=0,
                is_active=True, address=1,
            )
            SensorLatest.objects.update_or_create(
                sensor=sensor,
                defaults={"value": 1.5, "readtime": timezone.now()},
            )
            self.sensors[ad] = sensor

    def _payload(self):
        from sais_domain.services import build_sim_payload
        return build_sim_payload(self.cabinet, readtime=timezone.now()).values

    def test_no_selection_sends_everything(self):
        """Hiç kayıt yoksa davranış değişmez — geriye uyumluluk."""
        values = self._payload()
        self.assertIn("Debi", values)
        self.assertIn("Sicaklik", values)

    def test_disabled_parameter_is_omitted(self):
        """Kapatılan kolon ne değer ne status olarak yazılır."""
        from sais_domain.models import SimChannel

        SimChannel.objects.create(
            cabinet=self.cabinet,
            parameter=self.sensors["Debi"].parameter, is_enabled=True,
        )
        SimChannel.objects.create(
            cabinet=self.cabinet,
            parameter=self.sensors["Sicaklik"].parameter, is_enabled=False,
        )
        values = self._payload()
        self.assertIn("Debi", values)
        self.assertIn("Debi_Status", values)
        self.assertNotIn("Sicaklik", values)
        self.assertNotIn("Sicaklik_Status", values)

    def test_enabled_names_none_when_unconfigured(self):
        """``None`` (yapılandırılmamış) ile boş küme (hiçbiri) ayrı anlamlar."""
        from sais_domain.models import SimChannel

        self.assertIsNone(SimChannel.enabled_parameter_names(self.cabinet))
        SimChannel.objects.create(
            cabinet=self.cabinet,
            parameter=self.sensors["Debi"].parameter, is_enabled=False,
        )
        self.assertEqual(SimChannel.enabled_parameter_names(self.cabinet), set())

    def test_backfill_uses_same_selection(self):
        """Eksik veri yeniden gönderimi canlı payload ile aynı kolonları yazar."""
        from api.models import Reading
        from sais_domain.models import SimChannel
        from sais_domain.services import build_sim_payloads_for_times

        SimChannel.objects.create(
            cabinet=self.cabinet,
            parameter=self.sensors["Sicaklik"].parameter, is_enabled=False,
        )
        SimChannel.objects.create(
            cabinet=self.cabinet,
            parameter=self.sensors["Debi"].parameter, is_enabled=True,
        )
        hedef = (timezone.now() - timedelta(minutes=5)).replace(second=0, microsecond=0)
        for ad in ("Debi", "Sicaklik"):
            Reading.objects.create(
                sensor=self.sensors[ad], value=2.0,
                time_iso=hedef + timedelta(seconds=10),
            )
        payloads = build_sim_payloads_for_times(self.cabinet, [hedef])
        self.assertTrue(payloads)
        values = payloads[0].values
        self.assertIn("Debi", values)
        self.assertNotIn("Sicaklik", values)
