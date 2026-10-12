"""Tesis Sanal Turu — sayfalar + AJAX uçları.

Sayfalar:
  * `TourListView`    — istasyon bazlı sahne galerisi (base.html içinde).
  * `TourViewerView`  — standalone tam ekran 360° görüntüleyici + hotspot editörü.
  * `TourCaptureView` — standalone mobil çekim sayfası (kamera + jiroskop).

Okuma uçları tüm rollere açık (`login_required`); yazma uçları operatör+admin
(`_require_operator`). Yanıt sözleşmesi dashboard AJAX'ıyla aynı: `{"ok": bool, ...}`.
"""
from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from api.events import EventType, log_event
from api.models import Calibration, Sensor, Station

from .api_views import _humanize_ago_tr, _require_operator
from .models import Document, EquipmentPhoto, TourFrame, TourHotspot, TourScene
from .tour_checklist import (
    CATEGORIES, CUSTOM_KEY, build_checklist, checklist_with_progress, sensor_item_key,
)
from .permissions import ROLE_ADMIN, ROLE_OPERATOR, RoleRequiredMixin, OperatorRequiredMixin, user_has_role


def _ai_enabled() -> bool:
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _float(value, default=0.0):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f else default  # NaN koruması


def _upload_too_big(upload) -> bool:
    return upload.size > settings.TOUR_MAX_UPLOAD_MB * 1024 * 1024


# --------------------------------------------------------------------------- #
# Serileştirme
# --------------------------------------------------------------------------- #

def _scene_dict(scene: TourScene, hotspot_count: int | None = None) -> dict:
    from django.urls import reverse

    ready = scene.status == TourScene.Status.READY
    v = scene.image_version
    return {
        "id": scene.pk,
        "station_id": scene.station_id,
        "name": scene.name,
        "description": scene.description,
        "order": scene.order,
        "status": scene.status,
        "status_label": scene.get_status_display(),
        "error": scene.error,
        "source": scene.source,
        "stitch_method": scene.stitch_method,
        "initial_yaw": scene.initial_yaw,
        "initial_pitch": scene.initial_pitch,
        "image_url": (reverse("dashboard:api_tour_scene_image", args=[scene.pk]) + "?v=%s" % v)
        if ready else None,
        "thumb_url": (reverse("dashboard:api_tour_scene_thumb", args=[scene.pk]) + "?v=%s" % v)
        if ready else None,
        "hotspot_count": hotspot_count,
        "updated_at": timezone.localtime(scene.updated_at).strftime("%d.%m.%Y %H:%M")
        if scene.updated_at else "",
        "ai_status": scene.ai_status,
        "ai_error": scene.ai_error,
        "ai_added": scene.ai_added,
        "ai_inventory": scene.ai_inventory,
    }


def _hotspot_dict(h: TourHotspot) -> dict:
    sensor = h.sensor
    param = sensor.parameter if sensor else None
    return {
        "id": h.pk,
        "scene_id": h.scene_id,
        "kind": h.kind,
        "kind_label": h.get_kind_display(),
        "yaw": h.yaw,
        "pitch": h.pitch,
        "title": h.title,
        "description": h.description,
        "brand": h.brand,
        "model_name": h.model_name,
        "serial_no": h.serial_no,
        "sensor_id": h.sensor_id,
        "sensor_tag": sensor.tag if sensor else "",
        "sensor_label": ((param.display_name if param else None) or (sensor.tag if sensor else "")),
        "unit": ((param.unit_txt or param.unit or "") if param else ""),
        "digital": bool(sensor and sensor.sensor_type in (2, 3)),
        "target_scene_id": h.target_scene_id,
        "document_id": h.document_id,
        "document_title": h.document.title if h.document_id else "",
        "equipment_key": h.equipment_key,
        "ai_suggested": h.ai_suggested,
        "ai_confidence": h.ai_confidence,
        "confirmed": h.confirmed,
    }


def _hotspot_qs():
    return TourHotspot.objects.select_related("sensor__parameter", "document")


# --------------------------------------------------------------------------- #
# Sayfalar
# --------------------------------------------------------------------------- #

class TourListView(RoleRequiredMixin, TemplateView):
    """Sahne galerisi — istasyon seçimi + sahne kartları + QR ile mobil çekim."""

    template_name = "dashboard/tour/tour_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        stations = list(Station.objects.order_by("name"))
        try:
            station_id = int(self.request.GET.get("station") or 0)
        except ValueError:
            station_id = 0
        if not station_id and stations:
            # Varsayılan: sahnesi olan ilk istasyon, yoksa ilk istasyon.
            with_scenes = (TourScene.objects.values_list("station_id", flat=True)
                           .order_by("station_id").first())
            station_id = with_scenes or stations[0].pk
        station = next((s for s in stations if s.pk == station_id), None)
        scenes = []
        if station:
            qs = (TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(station=station)
                  .annotate(hcount=Count("hotspots", filter=Q(hotspots__confirmed=True)),
                            fcount=Count("frames", distinct=True)))
            scenes = [dict(_scene_dict(s, s.hcount), frame_count=s.fcount) for s in qs]
        ctx.update({
            "stations": stations,
            "station": station,
            "scenes": scenes,
            "can_manage": user_has_role(self.request.user, ROLE_ADMIN, ROLE_OPERATOR),
            "tab": "photos" if self.request.GET.get("tab") == "photos" else "scenes",
            "ai_enabled": _ai_enabled(),
        })
        return ctx


class TourViewerView(RoleRequiredMixin, TemplateView):
    """Standalone 360° tur görüntüleyici (her rol). Operatörde düzenleme modu."""

    template_name = "dashboard/tour/viewer.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        station = get_object_or_404(Station, pk=self.kwargs["station_id"])
        try:
            start_scene = int(self.request.GET.get("scene") or 0)
        except ValueError:
            start_scene = 0
        can_manage = user_has_role(self.request.user, ROLE_ADMIN, ROLE_OPERATOR)
        sensors = []
        documents = []
        equipment = []
        if can_manage:
            sensors = [
                {"id": s.pk, "text": "%s — %s" % (
                    (s.parameter.display_name if s.parameter else None) or s.tag, s.tag)}
                for s in Sensor.objects.filter(connection__station=station)
                .select_related("parameter").order_by("display_order", "id")
            ]
            documents = [{"id": d.pk, "text": d.title}
                         for d in Document.objects.order_by("title")]
            equipment = [{"id": it["key"], "text": it["title"]}
                         for it in build_checklist(station)]
        ctx.update({
            "station": station,
            "start_scene": start_scene,
            "can_manage": can_manage,
            "ai_enabled": can_manage and _ai_enabled(),
            "sensors_json": sensors,
            "documents_json": documents,
            "equipment_json": equipment,
            "kinds": TourHotspot.Kind.choices,
        })
        return ctx


class TourCaptureView(OperatorRequiredMixin, TemplateView):
    """Standalone mobil çekim sayfası (telefon kamerası + jiroskop yönlendirmesi)."""

    template_name = "dashboard/tour/capture.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        scene = get_object_or_404(TourScene.objects.defer(*TourScene.LIGHT_DEFER)
                                  .select_related("station"), pk=self.kwargs["pk"])
        ctx.update({
            "scene": scene,
            "frame_count": scene.frames.count(),
            "max_frame_px": settings.TOUR_MAX_FRAME_PX,
            "max_frames": settings.TOUR_MAX_FRAMES,
        })
        return ctx


# --------------------------------------------------------------------------- #
# Okuma uçları (tüm roller)
# --------------------------------------------------------------------------- #

@login_required
def tour_scenes(request):
    """Bir istasyonun sahneleri + hotspot'ları (görüntüleyici tek çağrıda yükler)."""
    try:
        station_id = int(request.GET.get("station") or 0)
    except ValueError:
        station_id = 0
    scenes = list(TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(station_id=station_id))
    can_manage = user_has_role(request.user, ROLE_ADMIN, ROLE_OPERATOR)
    hs = _hotspot_qs().filter(scene__station_id=station_id)
    if not can_manage:
        hs = hs.filter(confirmed=True)  # onaylanmamış AI önerileri yalnız düzenleyene
    by_scene: dict[int, list] = {}
    for h in hs:
        by_scene.setdefault(h.scene_id, []).append(_hotspot_dict(h))
    out = []
    for s in scenes:
        d = _scene_dict(s, len([h for h in by_scene.get(s.pk, []) if h["confirmed"]]))
        d["hotspots"] = by_scene.get(s.pk, [])
        out.append(d)
    return JsonResponse({"ok": True, "scenes": out})


def _serve_binary(request, data, version_key: str):
    if not data:
        raise Http404("Görüntü yok.")
    etag = '"%s"' % hashlib.sha1(version_key.encode()).hexdigest()[:20]
    if request.headers.get("If-None-Match") == etag:
        return HttpResponseNotModified()
    resp = HttpResponse(bytes(data), content_type="image/jpeg")
    resp["ETag"] = etag
    # URL'de ?v=<image_version> olduğu için güvenle uzun önbelleklenebilir.
    resp["Cache-Control"] = "private, max-age=86400"
    return resp


@login_required
def tour_scene_image(request, pk):
    scene = TourScene.objects.filter(pk=pk).only("panorama", "image_version").first()
    if scene is None:
        raise Http404
    return _serve_binary(request, scene.panorama, "p%s-%s" % (pk, scene.image_version))


@login_required
def tour_scene_thumb(request, pk):
    scene = TourScene.objects.filter(pk=pk).only("thumbnail", "image_version").first()
    if scene is None:
        raise Http404
    return _serve_binary(request, scene.thumbnail, "t%s-%s" % (pk, scene.image_version))


@login_required
def tour_scene_status(request, pk):
    scene = TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(pk=pk).first()
    if scene is None:
        return JsonResponse({"ok": False, "error": "Sahne bulunamadı."}, status=404)
    d = _scene_dict(scene)
    d["frame_count"] = scene.frames.count()
    return JsonResponse({"ok": True, "scene": d})


@login_required
def tour_hotspot_info(request, pk):
    """Hotspot tıklama paneli: sensör anlık değeri + son kalibrasyon."""
    h = _hotspot_qs().select_related("sensor__latest__status").filter(pk=pk).first()
    if h is None:
        return JsonResponse({"ok": False, "error": "İşaret bulunamadı."}, status=404)
    data = _hotspot_dict(h)
    if h.sensor_id:
        latest = getattr(h.sensor, "latest", None)
        if latest is not None:
            data.update({
                "value": latest.value,
                "status": str(latest.status) if latest.status_id else "",
                "quality": latest.quality,
                "readtime": timezone.localtime(latest.readtime).strftime("%d.%m.%Y %H:%M:%S")
                if latest.readtime else "",
                "readtime_ago": _humanize_ago_tr(latest.readtime),
            })
        cal = (Calibration.objects.filter(sensor_id=h.sensor_id)
               .order_by("-time_iso").only("time_iso", "is_valid").first())
        if cal:
            data["last_calibration"] = timezone.localtime(cal.time_iso).strftime("%d.%m.%Y %H:%M")
            data["last_calibration_valid"] = cal.is_valid
        data["station_id"] = h.scene.station_id
        data["parameter_id"] = h.sensor.parameter_id
    keys = {k for k in (h.equipment_key,
                        sensor_item_key(h.sensor_id) if h.sensor_id else "") if k}
    data["photos"] = ([_photo_dict(ph) for ph in _photos_qs()
                       .filter(station_id=h.scene.station_id, item_key__in=keys)[:24]]
                      if keys else [])
    return JsonResponse({"ok": True, "hotspot": data})


# --------------------------------------------------------------------------- #
# Yazma uçları (operatör + admin)
# --------------------------------------------------------------------------- #

@login_required
@require_POST
def tour_scene_save(request):
    denied = _require_operator(request)
    if denied:
        return denied
    p = _json_body(request)
    name = (p.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Sahne adı zorunlu."}, status=400)
    if p.get("id"):
        scene = TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(pk=p["id"]).first()
        if scene is None:
            return JsonResponse({"ok": False, "error": "Sahne bulunamadı."}, status=404)
        created = False
    else:
        station = Station.objects.filter(pk=p.get("station_id") or 0).first()
        if station is None:
            return JsonResponse({"ok": False, "error": "Geçerli bir tesis seçin."}, status=400)
        next_order = (TourScene.objects.filter(station=station).count())
        scene = TourScene(station=station, created_by=request.user, order=next_order)
        created = True
    scene.name = name[:120]
    scene.description = (p.get("description") or "").strip()
    if "order" in p:
        scene.order = max(0, int(_float(p.get("order"), scene.order)))
    if "initial_yaw" in p:
        scene.initial_yaw = _float(p.get("initial_yaw"))
        scene.initial_pitch = _float(p.get("initial_pitch"))
    if created:
        scene.save()
    else:
        scene.save(update_fields=["name", "description", "order", "initial_yaw",
                                  "initial_pitch", "updated_at"])
    log_event(EventType.CONFIG, "Tur sahnesi %s: %s" % ("eklendi" if created else "güncellendi",
                                                        scene.name),
              request=request, station=scene.station)
    return JsonResponse({"ok": True, "scene": _scene_dict(scene)})


@login_required
@require_POST
def tour_scene_delete(request):
    denied = _require_operator(request)
    if denied:
        return denied
    p = _json_body(request)
    scene = TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(pk=p.get("id") or 0).first()
    if scene is None:
        return JsonResponse({"ok": False, "error": "Sahne bulunamadı."}, status=404)
    name, station = scene.name, scene.station
    scene.delete()
    log_event(EventType.CONFIG, "Tur sahnesi silindi: %s" % name, request=request, station=station)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def tour_frames_reset(request, pk):
    """Yeni çekime başlarken eski kareleri temizle."""
    denied = _require_operator(request)
    if denied:
        return denied
    scene = get_object_or_404(TourScene.objects.defer(*TourScene.LIGHT_DEFER), pk=pk)
    TourFrame.objects.filter(scene=scene).delete()
    if scene.status != TourScene.Status.READY:
        TourScene.objects.filter(pk=pk).update(status=TourScene.Status.CAPTURING, error="")
    return JsonResponse({"ok": True})


@login_required
@require_POST
def tour_frame_upload(request, pk):
    """Mobil çekimden tek kare (multipart: image + yaw/pitch/roll/hfov/seq)."""
    from . import tour_stitch as ts

    denied = _require_operator(request)
    if denied:
        return denied
    scene = get_object_or_404(TourScene.objects.defer(*TourScene.LIGHT_DEFER), pk=pk)
    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"ok": False, "error": "Kare dosyası yok."}, status=400)
    if _upload_too_big(upload):
        return JsonResponse({"ok": False, "error": "Kare çok büyük."}, status=413)
    seq = int(_float(request.POST.get("seq"), 0))
    if TourFrame.objects.filter(scene=scene).exclude(seq=seq).count() >= settings.TOUR_MAX_FRAMES:
        return JsonResponse({"ok": False, "error": "Sahne başına kare sınırı aşıldı."}, status=400)
    try:
        data, w, h = ts.normalize_jpeg(upload.read(), settings.TOUR_MAX_FRAME_PX)
    except ts.StitchError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    has_or = request.POST.get("has_orientation") in ("1", "true", "True")
    # Aynı seq tekrar gelirse (ağ yeniden denemesi) üzerine yaz → mükerrer kare olmaz.
    with transaction.atomic():
        TourFrame.objects.filter(scene=scene, seq=seq).delete()
        TourFrame.objects.create(
            scene=scene, seq=seq, image=data, width=w, height=h,
            yaw=_float(request.POST.get("yaw")), pitch=_float(request.POST.get("pitch")),
            roll=_float(request.POST.get("roll")), hfov=_float(request.POST.get("hfov"), 60.0),
            has_orientation=has_or,
        )
    count = TourFrame.objects.filter(scene=scene).count()
    return JsonResponse({"ok": True, "seq": seq, "frame_count": count})


@login_required
@require_POST
def tour_scene_finish(request, pk):
    """Çekimi bitir → birleştirme task'ını kuyruğa al."""
    from .tasks import stitch_tour_scene

    denied = _require_operator(request)
    if denied:
        return denied
    scene = get_object_or_404(TourScene.objects.defer(*TourScene.LIGHT_DEFER), pk=pk)
    if not scene.frames.exists():
        return JsonResponse({"ok": False, "error": "Hiç kare çekilmedi."}, status=400)
    TourScene.objects.filter(pk=pk).update(status=TourScene.Status.PROCESSING, error="",
                                           source=TourScene.Source.GUIDED)
    stitch_tour_scene.delay(scene.pk)
    log_event(EventType.CONFIG, "Tur sahnesi çekimi tamamlandı: %s" % scene.name,
              request=request, station=scene.station)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def tour_pano_upload(request, pk):
    """Hazır panorama yükle (telefonun Panorama / Photo Sphere çıktısı)."""
    from . import tour_stitch as ts

    denied = _require_operator(request)
    if denied:
        return denied
    scene = get_object_or_404(TourScene.objects.defer(*TourScene.LIGHT_DEFER), pk=pk)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"ok": False, "error": "Dosya seçilmedi."}, status=400)
    if _upload_too_big(upload):
        return JsonResponse({"ok": False, "error": "Dosya %s MB sınırını aşıyor."
                             % settings.TOUR_MAX_UPLOAD_MB}, status=413)
    hspan = _float(request.POST.get("hspan"), 0) or None
    try:
        pano = ts.prepare_uploaded_panorama(upload.read(), hspan, settings.TOUR_PANO_WIDTH)
        jpeg = ts.encode_jpeg(pano, 85)
        thumb = ts.make_thumbnail(pano)
    except ts.StitchError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    h, w = pano.shape[:2]
    TourScene.objects.filter(pk=pk).update(
        panorama=jpeg, thumbnail=thumb, pano_width=w, pano_height=h,
        status=TourScene.Status.READY, source=TourScene.Source.UPLOAD,
        stitch_method="upload", error="", image_version=scene.image_version + 1,
        updated_at=timezone.now(),
    )
    log_event(EventType.CONFIG, "Tur sahnesine panorama yüklendi: %s" % scene.name,
              request=request, station=scene.station)
    scene.refresh_from_db()
    return JsonResponse({"ok": True, "scene": _scene_dict(scene)})


_HOTSPOT_TEXT_FIELDS = {"title": 150, "brand": 100, "model_name": 100, "serial_no": 100}


@login_required
@require_POST
def tour_hotspot_save(request):
    denied = _require_operator(request)
    if denied:
        return denied
    p = _json_body(request)
    if p.get("id"):
        h = TourHotspot.objects.filter(pk=p["id"]).select_related("scene").first()
        if h is None:
            return JsonResponse({"ok": False, "error": "İşaret bulunamadı."}, status=404)
    else:
        scene = TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(pk=p.get("scene_id") or 0).first()
        if scene is None:
            return JsonResponse({"ok": False, "error": "Sahne bulunamadı."}, status=404)
        h = TourHotspot(scene=scene, created_by=request.user)
    kind = p.get("kind", h.kind)
    if kind not in TourHotspot.Kind.values:
        return JsonResponse({"ok": False, "error": "Geçersiz işaret türü."}, status=400)
    h.kind = kind
    if "yaw" in p:
        h.yaw = _float(p.get("yaw"))
        h.pitch = max(-90.0, min(90.0, _float(p.get("pitch"))))
    if h.yaw is None:
        return JsonResponse({"ok": False, "error": "Konum eksik."}, status=400)
    for field, limit in _HOTSPOT_TEXT_FIELDS.items():
        if field in p:
            setattr(h, field, (p.get(field) or "").strip()[:limit])
    if "description" in p:
        h.description = (p.get("description") or "").strip()
    if "equipment_key" in p:
        h.equipment_key = (p.get("equipment_key") or "").strip()[:64]

    station_id = h.scene.station_id
    if "sensor_id" in p:
        sid = p.get("sensor_id") or None
        if sid and not Sensor.objects.filter(pk=sid, connection__station_id=station_id).exists():
            return JsonResponse({"ok": False, "error": "Sensör bu tesise ait değil."}, status=400)
        h.sensor_id = sid
    if "target_scene_id" in p:
        tid = p.get("target_scene_id") or None
        if tid and (int(tid) == h.scene_id or not TourScene.objects.filter(
                pk=tid, station_id=station_id).exists()):
            return JsonResponse({"ok": False, "error": "Geçersiz hedef sahne."}, status=400)
        h.target_scene_id = tid
    if "document_id" in p:
        did = p.get("document_id") or None
        if did and not Document.objects.filter(pk=did).exists():
            return JsonResponse({"ok": False, "error": "Doküman bulunamadı."}, status=400)
        h.document_id = did

    if h.kind == TourHotspot.Kind.SENSOR and not h.sensor_id:
        return JsonResponse({"ok": False, "error": "Sensör işareti için sensör seçin."}, status=400)
    if h.kind == TourHotspot.Kind.LINK and not h.target_scene_id:
        return JsonResponse({"ok": False, "error": "Geçiş için hedef sahne seçin."}, status=400)
    if p.get("confirm"):
        h.confirmed = True
    h.save()
    h = _hotspot_qs().get(pk=h.pk)
    return JsonResponse({"ok": True, "hotspot": _hotspot_dict(h)})


@login_required
@require_POST
def tour_hotspot_delete(request):
    denied = _require_operator(request)
    if denied:
        return denied
    p = _json_body(request)
    deleted, _ = TourHotspot.objects.filter(pk=p.get("id") or 0).delete()
    if not deleted:
        return JsonResponse({"ok": False, "error": "İşaret bulunamadı."}, status=404)
    return JsonResponse({"ok": True})


# =========================================================================== #
# Ekipman fotoğrafları (yönlendirmeli çekim listesi)
# =========================================================================== #

def _photo_dict(ph: EquipmentPhoto) -> dict:
    from django.urls import reverse

    return {
        "id": ph.pk,
        "item_key": ph.item_key,
        "shot_key": ph.shot_key,
        "label": ph.label,
        "caption": ph.caption,
        "sensor_id": ph.sensor_id,
        "brand": ph.brand,
        "model_name": ph.model_name,
        "serial_no": ph.serial_no,
        "ai_status": ph.ai_status,
        "ai_text": ph.ai_text,
        "width": ph.width,
        "height": ph.height,
        "image_url": reverse("dashboard:api_tour_photo_image", args=[ph.pk]),
        "thumb_url": reverse("dashboard:api_tour_photo_thumb", args=[ph.pk]),
        "created_at": timezone.localtime(ph.created_at).strftime("%d.%m.%Y %H:%M")
        if ph.created_at else "",
        "created_by": (ph.created_by.get_full_name() or ph.created_by.username)
        if ph.created_by_id else "",
    }


def _photos_qs():
    return EquipmentPhoto.objects.defer(*EquipmentPhoto.LIGHT_DEFER).select_related("created_by")


def _photo_state(station) -> dict:
    """Çekim listesi + ilerleme + fotoğraflar (masaüstü sekme ve mobil sihirbaz ortak)."""
    photos = list(_photos_qs().filter(station=station))
    items = checklist_with_progress(station, photos)
    known = {it["key"] for it in items}
    by_item: dict[str, list] = {}
    for ph in photos:
        key = ph.item_key if ph.item_key in known else CUSTOM_KEY
        by_item.setdefault(key, []).append(_photo_dict(ph))
    total = sum(len(it["shots"]) for it in items)
    done = sum(it["done_shots"] for it in items)
    return {
        "items": items,
        "photos": by_item,
        "categories": [{"key": k, "label": str(v)} for k, v in CATEGORIES],
        "progress": {"done": done, "total": total,
                     "percent": int(round(100.0 * done / total)) if total else 0,
                     "photo_count": len(photos)},
    }


class PhotoCaptureView(OperatorRequiredMixin, TemplateView):
    """Mobil ekipman fotoğrafı sihirbazı — çekim listesi üzerinden sırayla yönlendirir.

    `<input type=file capture=environment>` kullandığı için HTTPS GEREKTİRMEZ
    (canlı kamera akışı istemez; telefonun kendi kamera uygulamasını açar).
    """

    template_name = "dashboard/tour/photo_capture.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["station"] = get_object_or_404(Station, pk=self.kwargs["station_id"])
        ctx["max_px"] = settings.TOUR_MAX_FRAME_PX
        ctx["ai_enabled"] = _ai_enabled()
        return ctx


@login_required
def tour_photos(request):
    try:
        station_id = int(request.GET.get("station") or 0)
    except ValueError:
        station_id = 0
    station = get_object_or_404(Station, pk=station_id)
    return JsonResponse({"ok": True, **_photo_state(station)})


@login_required
def tour_photo_image(request, pk):
    ph = EquipmentPhoto.objects.filter(pk=pk).only("image").first()
    if ph is None:
        raise Http404
    resp = _serve_binary(request, ph.image, "e%s" % pk)
    if request.GET.get("download") and resp.status_code == 200:
        resp["Content-Disposition"] = 'attachment; filename="ekipman-%s.jpg"' % pk
    return resp


@login_required
def tour_photo_thumb(request, pk):
    ph = EquipmentPhoto.objects.filter(pk=pk).only("thumbnail").first()
    if ph is None:
        raise Http404
    return _serve_binary(request, ph.thumbnail, "et%s" % pk)


@login_required
@require_POST
def tour_photo_upload(request):
    """Tek ekipman fotoğrafı (multipart: file, station_id, item_key, shot_key, label, caption)."""
    from . import tour_stitch as ts

    denied = _require_operator(request)
    if denied:
        return denied
    station = Station.objects.filter(pk=request.POST.get("station_id") or 0).first()
    if station is None:
        return JsonResponse({"ok": False, "error": "Tesis bulunamadı."}, status=400)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"ok": False, "error": "Fotoğraf seçilmedi."}, status=400)
    if _upload_too_big(upload):
        return JsonResponse({"ok": False, "error": "Dosya %s MB sınırını aşıyor."
                             % settings.TOUR_MAX_UPLOAD_MB}, status=413)
    item_key = (request.POST.get("item_key") or CUSTOM_KEY).strip()[:64]
    shot_key = (request.POST.get("shot_key") or "").strip()[:32]
    sensor_id = None
    if item_key.startswith("sensor:"):
        try:
            sid = int(item_key.split(":", 1)[1])
        except ValueError:
            return JsonResponse({"ok": False, "error": "Geçersiz madde."}, status=400)
        if not Sensor.objects.filter(pk=sid, connection__station=station).exists():
            return JsonResponse({"ok": False, "error": "Sensör bu tesise ait değil."}, status=400)
        sensor_id = sid
    try:
        # Etiket plakası okunabilsin diye çekim karesinden daha yüksek çözünürlük (2x).
        img = ts.limit_size(ts.decode_image(upload.read()), settings.TOUR_MAX_FRAME_PX * 2)
        data = ts.encode_jpeg(img, 88)
        thumb = ts.make_thumbnail(img, 360)
    except ts.StitchError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    h, w = img.shape[:2]
    ph = EquipmentPhoto.objects.create(
        station=station, item_key=item_key, shot_key=shot_key, sensor_id=sensor_id,
        label=(request.POST.get("label") or "").strip()[:150],
        caption=(request.POST.get("caption") or "").strip(),
        image=data, thumbnail=thumb, width=w, height=h, created_by=request.user,
    )
    return JsonResponse({"ok": True, "photo": _photo_dict(_photos_qs().get(pk=ph.pk))})


@login_required
@require_POST
def tour_photo_save(request):
    denied = _require_operator(request)
    if denied:
        return denied
    p = _json_body(request)
    ph = _photos_qs().filter(pk=p.get("id") or 0).first()
    if ph is None:
        return JsonResponse({"ok": False, "error": "Fotoğraf bulunamadı."}, status=404)
    for field, limit in (("label", 150), ("brand", 100), ("model_name", 100), ("serial_no", 100)):
        if field in p:
            setattr(ph, field, (p.get(field) or "").strip()[:limit])
    if "caption" in p:
        ph.caption = (p.get("caption") or "").strip()
    ph.save(update_fields=["label", "brand", "model_name", "serial_no", "caption"])
    return JsonResponse({"ok": True, "photo": _photo_dict(ph)})


@login_required
@require_POST
def tour_photo_delete(request):
    denied = _require_operator(request)
    if denied:
        return denied
    p = _json_body(request)
    deleted, _ = EquipmentPhoto.objects.filter(pk=p.get("id") or 0).delete()
    if not deleted:
        return JsonResponse({"ok": False, "error": "Fotoğraf bulunamadı."}, status=404)
    return JsonResponse({"ok": True})


# =========================================================================== #
# AI (Claude vision) — sahne hotspot önerisi + etiket plakası okuma
# =========================================================================== #

def _ai_gate(request):
    denied = _require_operator(request)
    if denied:
        return denied
    if not _ai_enabled():
        return JsonResponse({"ok": False, "error": "AI anahtarı tanımlı değil."}, status=400)
    from api.licensing import license_active
    if not license_active():
        return JsonResponse({"ok": False, "error": "Lisans aktif değil."}, status=403)
    return None


@login_required
@require_POST
def tour_ai_suggest(request, pk):
    from .tasks import ai_suggest_hotspots

    denied = _ai_gate(request)
    if denied:
        return denied
    scene = get_object_or_404(TourScene.objects.defer(*TourScene.LIGHT_DEFER), pk=pk)
    if scene.ai_status == "running":
        return JsonResponse({"ok": True, "already": True})
    if not scene.frames.filter(has_orientation=True).exists():
        return JsonResponse({"ok": False, "error": "Bu sahnede yönelimli çekim karesi yok — AI önerisi "
                             "yalnız telefonla yönlendirmeli çekilmiş sahnelerde çalışır."}, status=400)
    TourScene.objects.filter(pk=pk).update(ai_status="running", ai_error="")
    ai_suggest_hotspots.delay(scene.pk, request.user.pk)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def tour_photo_ai_read(request, pk):
    from .tasks import ai_read_nameplate

    denied = _ai_gate(request)
    if denied:
        return denied
    ph = _photos_qs().filter(pk=pk).first()
    if ph is None:
        return JsonResponse({"ok": False, "error": "Fotoğraf bulunamadı."}, status=404)
    hint = ""
    item = next((it for it in build_checklist(ph.station) if it["key"] == ph.item_key), None)
    if item:
        hint = item["title"]
    EquipmentPhoto.objects.filter(pk=pk).update(ai_status="running")
    ai_read_nameplate.delay(ph.pk, hint, request.user.pk)
    return JsonResponse({"ok": True})


@login_required
def tour_photo_get(request, pk):
    ph = _photos_qs().filter(pk=pk).first()
    if ph is None:
        return JsonResponse({"ok": False, "error": "Fotoğraf bulunamadı."}, status=404)
    d = _photo_dict(ph)
    d["ai_text"] = ph.ai_text
    return JsonResponse({"ok": True, "photo": d})
