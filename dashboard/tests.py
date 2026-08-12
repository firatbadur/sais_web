"""dashboard uygulaması testleri — mimik (2B/3B) tür yönlendirmesi ve API.

Neden bu kapsam: 3B editörün getirdiği asıl risk **yönlendirme dalları**dır ve
bunlar elle tıklamayla kolayca atlanır. İki değişmez korunur:

1. `MimicScreen.kind` **oluşturmada** yazılır, sonradan DEĞİŞTİRİLEMEZ. Bir tür
   çevrimi, Fabric belgesini `3d` satırında (veya tersini) bırakır ve her okuyucu
   kırılır.
2. Görüntüleyici TEK route'tur; şablonu `kind` seçer. Bu, header "Mimik"
   menüsünü, galeri linklerini ve 2B↔3B çapraz `openMimic` gezinmesini ek kod
   olmadan çalıştıran mekanizmadır — kırılırsa sessizce yanlış renderer açılır.

Three.js davranışı bu yığında ekonomik olarak unit-test edilemez; o taraf
tarayıcı tabanlı süitlerle doğrulanır (bkz. commit mesajları).
"""
import json

from django.test import TestCase
from django.urls import reverse

from api.events import clear_logtype_cache
from dashboard.models import MimicScreen
from users.models import CustomUser


class MimicTestCase(TestCase):
    """Ortak taban: bayat ``LogType`` önbelleğini temizler.

    Dashboard view/API'leri denetim kaydı yazar (``log_event``) ve ``LogType``
    satırlarını process-local önbellekte **PK ile** tutar. Her ``TestCase``
    işlemi geri aldığı için önceki testten kalan PK geçersizleşir ve FK
    kısıtı erteli kontrolde (``SET CONSTRAINTS ALL IMMEDIATE``) patlar —
    testin kendi mantığıyla ilgisi olmayan bir hata. Önbelleği temizlemek
    her testin kendi işleminde satırı yeniden oluşturmasını sağlar.
    """

    def setUp(self):
        super().setUp()
        clear_logtype_cache()


def _screen(kind="2d", **over):
    data = ({"schema": "mimic3d/1", "kind": "3d", "objects": []} if kind == "3d"
            else {"version": "5.3.0", "objects": []})
    kwargs = {"name": "T-" + kind, "kind": kind, "data": data}
    kwargs.update(over)
    return MimicScreen.objects.create(**kwargs)


class MimicKindModelTests(MimicTestCase):
    def test_default_kind_is_2d(self):
        m = MimicScreen.objects.create(name="Varsayılan")
        self.assertEqual(m.kind, MimicScreen.KIND_2D)
        self.assertFalse(m.is_3d)

    def test_is_3d_property(self):
        self.assertTrue(_screen("3d").is_3d)
        self.assertFalse(_screen("2d").is_3d)


class MimicApiBaseTest(MimicTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="t_admin", password="x9Zq!Test", rol=1)
        cls.operator = CustomUser.objects.create_user(
            username="t_oper", password="x9Zq!Test", rol=2)
        cls.viewer = CustomUser.objects.create_user(
            username="t_user", password="x9Zq!Test", rol=3)

    def save_payload(self, payload):
        return self.client.post(reverse("dashboard:api_mimic_save"),
                                data=json.dumps(payload),
                                content_type="application/json")


class MimicSaveApiTests(MimicApiBaseTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_with_kind_3d(self):
        r = self.save_payload({"name": "Yeni 3B", "kind": "3d",
                               "data": {"schema": "mimic3d/1", "objects": []}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(MimicScreen.objects.get(pk=r.json()["id"]).kind, "3d")

    def test_create_without_kind_defaults_2d(self):
        """2B editör `kind` göndermese de mevcut kayıt yolu bozulmamalı."""
        r = self.save_payload({"name": "Yeni 2B", "data": {"objects": []}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(MimicScreen.objects.get(pk=r.json()["id"]).kind, "2d")

    def test_kind_is_immutable(self):
        m = _screen("3d")
        r = self.save_payload({"id": m.pk, "name": m.name, "kind": "2d",
                               "data": {"objects": []}})
        self.assertEqual(r.status_code, 400)
        m.refresh_from_db()
        self.assertEqual(m.kind, "3d", "tür değiştirilememeli")

    def test_same_kind_update_allowed(self):
        m = _screen("3d")
        r = self.save_payload({"id": m.pk, "name": m.name, "kind": "3d",
                               "data": {"schema": "mimic3d/1", "objects": [], "v": 2}})
        self.assertEqual(r.status_code, 200)

    def test_data_must_be_dict(self):
        r = self.save_payload({"name": "Bozuk", "data": "düz-metin"})
        self.assertEqual(r.status_code, 400)

    def test_oversize_data_rejected_with_413(self):
        big = {"blob": "x" * (2 * 1024 * 1024 + 5000)}
        r = self.save_payload({"name": "Büyük", "kind": "3d", "data": big})
        self.assertEqual(r.status_code, 413)
        self.assertIn("2 MB", r.json()["error"])

    def test_non_admin_cannot_save(self):
        self.client.force_login(self.viewer)
        r = self.save_payload({"name": "X", "data": {}})
        self.assertEqual(r.status_code, 403)


class MimicViewerDispatchTests(MimicApiBaseTest):
    """TEK viewer route'u, kind'a göre şablon — çapraz gezinmenin dayanağı."""

    def test_2d_uses_fabric_template(self):
        self.client.force_login(self.admin)
        m = _screen("2d")
        r = self.client.get(reverse("dashboard:mimic_viewer", args=[m.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "dashboard/mimic/viewer.html")

    def test_3d_uses_three_template(self):
        self.client.force_login(self.admin)
        m = _screen("3d")
        r = self.client.get(reverse("dashboard:mimic_viewer", args=[m.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "dashboard/mimic/viewer3d.html")

    def test_missing_screen_404(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("dashboard:mimic_viewer", args=[999999]))
        self.assertEqual(r.status_code, 404)

    def test_role3_can_view_both_kinds(self):
        """Görüntüleyici tüm rollere açık (izleme); CRUD admin'e kapalı."""
        self.client.force_login(self.viewer)
        for kind, tpl in (("2d", "dashboard/mimic/viewer.html"),
                          ("3d", "dashboard/mimic/viewer3d.html")):
            m = _screen(kind, name="V-" + kind)
            r = self.client.get(reverse("dashboard:mimic_viewer", args=[m.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertTemplateUsed(r, tpl)


class MimicEditorRedirectTests(MimicApiBaseTest):
    """Çapraz-tür koruması: yanlış editöre açılan kayıt yönlendirilir.

    Bu koruma olmadan eski bir bookmark 3B belgesini Fabric editörüne yükler ve
    kayıtta belge sessizce Fabric JSON'una EZİLİR (geri dönüşü olmayan kayıp).
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_2d_editor_redirects_3d_screen(self):
        m = _screen("3d")
        r = self.client.get(reverse("dashboard:mimic_editor_edit", args=[m.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"],
                         reverse("dashboard:mimic3d_editor_edit", args=[m.pk]))

    def test_3d_editor_redirects_2d_screen(self):
        m = _screen("2d")
        r = self.client.get(reverse("dashboard:mimic3d_editor_edit", args=[m.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"],
                         reverse("dashboard:mimic_editor_edit", args=[m.pk]))

    def test_matching_kind_renders(self):
        m3 = _screen("3d")
        r = self.client.get(reverse("dashboard:mimic3d_editor_edit", args=[m3.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "dashboard/mimic/editor3d.html")
        m2 = _screen("2d", name="E-2d")
        r = self.client.get(reverse("dashboard:mimic_editor_edit", args=[m2.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "dashboard/mimic/editor.html")

    def test_new_3d_editor_renders(self):
        r = self.client.get(reverse("dashboard:mimic3d_editor_new"))
        self.assertEqual(r.status_code, 200)

    def test_operator_forbidden(self):
        """Editörler rol=1 (AdminRequiredMixin)."""
        self.client.force_login(self.operator)
        for url in (reverse("dashboard:mimic3d_editor_new"),
                    reverse("dashboard:mimic_editor_new")):
            self.assertEqual(self.client.get(url).status_code, 403)


class MimicPayloadTests(MimicApiBaseTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.m2 = _screen("2d", name="P-2d")
        self.m3 = _screen("3d", name="P-3d")

    def test_menu_includes_kind_and_is_open_to_all_roles(self):
        r = self.client.get(reverse("dashboard:api_mimic_menu"))
        items = r.json()["results"]
        self.assertTrue(items)
        self.assertTrue(all("kind" in i for i in items))
        # Header menüsü her rolde çalışmalı (operatör/normal kullanıcı izler)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("dashboard:api_mimic_menu")).status_code, 200)

    def test_list_includes_kind_and_optional_filter(self):
        r = self.client.get(reverse("dashboard:api_mimic_list"))
        items = r.json()["results"]
        self.assertTrue(all("kind" in i for i in items))
        kinds = {i["kind"] for i in items}
        self.assertEqual(kinds, {"2d", "3d"}, "varsayılan filtrelememeli")

        r = self.client.get(reverse("dashboard:api_mimic_list") + "?kind=3d")
        items = r.json()["results"]
        self.assertTrue(items)
        self.assertTrue(all(i["kind"] == "3d" for i in items))

    def test_get_includes_kind(self):
        r = self.client.get(reverse("dashboard:api_mimic_get") + "?id=%s" % self.m3.pk)
        self.assertEqual(r.json()["screen"]["kind"], "3d")


class Mimic3DSeedTests(MimicTestCase):
    """Yerleşik 3B şablonu: idempotent + şema uyumlu + silinemez."""

    def test_seed_is_idempotent_and_valid(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_mimic3d_templates", stdout=StringIO())
        call_command("seed_mimic3d_templates", stdout=StringIO())
        qs = MimicScreen.objects.filter(is_template=True, kind="3d")
        self.assertEqual(qs.count(), 1, "iki kez çalıştırmak tek kayıt bırakmalı")
        doc = qs.first().data
        self.assertEqual(doc["schema"], "mimic3d/1")
        self.assertEqual(doc["kind"], "3d")
        self.assertGreater(len(doc["objects"]), 20)
        self.assertTrue(all(o.get("id") and o.get("type") for o in doc["objects"]))
        # Her sembol bir anahtar taşımalı (autoKind bunu okur)
        for o in doc["objects"]:
            if o["type"] == "symbol":
                self.assertTrue(o.get("symbolKey"), o)

    def test_builtin_template_cannot_be_deleted(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_mimic3d_templates", stdout=StringIO())
        m = MimicScreen.objects.get(is_template=True, kind="3d")
        admin = CustomUser.objects.create_user(username="t_a2", password="x9Zq!Test", rol=1)
        self.client.force_login(admin)
        r = self.client.post(reverse("dashboard:api_mimic_delete"),
                             data=json.dumps({"id": m.pk}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(MimicScreen.objects.filter(pk=m.pk).exists())
