"""Tesis Sanal Turu testleri — birleştirme geometrisi, yetki, fotoğraf listesi, AI ayrıştırma.

`dashboard/tests.py` modülüne aktarılır (Django test keşfi `tests.py`'yi bulur).
"""
import json
import math
from unittest import mock

import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from api.events import clear_logtype_cache
from api.models import Connection, Parameter, Sensor, Station
from dashboard import tour_ai
from dashboard import tour_stitch as ts
from dashboard.models import EquipmentPhoto, TourFrame, TourHotspot, TourScene
from users.models import CustomUser


def _equirect(w=512):
    rng = np.random.default_rng(7)
    small = rng.integers(0, 255, (16, 32, 3), dtype=np.uint8)
    return ts.cv2.resize(small, (w, w // 2), interpolation=ts.cv2.INTER_CUBIC)


def _render(src, yaw, pitch, roll, hfov, iw=320, ih=240):
    """Equirect kaynaktan kamera karesi üret (birleştirmenin tersi — test yardımcısı)."""
    H, W = src.shape[:2]
    r, u, f = ts.camera_basis(yaw, pitch, roll)
    fl = (iw / 2) / math.tan(math.radians(hfov) / 2)
    xs, ys = np.meshgrid(np.arange(iw) + .5, np.arange(ih) + .5)
    d = (f[None, None, :] + ((xs - iw / 2) / fl)[..., None] * r
         + (-(ys - ih / 2) / fl)[..., None] * u)
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    lon, lat = np.arctan2(d[..., 0], d[..., 1]), np.arcsin(d[..., 2])
    mx = ((lon + np.pi) / (2 * np.pi) * W - .5).astype(np.float32)
    my = ((np.pi / 2 - lat) / np.pi * H - .5).astype(np.float32)
    return ts.cv2.remap(src, mx, my, ts.cv2.INTER_LINEAR, borderMode=ts.cv2.BORDER_WRAP)


def _jpeg(w=64, h=48):
    return ts.encode_jpeg(np.full((h, w, 3), 120, dtype=np.uint8))


class TourGeometryTests(SimpleTestCase):
    def test_orientation_stitch_reconstructs_sphere(self):
        """Jiroskop yönelimli karelerden kurulan panorama kaynağı geri üretmeli (eşleştirmesiz)."""
        src = _equirect()
        frames = []
        for pitch, n in ((0, 12), (45, 8), (-45, 8)):
            for i in range(n):
                yaw = i * 360 / n
                frames.append({"img": _render(src, yaw, pitch, 4, 65), "yaw": yaw,
                               "pitch": pitch, "roll": 4, "hfov": 65})
        out = ts.stitch_by_orientation(frames, out_width=512)
        self.assertEqual(out.shape[:2], (256, 512))
        band = slice(64, 192)  # ekvator bandı (kutuplar dolgulu)
        err = np.abs(out[band].astype(int) - src[band].astype(int)).mean()
        self.assertLess(err, 8.0)

    def test_frame_point_mapping(self):
        yaw, pitch = ts.frame_point_to_yaw_pitch(0.5, 0.5, 30, 10, 0, 65, 4 / 3)
        self.assertAlmostEqual(yaw, 30.0, places=4)
        self.assertAlmostEqual(pitch, 10.0, places=4)
        yaw, pitch = ts.frame_point_to_yaw_pitch(1.0, 0.5, 0, 0, 0, 90, 4 / 3)
        self.assertAlmostEqual(yaw, 45.0, places=4)
        self.assertAlmostEqual(pitch, 0.0, places=4)

    def test_strip_panorama_embedded_as_2to1(self):
        strip = np.full((100, 800, 3), 200, dtype=np.uint8)  # 8:1 yatay şerit
        out = ts.prepare_uploaded_panorama(ts.encode_jpeg(strip), 180, out_width=1024)
        h, w = out.shape[:2]
        self.assertEqual(w, 2 * h)

    def test_ai_detections_merge_and_filter(self):
        frames = [{"yaw": 0, "pitch": 0, "roll": 0, "hfov": 60, "aspect": 1.0},
                  {"yaw": 20, "pitch": 0, "roll": 0, "hfov": 60, "aspect": 1.0}]
        base = {"category": "analyzer", "brand": "", "model": "", "x": 0.5, "y": 0.5,
                "sensor_id": 0, "confidence": 0.8}
        dets = [
            dict(base, frame=0, label="pH Analizörü", sensor_id=5, confidence=0.7),
            # Komşu karede aynı ekipman (~aynı yön) → birleşir, yüksek güven kazanır
            dict(base, frame=1, label="pH analizörü", brand="Hach", x=0.2, sensor_id=5, confidence=0.9),
            dict(base, frame=0, label="Belirsiz", confidence=0.1),         # düşük güven → elenir
            dict(base, frame=9, label="Hatalı kare"),                     # geçersiz kare → elenir
            dict(base, frame=0, label="Pompa", x=0.9, y=0.8, sensor_id=99),  # yabancı sensör → None
        ]
        pts = tour_ai.detections_to_points(dets, frames, valid_sensors={5})
        self.assertEqual(len(pts), 2)
        ph = next(p for p in pts if p["sensor_id"] == 5)
        self.assertEqual(ph["brand"], "Hach")
        pump = next(p for p in pts if p["label"] == "Pompa")
        self.assertIsNone(pump["sensor_id"])


class TourApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = CustomUser.objects.create_user(username="tour_op", password="x9Zq!Test", rol=2)
        cls.viewer = CustomUser.objects.create_user(username="tour_user", password="x9Zq!Test", rol=3)
        cls.station = Station.objects.create(name="Tur Tesis")
        conn = Connection.objects.create(station=cls.station, name="plc", protocol="modbus_tcp",
                                         transport="tcp", host="10.0.0.5", is_enabled=True)
        cls.param = Parameter.objects.create(station=cls.station, parameter_name="pH")
        cls.sensor = Sensor.objects.create(connection=conn, parameter=cls.param, sensor_type=0)
        other = Station.objects.create(name="Diğer Tesis")
        oconn = Connection.objects.create(station=other, name="plc2", protocol="modbus_tcp",
                                          transport="tcp", host="10.0.0.6", is_enabled=True)
        cls.foreign_sensor = Sensor.objects.create(connection=oconn, sensor_type=0)

    def setUp(self):
        clear_logtype_cache()  # bkz. dashboard.tests.MimicTestCase

    def post_json(self, name, payload, args=None):
        return self.client.post(reverse(name, args=args), data=json.dumps(payload),
                                content_type="application/json")

    def scenes_json(self):
        url = reverse("dashboard:api_tour_scenes") + "?station=%s" % self.station.pk
        return self.client.get(url).json()

    def make_scene(self, **kw):
        return TourScene.objects.create(station=self.station, name=kw.pop("name", "Kabin İçi"), **kw)

    def upload_photo(self, item_key, shot_key="nameplate"):
        return self.client.post(reverse("dashboard:api_tour_photo_upload"), {
            "station_id": self.station.pk, "item_key": item_key, "shot_key": shot_key,
            "file": SimpleUploadedFile("n.jpg", _jpeg())})

    def test_viewer_role_reads_but_cannot_write(self):
        scene = self.make_scene()
        self.client.force_login(self.viewer)
        self.assertTrue(self.scenes_json()["ok"])
        r = self.post_json("dashboard:api_tour_hotspot_save",
                           {"scene_id": scene.pk, "kind": "note", "yaw": 0, "pitch": 0})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.upload_photo("sampler").status_code, 403)
        r = self.client.get(reverse("dashboard:tour_capture", args=[scene.pk]))
        self.assertEqual(r.status_code, 403)

    def test_hotspot_validation(self):
        scene = self.make_scene()
        self.client.force_login(self.operator)
        r = self.post_json("dashboard:api_tour_hotspot_save",
                           {"scene_id": scene.pk, "kind": "sensor", "yaw": 10, "pitch": 0})
        self.assertEqual(r.status_code, 400)  # sensör türü sensörsüz olamaz
        r = self.post_json("dashboard:api_tour_hotspot_save",
                           {"scene_id": scene.pk, "kind": "sensor", "yaw": 10, "pitch": 0,
                            "sensor_id": self.foreign_sensor.pk})
        self.assertEqual(r.status_code, 400)  # başka tesisin sensörü
        r = self.post_json("dashboard:api_tour_hotspot_save",
                           {"scene_id": scene.pk, "kind": "sensor", "yaw": 10, "pitch": 95,
                            "sensor_id": self.sensor.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(TourHotspot.objects.get(pk=r.json()["hotspot"]["id"]).pitch, 90.0)

    def test_ai_suggestions_hidden_from_viewer_until_confirmed(self):
        scene = self.make_scene()
        TourHotspot.objects.create(scene=scene, kind="equipment", yaw=0, pitch=0, title="AI",
                                   ai_suggested=True, confirmed=False)
        self.client.force_login(self.viewer)
        self.assertEqual(self.scenes_json()["scenes"][0]["hotspots"], [])
        self.client.force_login(self.operator)
        self.assertEqual(len(self.scenes_json()["scenes"][0]["hotspots"]), 1)

    def test_panorama_upload_marks_ready_and_serves_image(self):
        scene = self.make_scene()
        self.client.force_login(self.operator)
        pano = ts.encode_jpeg(np.full((200, 400, 3), 90, dtype=np.uint8))
        r = self.client.post(reverse("dashboard:api_tour_pano_upload", args=[scene.pk]),
                             {"file": SimpleUploadedFile("p.jpg", pano, content_type="image/jpeg")})
        self.assertEqual(r.status_code, 200, r.content)
        scene.refresh_from_db()
        self.assertEqual(scene.status, TourScene.Status.READY)
        self.assertEqual(scene.pano_width, 2 * scene.pano_height)
        img_url = r.json()["scene"]["image_url"]
        img = self.client.get(img_url)
        self.assertEqual(img["Content-Type"], "image/jpeg")
        self.assertEqual(self.client.get(img_url, HTTP_IF_NONE_MATCH=img["ETag"]).status_code, 304)

    def test_frame_upload_is_idempotent_per_seq(self):
        scene = self.make_scene()
        self.client.force_login(self.operator)
        url = reverse("dashboard:api_tour_frame_upload", args=[scene.pk])
        for _ in range(2):  # ağ yeniden denemesi aynı seq'i tekrar yollar
            r = self.client.post(url, {"seq": 3, "yaw": 30, "pitch": 0, "roll": 0, "hfov": 50,
                                       "has_orientation": "1",
                                       "image": SimpleUploadedFile("f.jpg", _jpeg())})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(TourFrame.objects.filter(scene=scene).count(), 1)

    def test_stitch_task_from_frames(self):
        """Yüklenen yönelimli kareler run_stitch ile panoramaya dönüşmeli."""
        from dashboard.tasks import run_stitch

        src = _equirect(256)
        scene = self.make_scene()
        for i in range(12):
            TourFrame.objects.create(scene=scene, seq=i, image=ts.encode_jpeg(_render(src, i * 30, 0, 0, 65)),
                                     yaw=i * 30, pitch=0, roll=0, hfov=65, has_orientation=True)
        with override_settings(TOUR_PANO_WIDTH=512):
            self.assertEqual(run_stitch(scene.pk), "ready")
        scene.refresh_from_db()
        self.assertEqual((scene.pano_width, scene.pano_height, scene.stitch_method), (512, 256, "orientation"))
        self.assertTrue(scene.thumbnail)

    def test_photo_checklist_progress_and_hotspot_link(self):
        self.client.force_login(self.operator)
        item = "sensor:%s" % self.sensor.pk
        self.assertEqual(self.upload_photo(item).status_code, 200)
        self.assertEqual(EquipmentPhoto.objects.get().sensor_id, self.sensor.pk)
        self.assertEqual(self.upload_photo("sensor:%s" % self.foreign_sensor.pk).status_code, 400)

        url = reverse("dashboard:api_tour_photos") + "?station=%s" % self.station.pk
        state = self.client.get(url).json()
        it = next(i for i in state["items"] if i["key"] == item)
        self.assertEqual(it["done_shots"], 1)
        self.assertEqual(state["progress"]["done"], 1)
        self.assertIn("sampler", {i["key"] for i in state["items"]})

        # Sensöre bağlı işaret, o sensörün yakın çekim fotoğraflarını panelde gösterir.
        h = TourHotspot.objects.create(scene=self.make_scene(), kind="sensor", yaw=0, pitch=0,
                                       sensor=self.sensor)
        info = self.client.get(reverse("dashboard:api_tour_hotspot_info", args=[h.pk])).json()
        self.assertEqual(len(info["hotspot"]["photos"]), 1)

    @override_settings(ANTHROPIC_API_KEY="test-key")
    def test_nameplate_reading_fills_only_empty_fields(self):
        ph = EquipmentPhoto.objects.create(station=self.station, item_key="sampler",
                                           shot_key="nameplate", image=_jpeg(), brand="Elle Girilen")
        fake = {"equipment": "Numune alıcı", "brand": "Hach", "model": "AS950",
                "serial_no": "SN-123", "other_text": "230V", "notes": ""}
        with mock.patch.object(tour_ai, "_call", return_value=fake):
            tour_ai.read_nameplate(ph)
        self.assertEqual(ph.brand, "Elle Girilen")  # kullanıcı girdisi ezilmez
        self.assertEqual((ph.model_name, ph.serial_no), ("AS950", "SN-123"))
        self.assertIn("230V", ph.ai_text)

    @override_settings(ANTHROPIC_API_KEY="")
    def test_ai_endpoint_disabled_without_key(self):
        scene = self.make_scene()
        self.client.force_login(self.operator)
        r = self.post_json("dashboard:api_tour_ai_suggest", {}, args=[scene.pk])
        self.assertEqual(r.status_code, 400)

    def test_pages_render(self):
        scene = self.make_scene(status=TourScene.Status.READY)
        self.client.force_login(self.operator)
        st = self.station.pk
        for name, args, q in (("dashboard:tour", None, "?station=%s" % st),
                              ("dashboard:tour", None, "?station=%s&tab=photos" % st),
                              ("dashboard:tour_viewer", [st], ""),
                              ("dashboard:tour_capture", [scene.pk], ""),
                              ("dashboard:tour_photo_capture", [st], "")):
            r = self.client.get(reverse(name, args=args) + q)
            self.assertEqual(r.status_code, 200, name)

    def test_tab_links_are_absolute(self):
        """base.html `<base href="/">` içerir: göreli `?tab=...` linki anasayfaya gider."""
        self.client.force_login(self.operator)
        html = self.client.get(reverse("dashboard:tour") + "?station=%s" % self.station.pk).content.decode()
        self.assertNotIn('href="?', html)
        self.assertIn('href="%s?station=%s&tab=photos"' % (reverse("dashboard:tour"), self.station.pk), html)
