"""
SAIS / Envisoft dakikalık veri gönderim payload builder'ları.

Bu modül cihaz okuma yapmaz; ``SensorLatest`` snapshot'larından
Bakanlık ``SendData`` ve Envisoft ``SendData`` payload'larını üretir.
Network / HTTP işi ``sais_domain.clients`` ve ``sais_domain.tasks``
katmanlarına aittir.

Bu ayrım test edilebilirliği için önemli: payload üretimi DB read-only
ve deterministik — task içinde ayrıca mock'lamadan birim test yazılabilir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db.models import QuerySet

from api.models import SensorLatest

from .models import SaisCabinet


# Dakika başı ISO formatı — saniye HH:MM:00 olarak sıfırlanır.
# Bakanlık SendData spec'i ve Envisoft platformu birebir bu formatı bekliyor;
# ``datetime.now().strftime('%Y-%m-%dT%H:%M:00')`` ile birebir uyumlu.
_READTIME_FMT = "%Y-%m-%dT%H:%M:00"


# ``Sensor.sensor_type`` → Envisoft ``type`` kodu.
# Envisoft API tarihsel kodları:
#   1 = Analog (input/output)
#   2 = Digital input
#   4 = Digital output (DOUT-* protokol grubu)
_ENVISOFT_TYPE_MAP: dict[int, int] = {
    0: 1,  # Analog Input
    1: 1,  # Analog Output
    2: 2,  # Digital Input
    3: 4,  # Digital Output
}


def _format_readtime(when: datetime) -> str:
    return when.strftime(_READTIME_FMT)


def _cabinet_sensor_snapshots(cabinet: SaisCabinet) -> QuerySet[SensorLatest]:
    """Bir kabinin istasyonuna ait, parametreye bağlı, aktif sensörlerin snapshot'ları.

    ``Connection.station`` üstünden filtreler — istasyon altındaki tüm
    bağlantıların aktif sensörlerini kapsar. ``parameter`` null olan
    teknik sensörler (örn. cabin_temp standalone'sa) dışarıda bırakılır.
    """
    return (
        SensorLatest.objects
        .filter(
            sensor__connection__station_id=cabinet.station_id,
            sensor__is_active=True,
            sensor__parameter__isnull=False,
        )
        .select_related(
            "sensor",
            "sensor__parameter",
            "sensor__connection",
            "status",
        )
    )


# --------------------------------------------------------------------- SIM


@dataclass
class SimSendDataPayload:
    """Bakanlık ``/SAIS/SendData`` JSON gövdesi için dakikalık snapshot."""

    readtime: str
    values: dict[str, Any] = field(default_factory=dict)
    period: int = 1

    @property
    def is_empty(self) -> bool:
        return not self.values


def build_sim_payload(
    cabinet: SaisCabinet,
    *,
    readtime: datetime,
    period: int | None = None,
) -> SimSendDataPayload:
    """Bakanlık SIM ``/SAIS/SendData`` payload'ını snapshot'lardan üretir.

    Sadece **analog** sensörler dahil edilir (``sensor_type`` 0 ve 1).
    Her sensör için iki anahtar yazılır:

    - ``{parameter_name}``        → engineering value (``None`` → ``-9999``)
    - ``{parameter_name}_Status`` → ``StatusCode.code`` (yoksa ``0``)

    ``-9999`` Bakanlık'ın "geçersiz/eksik veri" sentinel'idir; orijinal
    istasyon kodundan korunmuştur.

    Aynı parametreye birden fazla sensör eşleşirse — pratik olmasa da —
    iteration sırasında son okunan kazanır.
    """
    values: dict[str, Any] = {}
    qs = _cabinet_sensor_snapshots(cabinet).filter(
        sensor__sensor_type__in=(0, 1),
    )
    for snap in qs:
        param = snap.sensor.parameter
        param_name = (param.parameter_name or param.parameter_txt or "").strip()
        if not param_name:
            continue
        value = snap.value if snap.value is not None else -9999
        status_code = snap.status.code if snap.status_id and snap.status.code is not None else 0
        values[param_name] = value
        values[f"{param_name}_Status"] = status_code

    return SimSendDataPayload(
        readtime=_format_readtime(readtime),
        values=values,
        period=period or cabinet.data_period or 1,
    )


# ---------------------------------------------------------------- Envisoft


# Envisoft satır tuple sıralaması (legacy şema; payload server tarafında
# pozisyonel parse edilir — alan sırası ASLA değiştirilmemeli):
#   0  station_id   — ``cabinet.station_id`` (fiziksel istasyon PK)
#   1  type         — 1=analog, 2=DI, 4=DO
#   2  channel_id   — Parameter.channel_number, yoksa Sensor.id
#   3  time         — HH:MM:00 ISO
#   4  device       — sensor brand/model
#   5  parameter    — parameter_name
#   6  raw          — engineering value (orijinal kodda da raw == value)
#   7  signal_type  — Sensor.signal_type (str)
#   8  status       — StatusCode.code
#   9  units        — parameter unit
#   10 value        — engineering value (raw ile aynı)
#   11 zaman        — HH:MM:00 ISO (time ile aynı)
EnvisoftRow = tuple[
    int, int, int, str, str, str, Any, str, int, str, Any, str
]


def build_envisoft_rows(
    cabinet: SaisCabinet,
    *,
    readtime: datetime,
) -> list[EnvisoftRow]:
    """Envisoft ``/SendData`` payload'ı için satır listesi üretir.

    - **Analog** sensörler her dakika gönderilir.
    - **Dijital** sensörler sadece o dakika içinde güncelleme almışsa
      gönderilir (orijinal istasyon kodundaki "dijital dataları sürekli
      kaydetmemek için" optimizasyonu birebir korunmuştur — Envisoft
      tarafında dijital event-driven kabul edilir).

    Snapshot ``readtime``'ı yoksa o sensör atlanır (henüz hiç okunmamış).
    """
    readtime_str = _format_readtime(readtime)
    rows: list[EnvisoftRow] = []

    for snap in _cabinet_sensor_snapshots(cabinet):
        sensor = snap.sensor
        param = sensor.parameter
        type_code = _ENVISOFT_TYPE_MAP.get(sensor.sensor_type)
        if type_code is None:
            continue

        # Dijital → sadece bu dakika değişmişse gönder.
        if type_code != 1:
            if not snap.readtime:
                continue
            if _format_readtime(snap.readtime) != readtime_str:
                continue

        channel_id = param.channel_number or sensor.id
        value = snap.value if snap.value is not None else -9999
        status_code = (
            snap.status.code
            if snap.status_id and snap.status.code is not None
            else 0
        )
        device = (sensor.brand or sensor.model or "").strip()
        parameter_name = (param.parameter_name or param.parameter_txt or "").strip()
        signal_type = sensor.signal_type if sensor.signal_type is not None else 0
        units = (param.unit_txt or param.unit or "").strip()

        rows.append((
            cabinet.station_id,
            type_code,
            int(channel_id),
            readtime_str,
            str(device),
            str(parameter_name),
            value,
            str(signal_type),
            int(status_code),
            str(units),
            value,
            readtime_str,
        ))
    return rows
