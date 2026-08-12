"""Yerleşik 3B mimik şablonunu tohumlar (idempotent).

`seed_mimic_templates` (2B) ile aynı deseni izler: `update_or_create(name=...)`
ve `is_template=True` (silinemez). Şablon, 3B editörün `mimic3d/1` şema
formatında Python'da üretilir — 3B belge kompakt olduğu için (sembol anahtarı +
transform + bağlama) bu 2B'deki Fabric JSON üretiminden çok daha kısadır.

Etiketler (`scada.tag`) SAIS parametre kodlarıyla hizalıdır (`pH`, `Debi`,
`Pompa1` ...). Sahada bu adlarla `Sensor.tag` varsa şablon canlı veriyle
ÇALIŞIR; yoksa animasyonlar 0 değerinde durur (hata vermez).

Çalıştırma:
    python manage.py seed_mimic3d_templates
"""
import json

from django.core.management.base import BaseCommand

from dashboard.models import MimicScreen

TEMPLATE_NAME = "SAIS Atıksu Tesisi — Örnek 3B HMI"

# Menü ön ayarları: viewer'da nesneye tıklanınca açılan rapor/kontrol menüsü.
MENU_FULL = {"enabled": True, "historic": True, "report": True, "daily": True, "control": False}
MENU_CTRL = {"enabled": True, "historic": True, "report": True, "daily": False, "control": True}
MENU_NONE = None


def _scada(tag, anim="auto", menu=None, **over):
    """Bağlama bloğu — 2B ile AYNI şema (moveRange 3B'de metre)."""
    d = {
        "tag": tag, "anim": anim,
        "min": 0, "max": 100, "threshold": 1, "speed": 1,
        "onColor": "#3fbf6f", "offColor": "#e4544c",
        "unit": "", "decimals": 1, "showUnit": True, "moveRange": 1.0,
    }
    if menu:
        d["menu"] = menu
    d.update(over)
    return d


def _obj(oid, otype, name, pos, scada=None, rot=None, scale=None, **extra):
    d = {
        "id": oid, "type": otype, "name": name, "parent": None,
        "position": pos, "rotation": rot or [0, 0, 0], "scale": scale or [1, 1, 1],
    }
    if scada:
        d["scada"] = scada
    d.update(extra)
    return d


def _sym(oid, key, name, pos, scada=None, rot=None, scale=None):
    return _obj(oid, "symbol", name, pos, scada, rot, scale, symbolKey=key)


def _label(oid, name, pos, tag, unit, decimals=2, width=1.5):
    return _obj(
        oid, "label", name, pos,
        _scada(tag, "text", unit=unit, decimals=decimals, min=0, max=100),
        text={
            "value": "0.00", "planeW": width, "planeH": 0.42, "billboard": True,
            "color": "#e8f0f8", "bg": "#0b1017", "bgOpacity": 0.55,
            "size": 128, "dpi": 256, "align": "center", "padding": 18,
            "font": "Inter, Arial, sans-serif", "weight": 700,
        },
    )


def build_scene():
    """Terfi → havalandırma → çöktürme hattı + numune/analiz paneli + pano."""
    o = []

    # ---- Hat 1: terfi kuyusu → pompa → havalandırma → çöktürme ----
    o.append(_sym("t3_well", "wet_well", "Terfi Kuyusu", [-8.5, 0, 2.5],
                  _scada("SeviyeKuyu", menu=MENU_FULL, max=5, unit="m", decimals=2)))
    o.append(_sym("t3_subpump", "pump_submersible", "Dalgıç Pompa", [-8.5, 0, 2.5],
                  _scada("Pompa1", menu=MENU_CTRL)))
    o.append(_sym("t3_pipe1", "pipe_h", "Emme Hattı", [-6.1, 0, 2.5],
                  _scada("Debi", menu=MENU_NONE, max=500)))
    o.append(_sym("t3_pump", "pump_centrifugal", "Terfi Pompası", [-4.1, 0, 2.5],
                  _scada("Pompa2", menu=MENU_CTRL)))
    o.append(_sym("t3_pipe2", "pipe_h", "Basma Hattı", [-2.2, 0, 2.5],
                  _scada("Debi", menu=MENU_NONE, max=500)))
    o.append(_sym("t3_aer", "aeration", "Havalandırma Havuzu", [0.9, 0, 2.5],
                  _scada("CozunmusOksijen", menu=MENU_FULL, max=12, unit="mg/L", decimals=2)))
    o.append(_sym("t3_pipe3", "pipe_h", "Çıkış Hattı", [3.6, 0, 2.5],
                  _scada("Debi", menu=MENU_NONE, max=500)))
    o.append(_sym("t3_clar", "clarifier", "Çöktürme Havuzu", [7.2, 0, 2.5],
                  _scada("AKM", menu=MENU_FULL, max=200, unit="mg/L", decimals=1)))

    # ---- Hat 2: kimyasal dozaj ----
    o.append(_sym("t3_ctank", "tank_cone", "Kimyasal Tank", [-8.0, 0, -2.5],
                  _scada("KimyasalSeviye", menu=MENU_FULL, max=100, unit="%", decimals=0)))
    o.append(_sym("t3_dos", "dosing_pump", "Dozaj Pompası", [-5.8, 0, -2.5],
                  _scada("DozajPompa", menu=MENU_CTRL)))
    o.append(_sym("t3_mixer", "mixer", "Karıştırıcı", [-4.2, 0, -2.5],
                  _scada("Karistirici", menu=MENU_CTRL)))
    o.append(_sym("t3_valve", "valve_ball", "Dozaj Vanası", [-2.6, 0, -2.5],
                  _scada("DozajVana", menu=MENU_CTRL)))

    # ---- Numune / analiz paneli ----
    o.append(_sym("t3_cabinet", "cabinet", "Numune Kabini", [-0.6, 0, -2.5],
                  _scada("Enerji", menu=MENU_FULL)))
    o.append(_sym("t3_plc", "plc", "PLC", [0.9, 0, -2.5], _scada("Plc", menu=MENU_FULL)))
    o.append(_sym("t3_cell", "flow_cell", "Akış Hücresi", [2.0, 0, -2.5],
                  _scada("pH", menu=MENU_FULL, max=14, unit="pH", decimals=2)))
    o.append(_sym("t3_ph", "ph_sensor", "pH Sensörü", [2.8, 0, -2.5],
                  _scada("pH", menu=MENU_FULL, max=14, unit="pH", decimals=2)))
    o.append(_sym("t3_analyzer", "analyzer", "Analizör", [3.9, 0, -2.5],
                  _scada("KOi", menu=MENU_FULL, max=1000, unit="mg/L", decimals=0)))
    o.append(_sym("t3_flow", "flowmeter", "Debimetre", [5.2, 0, -2.5],
                  _scada("Debi", menu=MENU_FULL, max=500, unit="m³/h", decimals=1)))
    o.append(_sym("t3_gauge", "gauge", "Basınç", [6.4, 0, -2.5],
                  _scada("Basinc", menu=MENU_FULL, max=10, unit="bar", decimals=2)))
    o.append(_sym("t3_fridge", "sample_fridge", "Numune Buzdolabı", [7.8, 0, -2.5],
                  _scada("NumuneAlma", menu=MENU_FULL)))
    o.append(_sym("t3_wash", "wash_bar", "Yıkama Barı", [2.0, 0, -0.9],
                  _scada("Yikama", menu=MENU_FULL)))

    # ---- Saha / güvenlik ----
    o.append(_sym("t3_beacon", "beacon", "Çakar Lamba", [-0.6, 0, -1.0],
                  _scada("Alarm", menu=MENU_FULL)))
    o.append(_sym("t3_estop", "emergency_stop", "Acil Stop", [0.4, 0, -1.0],
                  _scada("AcilStop", menu=MENU_FULL)))
    o.append(_sym("t3_lamp", "lamp", "Enerji Lambası", [1.2, 0, -1.0],
                  _scada("Enerji", menu=MENU_FULL)))
    o.append(_sym("t3_door", "door", "Kabin Kapısı", [-2.0, 0, -4.6],
                  _scada("Kapi", menu=MENU_FULL)))

    # ---- Değer göstergeleri (etiketler) ----
    o.append(_label("t3_lbl_ph", "pH Göstergesi", [2.0, 1.5, -2.5], "pH", "pH", 2, 1.3))
    o.append(_label("t3_lbl_do", "Çözünmüş Oksijen", [0.9, 1.9, 2.5],
                    "CozunmusOksijen", "mg/L", 2, 1.7))
    o.append(_label("t3_lbl_flow", "Debi", [5.2, 1.2, -2.5], "Debi", "m³/h", 1, 1.6))
    o.append(_label("t3_lbl_akm", "AKM", [7.2, 1.6, 2.5], "AKM", "mg/L", 1, 1.6))

    # ---- Kontrol butonları ----
    o.append(_obj("t3_btn_dos", "button", "Dozaj Aç/Kapat", [-5.8, 0.95, -1.1],
                  {"tag": "DozajPompa", "action": "toggle",
                   "pressValue": 100, "releaseValue": 0, "setValue": 100},
                  button={"label": "DOZAJ", "bg": "#2fb574", "fg": "#ffffff",
                          "w": 1.15, "h": 0.4, "d": 0.1, "radius": 0.06, "fontsize": 110}))
    o.append(_obj("t3_btn_wash", "button", "Yıkama Başlat", [2.0, 0.95, 0.2],
                  {"tag": "Yikama", "action": "momentary",
                   "pressValue": 100, "releaseValue": 0, "setValue": 100},
                  button={"label": "YIKAMA", "bg": "#009ef7", "fg": "#ffffff",
                          "w": 1.25, "h": 0.4, "d": 0.1, "radius": 0.06, "fontsize": 100}))

    return o


def build_doc():
    objects = build_scene()
    doc = {
        "schema": "mimic3d/1", "kind": "3d", "renderer": "three", "units": "m",
        "scene": {
            "background": {"mode": "gradient", "color": "#141b26",
                           "top": "#3d5f80", "bottom": "#0e141c"},
            "environment": {"preset": "room", "intensity": 0.55},
            "fog": {"enabled": False, "color": "#0e141c", "near": 20, "far": 160},
            "grid": {"visible": True, "size": 40, "divisions": 40,
                     "color1": "#3a4250", "color2": "#252b36"},
            "ground": {"visible": True, "size": 40, "color": "#20242d",
                       "roughness": 0.92, "metalness": 0.0, "receiveShadow": True},
            "lights": {
                "ambient": {"color": "#ffffff", "intensity": 0.40},
                "hemi": {"skyColor": "#dfe8f5", "groundColor": "#3a3f48", "intensity": 0.35},
                "dir": {"color": "#ffffff", "intensity": 1.15,
                        "position": [12, 18, 9], "target": [0, 0, 0], "castShadow": True},
            },
            "shadows": {"enabled": True, "quality": "medium", "bias": -0.0005},
            "toneMapping": "aces", "exposure": 1.0,
        },
        "camera": {
            "type": "perspective", "fov": 50, "near": 0.1, "far": 400,
            "position": [10.5, 7.5, 16.0], "target": [0, 1.0, 0], "orthoZoom": 40,
            "limits": {"minDistance": 1.5, "maxDistance": 180, "maxPolarAngle": 1.5533},
        },
        # Görüş noktaları: viewer üst barında buton olarak çıkar (3B'ye özgü konfor).
        "viewpoints": [
            {"id": "vp_all", "name": "Genel Görünüm", "position": [10.5, 7.5, 16.0],
             "target": [0, 1.0, 0], "fov": 50, "duration": 900, "hotkey": "1"},
            {"id": "vp_line", "name": "Arıtma Hattı", "position": [-1.5, 3.4, 9.5],
             "target": [-1.0, 0.8, 2.5], "fov": 46, "duration": 800, "hotkey": "2"},
            {"id": "vp_panel", "name": "Analiz Paneli", "position": [3.2, 2.4, 1.6],
             "target": [2.8, 0.8, -2.5], "fov": 42, "duration": 800, "hotkey": "3"},
            {"id": "vp_dos", "name": "Dozaj Ünitesi", "position": [-5.0, 2.6, 1.4],
             "target": [-5.4, 0.8, -2.5], "fov": 44, "duration": 800, "hotkey": "4"},
        ],
        "paths": [],
        "objects": objects,
        "meta": {"objectCount": len(objects), "editorVersion": "1.0.0", "bytes": 0},
    }
    doc["meta"]["bytes"] = len(json.dumps(doc, separators=(",", ":")))
    return doc


class Command(BaseCommand):
    help = "Yerleşik 3B mimik şablonunu tohumlar (idempotent)."

    def handle(self, *args, **options):
        doc = build_doc()
        screen, created = MimicScreen.objects.update_or_create(
            name=TEMPLATE_NAME,
            defaults={
                "kind": MimicScreen.KIND_3D,
                "description": (
                    "Örnek 3B HMI: terfi kuyusu → havalandırma → çöktürme hattı, "
                    "kimyasal dozaj ünitesi, numune/analiz paneli ve saha ekipmanları. "
                    "Etiketler SAIS parametre kodlarıyla hizalıdır; kopyalayıp "
                    "kendi saha etiketlerinizle düzenleyebilirsiniz."
                ),
                "data": doc,
                "width": 1280,
                "height": 720,
                "background": "#0e141c",
                "is_template": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            "%s: %s (id=%s, %s nesne, %.1f KB)" % (
                "Oluşturuldu" if created else "Güncellendi",
                TEMPLATE_NAME, screen.pk, len(doc["objects"]),
                doc["meta"]["bytes"] / 1024.0)))
