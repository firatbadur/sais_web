"""Yerleşik mimik şablonlarını (is_template) tohumlar — idempotent.

Şimdilik tek şablon: **SAIS Sürekli Atıksu İzleme Kabini** örnek HMI ekranı.
Eski "Kabin İzleme" demo panosunun mimik editörü (Fabric.js) formatındaki
karşılığıdır: peristaltik pompalar, akış hücresi kolonu, analizör paneli
(pH/Çöz.O₂/İletkenlik/KOİ/AKM/Sıcaklık), yıkama tankı, debimetre, vanalar ve
durum lambaları. Animasyon etiketleri (tag) SAIS parametre kodlarıyla
(`pH`, `Pompa1`, `Yikama`, `Ups`, `AcilStop` ...) hizalıdır; ileride gerçek
`Sensor`/`SensorLatest`'e bağlanabilir.

Çalıştır:  python manage.py seed_mimic_templates
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from dashboard.models import MimicScreen


# Renk paleti (mimic_symbols.js ile uyumlu)
BODY = "#c2ccd6"
EDGE = "#5e6b7a"
METAL = "#93a1b0"
DARK = "#2b313d"
STEEL = "#aeb9c4"
GREEN = "#3fbf6f"
RED = "#e4544c"
GRAY = "#8a96a3"
LIQ = "#dce6ee"
PANEL = "#222834"


def _scada(tag, anim="auto", **over):
    d = {
        "tag": tag, "anim": anim, "min": 0, "max": 100, "threshold": 1, "speed": 1,
        "onColor": GREEN, "offColor": RED, "unit": "", "decimals": 1, "moveRange": 60,
    }
    d.update(over)
    return d


# Fabric.js canonical obje özellik seti — editörde kaydedilen objelerle birebir
# parite (eksik özellik etkileşim/sürükleme hatasına yol açıyordu).
def _base(**over):
    d = {
        "version": "5.3.0", "originX": "left", "originY": "top",
        "angle": 0, "scaleX": 1, "scaleY": 1, "flipX": False, "flipY": False,
        "skewX": 0, "skewY": 0, "opacity": 1, "visible": True,
        "selectable": True, "evented": True,
        "fill": BODY, "stroke": EDGE, "strokeWidth": 2,
        "strokeDashArray": None, "strokeLineCap": "butt", "strokeDashOffset": 0,
        "strokeLineJoin": "miter", "strokeMiterLimit": 4, "strokeUniform": False,
        "backgroundColor": "", "fillRule": "nonzero", "paintFirst": "fill",
        "globalCompositeOperation": "source-over", "shadow": None,
    }
    d.update(over)
    return d


def _rect(left, top, w, h, fill=BODY, stroke=EDGE, sw=2, rx=0, name="rect",
          scada=None, symbol=None, opacity=1, extra=None):
    o = _base(type="rect", left=left, top=top, width=w, height=h, fill=fill,
              stroke=stroke, strokeWidth=sw, rx=rx, ry=rx, opacity=opacity, name=name,
              scada=scada or {"tag": "", "anim": "none"})
    if symbol:
        o["symbolKey"] = symbol
    if extra:
        o.update(extra)
    return o


def _circle(left, top, r, fill=BODY, stroke=EDGE, sw=2, name="circle",
            scada=None, symbol=None):
    o = _base(type="circle", left=left, top=top, radius=r, fill=fill,
              stroke=stroke, strokeWidth=sw, name=name,
              scada=scada or {"tag": "", "anim": "none"})
    if symbol:
        o["symbolKey"] = symbol
    return o


def _text(left, top, text, size=14, fill="#181c32", weight="normal", name="text"):
    return _base(type="i-text", left=left, top=top, text=text, fontSize=size,
                 fill=fill, stroke=None, fontWeight=weight, fontFamily="Inter, Arial",
                 textAlign="left", name=name, scada={"tag": "", "anim": "none"})


def _value(left, top, w, text, fill=GREEN, size=22, scada=None):
    return _base(type="textbox", left=left, top=top, width=w, text=text,
                 fontSize=size, fill=fill, stroke=None, textAlign="center",
                 fontWeight="bold", fontFamily="monospace", name="value",
                 scada=scada or {"tag": "", "anim": "none"})


def build_sais_cabinet():
    objs = []

    # --- Başlık ---
    objs.append(_text(40, 26, "SAIS Sürekli Atıksu İzleme Kabini", 30, "#181c32", "bold", "baslik"))
    objs.append(_text(42, 64, "Envisoft WebX — Örnek HMI Şablonu", 14, GRAY, "normal", "altbaslik"))

    # --- Kabin çerçevesi (dekoratif) ---
    # fill="" + perPixelTargetFind: iç boşluğa tıklayınca çerçeveyi seçmez
    # (altındaki nesneler/tuval hedeflenir) — "her tıklama çerçeveyi kapıyor" önlenir.
    objs.append(_rect(24, 96, 1232, 600, fill="", stroke="#c9d3dd", sw=2, rx=14, name="cerceve",
                      extra={"perPixelTargetFind": True}))

    # --- Giriş hattı + pompalar ---
    objs.append(_text(70, 150, "Numune Pompaları", 14, "#181c32", "600", "lbl-pompa"))
    objs.append(_rect(24, 206, 64, 12, fill=STEEL, name="boru-giris",
                      symbol="pipe_h", scada=_scada("AkisHizi", threshold=0)))
    objs.append(_circle(88, 176, 34, fill=BODY, name="pompa1",
                        symbol="pump_peristaltic", scada=_scada("Pompa1", speed=1.4)))
    objs.append(_text(96, 250, "Pompa 1", 12, "#3f4254", "600", "lbl-p1"))
    objs.append(_circle(88, 300, 34, fill=BODY, name="pompa2",
                        symbol="pump_peristaltic", scada=_scada("Pompa2", speed=1.4)))
    objs.append(_text(96, 374, "Pompa 2", 12, "#3f4254", "600", "lbl-p2"))

    # pompa → kolon boruları
    objs.append(_rect(156, 204, 210, 12, fill=STEEL, name="boru-p1",
                      symbol="pipe_h", scada=_scada("AkisHizi", threshold=0)))
    objs.append(_rect(156, 324, 150, 12, fill=STEEL, name="boru-p2",
                      symbol="pipe_h", scada=_scada("AkisHizi", threshold=0)))
    # vana
    objs.append(_circle(320, 188, 14, fill=BODY, name="vana1",
                        symbol="valve_ball", scada=_scada("ManuelYikama", onColor=GREEN, offColor=BODY)))

    # --- Akış hücresi / ölçüm kolonu ---
    objs.append(_text(372, 150, "Akış Hücresi", 14, "#181c32", "600", "lbl-kolon"))
    objs.append(_rect(376, 176, 84, 300, fill="#dfeaf1", stroke=EDGE, sw=3, rx=10, name="akis-hucresi",
                      symbol="flow_cell", scada=_scada("AkisHizi", min=0, max=100)))
    objs.append(_rect(396, 484, 44, 10, fill=METAL, name="kolon-cikis"))

    # --- Analizör paneli ---
    objs.append(_rect(520, 150, 380, 256, fill=PANEL, stroke="#171b22", sw=2, rx=10, name="analizor-panel"))
    objs.append(_text(538, 162, "ANALİZÖR PANELİ", 14, "#aeb9c4", "bold", "lbl-analizor"))
    params = [
        ("pH", "pH", "", 1),
        ("CozunmusOksijen", "Çöz. O₂", "mg/L", 2),
        ("Iletkenlik", "İletkenlik", "µS/cm", 0),
        ("KOi", "KOİ", "mg/L", 1),
        ("AKM", "AKM", "mg/L", 1),
        ("Sicaklik", "Sıcaklık", "°C", 1),
    ]
    bx0, by0, bw, bh, gx, gy = 538, 192, 172, 64, 184, 70
    for i, (code, label, unit, dec) in enumerate(params):
        col, row = i % 2, i // 2
        x = bx0 + col * gx
        y = by0 + row * gy
        objs.append(_rect(x, y, bw, bh, fill="#171b22", stroke="#0e1116", sw=1, rx=5, name="disp-bg-" + code))
        objs.append(_text(x + 10, y + 6, label, 12, "#7e8794", "600", "disp-lbl-" + code))
        objs.append(_value(x + 6, y + 24, bw - 12, "0." + ("0" * max(dec, 1)),
                           scada=_scada(code, anim="text", unit=unit, decimals=dec)))

    # --- Yıkama tankı ---
    objs.append(_text(948, 150, "Yıkama Tankı", 14, "#181c32", "600", "lbl-yikama"))
    objs.append(_rect(952, 176, 84, 240, fill=LIQ, stroke=EDGE, sw=3, rx=8, name="yikama-tanki",
                      symbol="tank_vertical", scada=_scada("Yikama", min=0, max=100)))
    objs.append(_circle(980, 430, 14, fill=BODY, name="vana-yikama",
                        symbol="valve_ball", scada=_scada("Yikama", onColor=GREEN, offColor=BODY)))

    # --- Debimetre + değer ---
    objs.append(_rect(540, 430, 130, 48, fill=BODY, stroke=EDGE, sw=2, rx=4, name="debimetre",
                      symbol="flowmeter", scada=_scada("Debi", min=0, max=100)))
    objs.append(_rect(688, 446, 110, 44, fill="#171b22", stroke="#0e1116", sw=1, rx=5, name="debi-bg"))
    objs.append(_text(696, 432, "Debi", 12, "#7e8794", "600", "lbl-debi"))
    objs.append(_value(690, 452, 106, "0.0",
                       scada=_scada("Debi", anim="text", unit="m³/sa", decimals=1)))
    objs.append(_rect(458, 450, 84, 10, fill=STEEL, name="boru-debi",
                      symbol="pipe_h", scada=_scada("AkisHizi", threshold=0)))

    # --- UPS ---
    objs.append(_rect(1066, 176, 84, 84, fill=BODY, stroke=EDGE, sw=3, rx=6, name="ups",
                      symbol="ups", scada=_scada("Ups", onColor=GREEN, offColor=RED)))
    objs.append(_text(1074, 264, "UPS", 12, "#3f4254", "600", "lbl-ups"))

    # --- Durum lambaları ---
    objs.append(_text(70, 560, "Durum Göstergeleri", 14, "#181c32", "600", "lbl-durum"))
    lamps = [
        ("Enerji", "Enerji", GREEN),
        ("Ups", "UPS", GREEN),
        ("AcilStop", "Acil Stop", RED),
        ("Kapi", "Kapı", RED),
        ("SuYok", "Su Yok", RED),
        ("SuBasti", "Su Bastı", RED),
        ("Bakim", "Bakım", "#f1b44c"),
        ("Yikama", "Yıkama", GREEN),
        ("NumuneAlma", "Numune", GREEN),
        ("Desarj", "Deşarj", GREEN),
    ]
    lx0, ly = 76, 592
    for i, (code, label, on) in enumerate(lamps):
        x = lx0 + i * 116
        objs.append(_circle(x, ly, 13, fill=GRAY, name="lamba-" + code,
                            symbol="lamp", scada=_scada(code, onColor=on, offColor=GRAY)))
        objs.append(_text(x + 34, ly + 2, label, 12, "#3f4254", "500", "lbl-" + code))

    return {"version": "5.3.0", "objects": objs}


class Command(BaseCommand):
    help = "Yerleşik mimik şablonlarını tohumlar (idempotent)."

    def handle(self, *args, **options):
        name = "SAIS Kabini — Örnek HMI"
        data = build_sais_cabinet()
        screen, created = MimicScreen.objects.update_or_create(
            name=name,
            defaults={
                "description": "SAIS sürekli atıksu izleme kabini için örnek HMI şablonu "
                               "(pompalar, akış hücresi, analizör, yıkama tankı, durum lambaları).",
                "data": data,
                "width": 1280,
                "height": 720,
                "background": "#eef3f7",
                "is_template": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            ("Oluşturuldu" if created else "Güncellendi") + f": {name} (id={screen.id}, "
            f"{len(data['objects'])} obje)"
        ))
