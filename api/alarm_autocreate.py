"""Sensör eklenince otomatik alarm tanımı oluşturma.

İstasyona belirli parametreler sensör olarak eklendiğinde, ilgili alarm kuralı
otomatik kurulur (kullanıcı tablodan toggle ile pasifleştirebilir/silebilir).

- Analog: pH, İletkenlik, Çözünmüş Oksijen, KOİ, AKM → limit alarmı.
  Min/Max = Parameter.gec_min / gec_max (geçerli veri aralığı).
- Dijital: Numune Alma, Duman, Su Baskını, Bakım sensörleri → durum (ON) alarmı.

Mesaj, varsa eşleşen hazır mesajdan (`MessageTemplate`) alınır; yoksa üretilir.
Idempotent: aynı istasyon+parametre (analog) / istasyon+sensör (dijital) için
ikinci kez kurmaz.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_save

logger = logging.getLogger("api.alarm_autocreate")

# parameter_name (makine adı) kümeleri — seed_initial_data ile uyumlu.
AUTO_ANALOG = {"pH", "Iletkenlik", "CozunmusOksijen", "KOi", "AKM"}
AUTO_DIGITAL = {
    "NumuneAlma", "NumuneAnlik", "Duman", "SuBasti",
    "Bakim", "İstasyonBakimda", "TesisBakimda", "BakimModu",
}

# parameter_name → hazır mesaj başlığı (MessageTemplate.title).
MSG_TITLE = {
    "pH": "pH Limit Aşımı",
    "Iletkenlik": "İletkenlik Limit Aşımı",
    "CozunmusOksijen": "Çözünmüş Oksijen Limit Aşımı",
    "KOi": "KOİ Limit Aşımı",
    "AKM": "AKM Limit Aşımı",
    "Duman": "Duman Algılandı",
    "SuBasti": "Su Baskını",
    "NumuneAlma": "Numune Alma",
    "NumuneAnlik": "Numune Alma",
    "Bakim": "Bakım Modu Aktif",
    "İstasyonBakimda": "Bakım Modu Aktif",
    "TesisBakimda": "Bakım Modu Aktif",
    "BakimModu": "Bakım Modu Aktif",
}


def _message_for(pname: str, fallback: str) -> str:
    from .models import MessageTemplate

    title = MSG_TITLE.get(pname)
    if title:
        t = MessageTemplate.objects.filter(title=title).first()
        if t and t.body:
            return t.body
    return fallback


def _create_for_sensor(sensor) -> None:
    from .models import AlarmRule

    param = sensor.parameter
    if param is None:
        return
    connection = sensor.connection
    station = connection.station if connection else None
    if station is None:
        return

    pname = param.parameter_name or ""
    label = param.parameter_txt or pname

    if sensor.sensor_type in (0, 1) and pname in AUTO_ANALOG:
        if AlarmRule.objects.filter(station=station, parameter=param,
                                    rule_type=AlarmRule.RULE_ANALOG).exists():
            return
        msg = _message_for(pname, f"{label} değeri limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz.")
        AlarmRule.objects.create(
            station=station, rule_type=AlarmRule.RULE_ANALOG, parameter=param,
            condition=AlarmRule.COND_MINMAX, min_value=param.gec_min, max_value=param.gec_max,
            period_minutes=60, message=msg, notify_all=True, send_sms=True, send_email=True,
        )
        logger.info("Otomatik analog alarm kuruldu: %s / %s", station, pname)

    elif sensor.sensor_type in (2, 3) and pname in AUTO_DIGITAL:
        if AlarmRule.objects.filter(station=station, sensor=sensor,
                                    rule_type=AlarmRule.RULE_DIGITAL).exists():
            return
        msg = _message_for(pname, f"{label} sensörü aktif olmuştur. Lütfen kontrol ediniz.")
        AlarmRule.objects.create(
            station=station, rule_type=AlarmRule.RULE_DIGITAL, sensor=sensor,
            trigger_state=True, period_minutes=60, message=msg,
            notify_all=True, send_sms=True, send_email=True,
        )
        logger.info("Otomatik dijital alarm kuruldu: %s / %s", station, pname)


def auto_create_alarm(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        _create_for_sensor(instance)
    except Exception:  # noqa: BLE001 — sensör kaydı alarm hatasıyla bozulmasın
        logger.exception("Otomatik alarm oluşturulamadı (sensor=%s)", getattr(instance, "pk", None))


def connect():
    """apps.ready() içinden çağrılır — Sensor post_save signal'ını bağlar."""
    from .models import Sensor
    post_save.connect(auto_create_alarm, sender=Sensor,
                      dispatch_uid="auto_create_alarm_on_sensor")
