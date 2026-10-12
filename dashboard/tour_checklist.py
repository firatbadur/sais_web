"""Ekipman fotoğrafı çekim listesi — tek doğruluk kaynağı.

Bir tesiste "neyin fotoğrafı çekilmeli, nasıl çekilmeli" sorusunun cevabı.
Madde = fotoğraflanacak ekipman; her maddenin sıralı **çekim adımları** vardır
(genel görünüm → etiket plakası → bağlantılar...). Mobil çekim sayfası kullanıcıyı
eksik adımlar üzerinden sırayla yönlendirir.

İki kaynak:
  * `STANDARD_ITEMS` — her atıksu kabininde bulunan sabit ekipman listesi.
  * İstasyonun **analog sensörleri** — her biri `sensor:<id>` anahtarlı madde
    (analizör/prob), böylece fotoğraf doğrudan sensöre bağlanır.

Madde eklemek/çıkarmak için yalnız bu dosya değiştirilir (DB'de tanım yok);
eski fotoğraflar `item_key` ile saklandığından kaldırılan bir maddenin
fotoğrafları "Diğer" altında görünmeye devam eder.
"""
from __future__ import annotations

from django.utils.translation import gettext_lazy as _

# Ortak çekim adımları ---------------------------------------------------------
SHOT_OVERVIEW = {"key": "overview", "label": _("Genel görünüm"),
                 "hint": _("Ekipmanın tamamı ve çevresi görünecek şekilde, 1-2 adım geriden.")}
SHOT_NAMEPLATE = {"key": "nameplate", "label": _("Etiket plakası"),
                  "hint": _("Marka / model / seri no yazıları okunacak kadar yakından, flaşsız ve dik açıyla.")}
SHOT_DISPLAY = {"key": "display", "label": _("Ekran / gösterge"),
                "hint": _("Ekrandaki değer ve menü okunur şekilde; parlama yapmayan açıdan.")}
SHOT_WIRING = {"key": "wiring", "label": _("Bağlantılar / kablolama"),
               "hint": _("Kablo girişleri, klemens numaraları ve etiketleri görünür şekilde.")}
SHOT_PROBE = {"key": "probe", "label": _("Prob / ölçüm noktası"),
              "hint": _("Sensörün sudaki/akış hücresindeki konumu ve montajı.")}
SHOT_DETAIL = {"key": "detail", "label": _("Detay"),
               "hint": _("Önemli gördüğünüz ek detay (sarf malzeme, bakım noktası, hasar...).")}

CATEGORIES = [
    ("analyzer", _("Analizörler / Sensörler")),
    ("sampling", _("Numune Alma")),
    ("control", _("Kontrol / PLC")),
    ("drive", _("Sürücüler / Motorlar")),
    ("pump", _("Pompalar / Vanalar")),
    ("electric", _("Elektrik / Enerji")),
    ("comm", _("Haberleşme")),
    ("cabin", _("Kabin / Saha")),
    ("other", _("Diğer")),
]

STANDARD_ITEMS = [
    {"key": "sampler", "category": "sampling", "title": _("Numune Alıcı (Sampler)"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE, SHOT_DISPLAY,
               {"key": "bottles", "label": _("Şişe bölmesi"),
                "hint": _("Kapak açık, şişe dizilimi ve soğutma bölmesi görünür.")},
               {"key": "hose", "label": _("Numune hortumu / giriş"),
                "hint": _("Emiş hortumunun kabine girişi ve bağlantısı.")}]},
    {"key": "flow_cell", "category": "sampling", "title": _("Akış Hücresi / Numune Hattı"),
     "shots": [SHOT_OVERVIEW, SHOT_PROBE,
               {"key": "valves", "label": _("Vanalar ve hat"),
                "hint": _("Numune hattı vanaları ve yönleri.")}]},
    {"key": "plc", "category": "control", "title": _("PLC"),
     "shots": [SHOT_OVERVIEW,
               {"key": "cpu", "label": _("CPU etiketi"),
                "hint": _("CPU modülünün marka/model/sürüm etiketi okunur şekilde.")},
               {"key": "io_modules", "label": _("I/O modülleri"),
                "hint": _("Tüm giriş/çıkış modülleri yan yana, modül tipleri okunur şekilde.")},
               {"key": "io_leds", "label": _("I/O LED durumları"),
                "hint": _("Çalışma anında LED'ler yanarken, hangi kanalın aktif olduğu görünür.")},
               SHOT_WIRING]},
    {"key": "hmi", "category": "control", "title": _("Panel PC / HMI / Kayıt Bilgisayarı"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE]},
    {"key": "vfd", "category": "drive", "title": _("Sürücüler (VFD / Soft Starter)"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE, SHOT_DISPLAY, SHOT_WIRING], "multi": True},
    {"key": "motor", "category": "drive", "title": _("Motorlar"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE], "multi": True},
    {"key": "sample_pump", "category": "pump", "title": _("Numune Pompası"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE, SHOT_DETAIL], "multi": True},
    {"key": "wash_pump", "category": "pump", "title": _("Yıkama Pompası / Kompresör"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE], "multi": True},
    {"key": "valves", "category": "pump", "title": _("Selenoid / Motorlu Vanalar"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE], "multi": True},
    {"key": "flowmeter", "category": "pump", "title": _("Debimetre"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE, SHOT_DISPLAY,
               {"key": "install", "label": _("Montaj noktası"),
                "hint": _("Debimetrenin kanal/boru üzerindeki montajı (kabin dışındaysa da).")}]},
    {"key": "wash_tank", "category": "pump", "title": _("Yıkama Tankı / Kimyasal"),
     "shots": [SHOT_OVERVIEW, SHOT_DETAIL]},
    {"key": "panel", "category": "electric", "title": _("Elektrik Panosu"),
     "shots": [SHOT_OVERVIEW,
               {"key": "breakers", "label": _("Sigortalar / şalterler"),
                "hint": _("Sigorta etiketleri okunur şekilde, tüm sıra görünür.")},
               SHOT_WIRING]},
    {"key": "ups", "category": "electric", "title": _("UPS / Akü"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE, SHOT_DISPLAY]},
    {"key": "power_supply", "category": "electric", "title": _("Güç Kaynakları (24V)"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE]},
    {"key": "modem", "category": "comm", "title": _("Modem / Router / Gateway"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE,
               {"key": "ports", "label": _("Portlar ve antenler"),
                "hint": _("Kablo bağlı portlar, SIM yuvası ve anten bağlantıları.")}]},
    {"key": "switch", "category": "comm", "title": _("Ağ Anahtarı / Seri Dönüştürücü"),
     "shots": [SHOT_OVERVIEW, SHOT_NAMEPLATE], "multi": True},
    {"key": "cabin_front", "category": "cabin", "title": _("Kabin Dış Görünüş"),
     "shots": [{"key": "front", "label": _("Ön cephe"), "hint": _("Kabinin tamamı, karşıdan.")},
               {"key": "back", "label": _("Arka / yanlar"), "hint": _("Diğer cepheler ve çevre düzeni.")},
               {"key": "sign", "label": _("İsim / istasyon levhası"), "hint": _("Levha yazısı okunur şekilde.")}]},
    {"key": "cabin_inside", "category": "cabin", "title": _("Kabin İçi Genel"),
     "shots": [SHOT_OVERVIEW,
               {"key": "hvac", "label": _("Klima / havalandırma"), "hint": _("Klima iç ünitesi ve etiketi.")}]},
    {"key": "intake", "category": "cabin", "title": _("Numune Alma Noktası (Kanal / Havuz)"),
     "shots": [SHOT_OVERVIEW,
               {"key": "pump_point", "label": _("Dalgıç pompa / emiş noktası"),
                "hint": _("Emiş noktasının konumu ve erişimi.")}]},
]

SENSOR_SHOTS = [SHOT_OVERVIEW, SHOT_NAMEPLATE, SHOT_DISPLAY, SHOT_PROBE, SHOT_WIRING]
CUSTOM_KEY = "custom"


def sensor_item_key(sensor_id: int) -> str:
    return "sensor:%s" % sensor_id


def build_checklist(station) -> list[dict]:
    """İstasyon için tam çekim listesi (sensör maddeleri + standart maddeler).

    Dönen her madde: key, category, title, shots[{key,label,hint}], multi, sensor_id.
    Metinler `str()`'e çevrilir (JSON'a hazır).
    """
    from api.models import Sensor

    items: list[dict] = []
    sensors = (Sensor.objects.filter(connection__station=station, is_active=True,
                                     sensor_type__in=(0, 1))
               .select_related("parameter").order_by("display_order", "id"))
    for s in sensors:
        p = s.parameter
        name = (p.display_name if p else None) or s.tag or str(s.pk)
        items.append({
            "key": sensor_item_key(s.pk), "category": "analyzer",
            "title": str(_("Analizör / Sensör: %(name)s") % {"name": name}),
            "shots": [_shot(x) for x in SENSOR_SHOTS], "multi": False, "sensor_id": s.pk,
        })
    for it in STANDARD_ITEMS:
        items.append({
            "key": it["key"], "category": it["category"], "title": str(it["title"]),
            "shots": [_shot(x) for x in it["shots"]], "multi": bool(it.get("multi")),
            "sensor_id": None,
        })
    return items


def _shot(s: dict) -> dict:
    return {"key": s["key"], "label": str(s["label"]), "hint": str(s["hint"])}


def category_label(key: str) -> str:
    return str(dict(CATEGORIES).get(key, key))


def checklist_with_progress(station, photos) -> list[dict]:
    """Çekim listesine fotoğraf sayılarını işler. `photos`: EquipmentPhoto iterable (defer'li)."""
    counts: dict[tuple[str, str], int] = {}
    for ph in photos:
        counts[(ph.item_key, ph.shot_key)] = counts.get((ph.item_key, ph.shot_key), 0) + 1
    items = build_checklist(station)
    for it in items:
        done = 0
        for sh in it["shots"]:
            sh["count"] = counts.get((it["key"], sh["key"]), 0)
            done += 1 if sh["count"] else 0
        it["done_shots"] = done
        it["complete"] = done == len(it["shots"])
        it["category_label"] = category_label(it["category"])
    return items
