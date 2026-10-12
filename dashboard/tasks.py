"""Dashboard Celery task'ları — Tesis Sanal Turu panorama birleştirme + AI öneri.

Görüntüler DB'de olduğundan task worker'da dosya sistemi paylaşımı gerektirmez
(bkz. `dashboard.models.TourScene` notu).
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def run_stitch(scene_id: int) -> str:
    """Sahnenin karelerini birleştirip panoramayı yazar. Durum metni döndürür.

    Task'tan ayrı tutuldu: testler ve debug için senkron çağrılabilir.
    """
    from django.conf import settings

    from dashboard import tour_stitch as ts
    from dashboard.models import TourFrame, TourScene

    scene = TourScene.objects.defer(*TourScene.LIGHT_DEFER).filter(pk=scene_id).first()
    if scene is None:
        return "missing"
    try:
        frames = list(TourFrame.objects.filter(scene=scene).order_by("seq", "id"))
        if not frames:
            raise ts.StitchError("Hiç kare yüklenmemiş.")
        if ts.cv2 is not None:
            ts.cv2.setNumThreads(2)  # polling ile paylaşılan worker'ı boğma
        oriented = [f for f in frames if f.has_orientation]
        if len(oriented) >= max(3, len(frames) // 2):
            pano = ts.stitch_by_orientation(
                [{"img": ts.decode_image(f.image), "yaw": f.yaw, "pitch": f.pitch,
                  "roll": f.roll, "hfov": f.hfov} for f in oriented],
                out_width=settings.TOUR_PANO_WIDTH,
            )
            method = "orientation"
        else:
            hfov = next((f.hfov for f in frames if f.hfov), 60.0)
            pano = ts.stitch_by_features(
                [ts.decode_image(f.image) for f in frames], hfov=hfov,
                out_width=settings.TOUR_PANO_WIDTH,
            )
            method = "opencv"
        h, w = pano.shape[:2]
        TourScene.objects.filter(pk=scene.pk).update(
            panorama=ts.encode_jpeg(pano, 85), thumbnail=ts.make_thumbnail(pano),
            pano_width=w, pano_height=h, status=TourScene.Status.READY,
            stitch_method=method, error="", image_version=scene.image_version + 1,
        )
        return "ready"
    except ts.StitchError as exc:
        TourScene.objects.filter(pk=scene.pk).update(
            status=TourScene.Status.FAILED, error=str(exc))
        return "failed"
    except Exception as exc:  # beklenmeyen — sahne "birleştiriliyor"da asılı kalmasın
        logger.exception("Tur sahnesi %s birleştirilemedi", scene_id)
        TourScene.objects.filter(pk=scene.pk).update(
            status=TourScene.Status.FAILED, error="Beklenmeyen hata: %s" % exc)
        return "failed"


@shared_task(name="dashboard.tasks.stitch_tour_scene", time_limit=900, soft_time_limit=840)
def stitch_tour_scene(scene_id: int) -> str:
    return run_stitch(scene_id)


def _user(user_id):
    """ApiLog `triggered_by` için kullanıcı nesnesi (task'a yalnız id taşınır)."""
    if not user_id:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=user_id).first()


@shared_task(name="dashboard.tasks.ai_suggest_hotspots", time_limit=900, soft_time_limit=840)
def ai_suggest_hotspots(scene_id: int, user_id: int | None = None) -> str:
    """Sahne kareleri → Claude vision → onaysız hotspot önerileri."""
    from dashboard.models import TourScene
    from dashboard.tour_ai import AIUnavailable, suggest_hotspots

    scene = (TourScene.objects.defer(*TourScene.LIGHT_DEFER).select_related("station")
             .filter(pk=scene_id).first())
    if scene is None:
        return "missing"
    try:
        added, inventory = suggest_hotspots(scene, triggered_by=_user(user_id))
    except AIUnavailable as exc:
        TourScene.objects.filter(pk=scene_id).update(ai_status="failed", ai_error=str(exc))
        return "failed"
    except Exception as exc:
        logger.exception("AI hotspot önerisi başarısız (sahne %s)", scene_id)
        TourScene.objects.filter(pk=scene_id).update(ai_status="failed",
                                                     ai_error="Beklenmeyen hata: %s" % exc)
        return "failed"
    TourScene.objects.filter(pk=scene_id).update(ai_status="done", ai_error="", ai_added=added,
                                                 ai_inventory=inventory)
    return "done"


@shared_task(name="dashboard.tasks.ai_read_nameplate", time_limit=300, soft_time_limit=270)
def ai_read_nameplate(photo_id: int, hint: str = "", user_id: int | None = None) -> str:
    """Ekipman fotoğrafı → etiket plakası (marka/model/seri no)."""
    from dashboard.models import EquipmentPhoto
    from dashboard.tour_ai import AIUnavailable, read_nameplate

    photo = EquipmentPhoto.objects.filter(pk=photo_id).first()
    if photo is None:
        return "missing"
    try:
        read_nameplate(photo, hint=hint, triggered_by=_user(user_id))
        photo.ai_status = "done"
    except AIUnavailable as exc:
        photo.ai_status, photo.ai_text = "failed", str(exc)
    except Exception as exc:
        logger.exception("Etiket okuma başarısız (foto %s)", photo_id)
        photo.ai_status, photo.ai_text = "failed", "Beklenmeyen hata: %s" % exc
    photo.save(update_fields=["brand", "model_name", "serial_no", "ai_text", "ai_status"])
    return photo.ai_status
