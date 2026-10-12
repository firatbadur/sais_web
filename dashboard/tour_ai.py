"""Tesis Sanal Turu — AI (Claude vision) yardımcıları.

İki iş:

1. `suggest_hotspots(scene)` — sahnenin **perspektif çekim karelerini** (panorama
   değil: equirectangular bozulma tanımayı zorlaştırır) gruplar halinde Claude'a
   gönderir; her karede görülen ekipmanları normalize kutu (bbox) ile ister. Kutu
   merkezi, karenin yönelimi + görüş açısıyla küre yaw/pitch'ine çevrilir
   (`tour_stitch.frame_point_to_yaw_pitch`), yakın tekrarlar birleştirilir ve
   **onaysız** `TourHotspot` (ai_suggested=True, confirmed=False) olarak yazılır.
   İstasyonun sensör listesi prompt'a verilir; model eşleşen sensör id'sini döndürür.
2. `read_nameplate(photo)` — ekipman fotoğrafındaki etiket plakasından
   marka/model/seri no okur, boş alanları doldurur (kullanıcının girdiğini ezmez).

Gate: `settings.ANTHROPIC_API_KEY` boşsa `AIUnavailable`. Her çağrı `ApiLog`'a
(`direction='out'`, component="tour_ai") düşer; görüntü verisi log'a yazılmaz.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import time

from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
FRAMES_PER_REQUEST = 6     # tek istekte gönderilen kare sayısı
AI_IMAGE_PX = 1280         # AI'a giden karelerin uzun kenarı (maliyet/kalite dengesi)
MERGE_DEG = 6.0            # aynı etiketli öneriler bu açıdan yakınsa tek işaret
MIN_CONFIDENCE = 0.35

CATEGORY_LABELS = {
    "analyzer": "Analizör", "sensor": "Sensör / Prob", "sampler": "Numune Alıcı",
    "plc": "PLC / I/O", "drive": "Sürücü / Motor", "pump": "Pompa", "valve": "Vana",
    "flowmeter": "Debimetre", "panel": "Elektrik Panosu", "ups": "UPS",
    "network": "Modem / Ağ", "tank": "Tank", "hvac": "Klima", "other": "Diğer",
}


class AIUnavailable(Exception):
    """AI yapılandırılmamış veya çağrı başarısız — mesaj kullanıcıya gösterilir."""


def _client():
    key = getattr(settings, "ANTHROPIC_API_KEY", "")
    if not key:
        raise AIUnavailable("AI anahtarı (ANTHROPIC_API_KEY) tanımlı değil.")
    try:
        import anthropic
    except Exception as exc:  # pragma: no cover - ortam bağımlı
        raise AIUnavailable("anthropic paketi kurulu değil.") from exc
    return anthropic.Anthropic(api_key=key, timeout=180.0, max_retries=2)


def _image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode("ascii")}}


def _shrink(jpeg: bytes, max_px: int = AI_IMAGE_PX) -> tuple[bytes, int, int]:
    from . import tour_stitch as ts

    img = ts.limit_size(ts.decode_image(jpeg), max_px)
    h, w = img.shape[:2]
    return ts.encode_jpeg(img, 82), w, h


def _call(*, content: list, schema: dict, system: str, max_tokens: int = 8000,
          triggered_by=None) -> dict:
    """Tek Claude çağrısı → şemaya uyan dict. Hata/ret durumunda AIUnavailable."""
    import anthropic

    from api.api_logging import record_outbound_call

    client = _client()
    model = settings.TOUR_AI_MODEL
    started = time.monotonic()
    status, body, err = None, "", ""
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": schema}},
            # Güvenlik sınıflandırıcısı reddederse istek varsayılan yedek modele yönlenir.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        status = 200
        if response.stop_reason == "refusal":
            raise AIUnavailable("Model isteği yanıtlamayı reddetti.")
        if response.stop_reason == "max_tokens":
            raise AIUnavailable("Yanıt uzunluk sınırına takıldı.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        body = text[:4000]
        return json.loads(text)
    except anthropic.APIStatusError as exc:
        status, err = exc.status_code, str(exc.message)[:500]
        raise AIUnavailable("AI servisi hata döndürdü (%s): %s" % (exc.status_code, err)) from exc
    except anthropic.APIConnectionError as exc:
        err = "bağlantı hatası"
        raise AIUnavailable("AI servisine bağlanılamadı (internet / güvenlik duvarı).") from exc
    except (ValueError, StopIteration) as exc:
        err = "geçersiz JSON"
        raise AIUnavailable("AI yanıtı çözümlenemedi.") from exc
    finally:
        try:
            n_images = sum(1 for c in content if c.get("type") == "image")
            record_outbound_call(
                method="POST", url=API_URL,
                request_body={"model": model, "images": n_images},
                response_status=status, response_body=body,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_message=err, component="tour_ai", triggered_by=triggered_by,
            )
        except Exception:  # log yazılamaması asıl işi bozmasın
            logger.exception("tour_ai ApiLog yazılamadı")


# --------------------------------------------------------------------------- #
# 1) Panorama sahnesi → hotspot önerileri
# --------------------------------------------------------------------------- #

DETECT_SCHEMA = {
    "type": "object",
    "properties": {
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frame": {"type": "integer", "description": "Görüntü sıra numarası (0'dan)."},
                    "label": {"type": "string"},
                    "category": {"type": "string", "enum": sorted(CATEGORY_LABELS)},
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "x": {"type": "number", "description": "Kutu merkezi, 0-1 (soldan)."},
                    "y": {"type": "number", "description": "Kutu merkezi, 0-1 (üstten)."},
                    "sensor_id": {"type": "integer", "description": "Eşleşen sensör id; yoksa 0."},
                    "confidence": {"type": "number"},
                },
                "required": ["frame", "label", "category", "brand", "model", "x", "y",
                             "sensor_id", "confidence"],
                "additionalProperties": False,
            },
        },
        "inventory": {"type": "string",
                      "description": "Bu görüntülerde görülen ekipmanların kısa Türkçe listesi."},
    },
    "required": ["detections", "inventory"],
    "additionalProperties": False,
}

DETECT_SYSTEM = (
    "Atıksu sürekli izleme istasyonu (SAIS) kabinlerinden çekilmiş fotoğrafları inceleyen bir "
    "saha mühendisisin. Görevin her fotoğrafta görünen ölçüm ve proses ekipmanlarını "
    "(analizörler, problar, numune alıcı, PLC ve I/O modülleri, sürücüler, pompalar, vanalar, "
    "debimetre, pano, UPS, modem, tank, klima) bulmak. Yalnız gerçekten görünen ve tanınabilir "
    "ekipmanı işaretle; duvar, kablo kanalı, boru gibi sıradan nesneleri işaretleme. Etiket "
    "Türkçe ve kısa olsun (ör. 'pH Analizörü', 'Numune Pompası'). Marka/model yalnız okunabiliyorsa "
    "yaz, yoksa boş bırak. Konumu ekipmanın merkezi olarak 0-1 aralığında ver. Verilen sensör "
    "listesinde ekipmanla açıkça eşleşen bir sensör varsa id'sini yaz, yoksa 0."
)


def _sensor_lines(station_id: int) -> tuple[str, set[int]]:
    from api.models import Sensor

    rows, ids = [], set()
    for s in (Sensor.objects.filter(connection__station_id=station_id, is_active=True)
              .select_related("parameter").order_by("display_order", "id")):
        p = s.parameter
        name = (p.display_name if p else None) or s.tag
        unit = ((p.unit_txt or p.unit or "") if p else "")
        kind = "dijital" if s.sensor_type in (2, 3) else "analog"
        rows.append("- id=%s: %s (%s%s, etiket %s)" % (s.pk, name, kind, ", " + unit if unit else "", s.tag))
        ids.add(s.pk)
    return ("\n".join(rows) or "(sensör yok)"), ids


def _angle_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    ya, pa = map(math.radians, a)
    yb, pb = map(math.radians, b)
    c = math.sin(pa) * math.sin(pb) + math.cos(pa) * math.cos(pb) * math.cos(ya - yb)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def detections_to_points(detections: list[dict], frames: list[dict], valid_sensors: set[int]):
    """Kare-bağıl tespitleri küre noktalarına çevirip birleştirir (saf fonksiyon, test edilir).

    `frames`: [{"yaw","pitch","roll","hfov","aspect"}] — tespitteki `frame` indeksine karşılık.
    """
    from .tour_stitch import frame_point_to_yaw_pitch

    points: list[dict] = []
    for d in detections:
        try:
            fr = frames[int(d["frame"])]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        conf = float(d.get("confidence") or 0)
        if conf < MIN_CONFIDENCE:
            continue
        x = min(1.0, max(0.0, float(d.get("x", 0.5))))
        y = min(1.0, max(0.0, float(d.get("y", 0.5))))
        yaw, pitch = frame_point_to_yaw_pitch(x, y, fr["yaw"], fr["pitch"], fr["roll"],
                                              fr["hfov"], fr["aspect"])
        sid = int(d.get("sensor_id") or 0)
        cand = {"yaw": yaw, "pitch": pitch, "label": (d.get("label") or "").strip()[:150],
                "category": d.get("category") or "other", "brand": (d.get("brand") or "").strip()[:100],
                "model": (d.get("model") or "").strip()[:100],
                "sensor_id": sid if sid in valid_sensors else None, "confidence": conf}
        # Komşu karelerdeki aynı ekipman → en güvenilir olanı tut.
        dup = next((p for p in points
                    if (p["label"].lower() == cand["label"].lower()
                        or (cand["sensor_id"] and p["sensor_id"] == cand["sensor_id"]))
                    and _angle_between((p["yaw"], p["pitch"]), (yaw, pitch)) < MERGE_DEG * 2), None)
        if dup:
            if cand["confidence"] > dup["confidence"]:
                dup.update(cand)
            continue
        points.append(cand)
    return points


def suggest_hotspots(scene, triggered_by=None) -> tuple[int, str]:
    """Sahne karelerinden hotspot önerileri üretir. (eklenen sayı, envanter metni)."""
    from .models import TourFrame, TourHotspot

    frames_qs = list(TourFrame.objects.filter(scene=scene, has_orientation=True).order_by("seq"))
    if not frames_qs:
        raise AIUnavailable("Bu sahnede yönelimli çekim karesi yok (yüklenen panoramalarda AI önerisi "
                            "yapılamaz; sahneyi telefonla yönlendirmeli çekin).")
    sensor_text, valid_sensors = _sensor_lines(scene.station_id)

    all_points, inventory = [], []
    for start in range(0, len(frames_qs), FRAMES_PER_REQUEST):
        chunk = frames_qs[start:start + FRAMES_PER_REQUEST]
        content, metas = [], []
        for i, f in enumerate(chunk):
            jpeg, w, h = _shrink(bytes(f.image))
            content.append({"type": "text", "text": "Görüntü %d:" % i})
            content.append(_image_block(jpeg))
            metas.append({"yaw": f.yaw, "pitch": f.pitch, "roll": f.roll,
                          "hfov": f.hfov or 60.0, "aspect": (w / float(h)) if h else 1.0})
        content.append({"type": "text", "text": (
            "Tesis: %s, sahne: %s.\nİstasyonun sensörleri:\n%s\n\n"
            "Her görüntüdeki ekipmanları listele." % (scene.station.name, scene.name, sensor_text))})
        result = _call(content=content, schema=DETECT_SCHEMA, system=DETECT_SYSTEM,
                       triggered_by=triggered_by)
        all_points.extend(detections_to_points(result.get("detections") or [], metas, valid_sensors))
        if result.get("inventory"):
            inventory.append(result["inventory"].strip())

    # Parçalar arası tekrarları da birleştir.
    merged: list[dict] = []
    for p in all_points:
        if any(m["label"].lower() == p["label"].lower()
               and _angle_between((m["yaw"], m["pitch"]), (p["yaw"], p["pitch"])) < MERGE_DEG * 2
               for m in merged):
            continue
        merged.append(p)

    # Önceki onaysız AI önerilerini yenileriyle değiştir; onaylanmış/elle eklenenlere dokunma
    # ve onlara çok yakın öneriyi ekleme (mükerrer işaret olmasın).
    TourHotspot.objects.filter(scene=scene, ai_suggested=True, confirmed=False).delete()
    existing = list(TourHotspot.objects.filter(scene=scene).values("yaw", "pitch"))
    added = 0
    for p in merged:
        if any(_angle_between((e["yaw"], e["pitch"]), (p["yaw"], p["pitch"])) < MERGE_DEG
               for e in existing):
            continue
        TourHotspot.objects.create(
            scene=scene,
            kind=TourHotspot.Kind.SENSOR if p["sensor_id"] else TourHotspot.Kind.EQUIPMENT,
            yaw=round(p["yaw"], 2), pitch=round(p["pitch"], 2),
            title=p["label"] or CATEGORY_LABELS.get(p["category"], ""),
            brand=p["brand"], model_name=p["model"], sensor_id=p["sensor_id"],
            description="AI kategori: %s" % CATEGORY_LABELS.get(p["category"], p["category"]),
            ai_suggested=True, ai_confidence=round(p["confidence"], 2), confirmed=False,
        )
        added += 1
    return added, "\n".join(inventory)


# --------------------------------------------------------------------------- #
# 2) Ekipman fotoğrafı → etiket plakası okuma
# --------------------------------------------------------------------------- #

NAMEPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "equipment": {"type": "string", "description": "Ekipmanın ne olduğu (Türkçe, kısa)."},
        "brand": {"type": "string"},
        "model": {"type": "string"},
        "serial_no": {"type": "string"},
        "other_text": {"type": "string",
                       "description": "Plakadaki diğer önemli bilgiler (güç, gerilim, ölçüm aralığı, IP...)."},
        "notes": {"type": "string", "description": "Görülen durum/hasar/eksik notu; yoksa boş."},
    },
    "required": ["equipment", "brand", "model", "serial_no", "other_text", "notes"],
    "additionalProperties": False,
}

NAMEPLATE_SYSTEM = (
    "Endüstriyel ekipman fotoğraflarından etiket plakası bilgisi çıkaran bir saha teknisyenisin. "
    "Yalnız fotoğrafta gerçekten okunabilen değerleri yaz; tahmin etme, okunamayan alanı boş bırak. "
    "Seri numarasını karakter karakter aynen aktar."
)


def read_nameplate(photo, hint: str = "", triggered_by=None) -> dict:
    """Fotoğraftan marka/model/seri no okur, boş alanları doldurup kaydeder."""
    jpeg, _, _ = _shrink(bytes(photo.image), 1800)  # küçük yazılar için daha yüksek çözünürlük
    content = [_image_block(jpeg), {"type": "text", "text": (
        "Bu fotoğraftaki ekipmanın etiket bilgilerini çıkar.%s" % (" Bağlam: " + hint if hint else ""))}]
    result = _call(content=content, schema=NAMEPLATE_SCHEMA, system=NAMEPLATE_SYSTEM,
                   max_tokens=4000, triggered_by=triggered_by)
    if not photo.brand and result.get("brand"):
        photo.brand = result["brand"][:100]
    if not photo.model_name and result.get("model"):
        photo.model_name = result["model"][:100]
    if not photo.serial_no and result.get("serial_no"):
        photo.serial_no = result["serial_no"][:100]
    lines = [result.get(k, "").strip() for k in ("equipment", "other_text", "notes")]
    photo.ai_text = "\n".join(x for x in lines if x)
    return result
