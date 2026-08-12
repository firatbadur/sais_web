"""dashboard uygulaması testleri — mimik editörü API'si ve yerleşik şablon.

Kapsam, sessizce bozulan ve elle tıklamayla kolay atlanan yerlere odaklı:

1. **Kayıt API'si sözleşmesi** (`api_mimic_save`): ad zorunlu, `data` sözlük
   olmalı, boyut sınırı anlaşılır bir 413 döndürmeli, yetki rol=1.
2. **Yerleşik şablonun gerçekten yüklenebilir olması.** Şablon Python'da
   üretiliyor ve Fabric metin objeleri `styles` anahtarı olmadan yazıldığında
   editörde HER `toObject()` çağrısı patlıyordu (kaydet/export/undo) — şablon
   editörde hiç açılamıyordu. Bu test o regresyonu kilitler.
3. **Görüntüleyici/editör yetki ve 404 davranışı.**
"""
import json

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from io import StringIO

from api.events import clear_logtype_cache
from dashboard.models import MimicScreen
from users.models import CustomUser


class MimicTestCase(TestCase):
    """Ortak taban: bayat ``LogType`` önbelleğini temizler.

    Dashboard view/API'leri denetim kaydı yazar (``log_event``) ve ``LogType``
    satırlarını process-local önbellekte **PK ile** tutar. Her ``TestCase``
    işlemi geri aldığı için önceki testten kalan PK geçersizleşir ve FK kısıtı
    erteli kontrolde patlar — testin kendi mantığıyla ilgisi olmayan bir hata.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="t_admin", password="x9Zq!Test", rol=1)
        cls.operator = CustomUser.objects.create_user(
            username="t_oper", password="x9Zq!Test", rol=2)
        cls.viewer = CustomUser.objects.create_user(
            username="t_user", password="x9Zq!Test", rol=3)

    def setUp(self):
        super().setUp()
        clear_logtype_cache()

    def screen(self, **over):
        kwargs = {"name": "T", "data": {"version": "5.3.0", "objects": []}}
        kwargs.update(over)
        return MimicScreen.objects.create(**kwargs)

    def save_payload(self, payload):
        return self.client.post(reverse("dashboard:api_mimic_save"),
                                data=json.dumps(payload),
                                content_type="application/json")


class MimicSaveApiTests(MimicTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_and_update(self):
        r = self.save_payload({"name": "Yeni", "data": {"objects": []}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        pk = r.json()["id"]
        r = self.save_payload({"id": pk, "name": "Yeni (v2)", "data": {"objects": [], "v": 2}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(MimicScreen.objects.get(pk=pk).name, "Yeni (v2)")

    def test_name_required(self):
        self.assertEqual(self.save_payload({"name": "  ", "data": {}}).status_code, 400)

    def test_data_must_be_dict(self):
        self.assertEqual(self.save_payload({"name": "X", "data": "düz-metin"}).status_code, 400)

    def test_unknown_id_404(self):
        r = self.save_payload({"id": 999999, "name": "X", "data": {}})
        self.assertEqual(r.status_code, 404)

    def test_oversize_data_rejected_with_413(self):
        """Anlaşılır Türkçe 413 — Django'nun opak 400'ü yerine."""
        big = {"blob": "x" * (2 * 1024 * 1024 + 5000)}
        r = self.save_payload({"name": "Büyük", "data": big})
        self.assertEqual(r.status_code, 413)
        self.assertIn("2 MB", r.json()["error"])

    def test_non_admin_cannot_save(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.save_payload({"name": "X", "data": {}}).status_code, 403)

    def test_builtin_template_cannot_be_deleted(self):
        m = self.screen(name="Yerleşik", is_template=True)
        r = self.client.post(reverse("dashboard:api_mimic_delete"),
                             data=json.dumps({"id": m.pk}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(MimicScreen.objects.filter(pk=m.pk).exists())


class MimicPageTests(MimicTestCase):
    def test_gallery_and_editor_admin_only(self):
        self.client.force_login(self.admin)
        for name in ("dashboard:admin_mimic", "dashboard:mimic_editor_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.client.force_login(self.operator)
        for name in ("dashboard:admin_mimic", "dashboard:mimic_editor_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_viewer_open_to_all_roles(self):
        m = self.screen()
        for user in (self.admin, self.operator, self.viewer):
            self.client.force_login(user)
            r = self.client.get(reverse("dashboard:mimic_viewer", args=[m.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertTemplateUsed(r, "dashboard/mimic/viewer.html")

    def test_viewer_missing_screen_404(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("dashboard:mimic_viewer", args=[999999]))
        self.assertEqual(r.status_code, 404)

    def test_menu_endpoint_open_to_all_roles(self):
        self.screen()
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("dashboard:api_mimic_menu"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["results"])


class MimicSeedTemplateTests(MimicTestCase):
    """Yerleşik şablon: idempotent + Fabric'in YÜKLEYEBİLECEĞİ biçimde."""

    def test_seed_is_idempotent(self):
        call_command("seed_mimic_templates", stdout=StringIO())
        call_command("seed_mimic_templates", stdout=StringIO())
        self.assertEqual(MimicScreen.objects.filter(is_template=True).count(), 1)

    def test_text_objects_have_styles(self):
        """REGRESYON: `styles` anahtarı olmayan metin objeleri Fabric 5.3'te
        yüklendikten sonra HER `toObject()` çağrısını patlatır (kaydet/export/
        undo) — şablon editörde hiç açılamıyordu."""
        call_command("seed_mimic_templates", stdout=StringIO())
        doc = MimicScreen.objects.get(is_template=True).data
        texts = [o for o in doc["objects"] if "text" in str(o.get("type", ""))]
        self.assertTrue(texts, "şablonda metin objesi olmalı")
        missing = [o.get("name") for o in texts if o.get("styles") is None]
        self.assertEqual(missing, [], "tüm metin objelerinde `styles` bulunmalı")
