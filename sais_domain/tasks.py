"""
Bakanlık SIM ve Envisoft'a periyodik veri gönderim Celery task'ları.

Akış::

    publish_minute_data         (beat: cron */1 * * * *)
        └─ aktif kabinler  →  publish_cabinet_data.delay(cabinet_id)
                ├─ SaisSimClient.send_data(...)
                └─ EnvisoftClient.send_data(...)

Tek kabinin yavaş yanıtı diğerlerini geciktirmesin diye fan-out yapılır.
İki dış sistem (SIM + Envisoft) bağımsızdır — biri fail ederse diğeri
yine denenir. Tüm HTTP çağrıları ``BaseHttpClient`` üzerinden geçtiği
için ``ApiLog(direction='out')`` satırları otomatik yazılır.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .clients import EnvisoftClient, SaisClientError, SaisSimClient
from .models import SaisCabinet, SystemSwitch
from .services import build_envisoft_rows, build_sim_payload


logger = logging.getLogger("sais_domain.tasks")


@shared_task(name="sais_domain.tasks.publish_minute_data")
def publish_minute_data() -> dict:
    """Aktif kabinler için per-cabinet publish task'ları enqueue eder.

    Beat tarafından her dakika başında çağrılır. Tek tek kabinler için
    fan-out — bir kabinin yavaş yanıtı diğerlerini geciktirmez.
    """
    cabinet_ids = list(
        SaisCabinet.objects
        .filter(station__active=True)
        .values_list("id", flat=True)
    )
    for cabinet_id in cabinet_ids:
        publish_cabinet_data.delay(cabinet_id)
    logger.info("publish_minute_data: %d cabinet enqueue edildi", len(cabinet_ids))
    return {"dispatched": len(cabinet_ids)}


@shared_task(name="sais_domain.tasks.publish_cabinet_data")
def publish_cabinet_data(cabinet_id: int) -> dict:
    """Tek bir kabinin SIM + Envisoft veri gönderimini yapar.

    İki gönderim birbirinden bağımsız çalışır; istisnalar log'lanır ama
    fırlatılmaz — bir kabinin patlaması beat'i etkilemez. Her başarısız
    çağrı zaten ``ApiLog`` üstünde error_message ile görünür durumda.
    """
    cabinet = (
        SaisCabinet.objects
        .select_related("station")
        .filter(pk=cabinet_id)
        .first()
    )
    if cabinet is None:
        logger.warning("publish_cabinet_data: cabinet=%s bulunamadı", cabinet_id)
        return {"cabinet_id": cabinet_id, "skipped": "cabinet bulunamadı"}
    if not cabinet.station_id or not cabinet.station.active:
        return {"cabinet_id": cabinet_id, "skipped": "istasyon pasif"}

    switch = SystemSwitch.load()
    readtime = timezone.localtime()

    sim_result = (
        _publish_sim(cabinet, readtime) if switch.sim_enabled
        else {"sent": False, "reason": "sim_disabled"}
    )
    envisoft_result = (
        _publish_envisoft(cabinet, readtime) if switch.envisoft_enabled
        else {"sent": False, "reason": "envisoft_disabled"}
    )

    return {
        "cabinet_id": cabinet_id,
        "readtime": readtime.strftime("%Y-%m-%dT%H:%M:00"),
        "sim": sim_result,
        "envisoft": envisoft_result,
    }


def _publish_sim(cabinet: SaisCabinet, readtime) -> dict:
    payload = build_sim_payload(cabinet, readtime=readtime)
    if payload.is_empty:
        return {"sent": False, "reason": "no analog values"}
    try:
        with SaisSimClient(cabinet) as client:
            result = client.send_data(
                readtime=payload.readtime,
                values=payload.values,
                period=payload.period,
            )
        return {"sent": True, "values": len(payload.values), "result": result}
    except SaisClientError as exc:
        logger.warning(
            "SIM SendData başarısız (cabinet=%s): %s", cabinet.id, exc,
        )
        return {"sent": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "SIM SendData beklenmedik hata (cabinet=%s)", cabinet.id,
        )
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


def _publish_envisoft(cabinet: SaisCabinet, readtime) -> dict:
    rows = build_envisoft_rows(cabinet, readtime=readtime)
    if not rows:
        return {"sent": False, "reason": "no rows"}
    try:
        with EnvisoftClient(cabinet) as client:
            response = client.send_data(rows)
        return {
            "sent": True,
            "rows": len(rows),
            "status": response.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Envisoft SendData hata (cabinet=%s)", cabinet.id,
        )
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}
