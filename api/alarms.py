"""Alarm değerlendirme motoru.

`api.tasks.run_alarms` periyodik task'ı her aktif `AlarmRule`'u `SensorLatest`
anlık değerlerine karşı değerlendirir. Koşul sağlanır ve kuralın `period_minutes`
süresi dolmuşsa aktif kullanıcılara SMS/e-posta gönderir (`api.notifications`),
opsiyonel olarak bir dijital output'u tetikler (`Command`), ve `last_triggered_at`
damgalar. Jenerik SCADA alarmı (SAIS'e özel değil).

Alıcı politikası (kullanıcı talebi): tek istasyon, **tüm aktif kullanıcılar**
yetkili — `notify_all=True` ise herkes, değilse yalnız oluşturan. Kullanıcının
`sms_enabled`/`email_enabled` tercihi + telefon/e-posta varlığı kanal başına
filtreler.
"""
from __future__ import annotations

import logging

from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger("sais_domain.alarms")

# Analog değer bu statuslarda değerlendirilmez (yıkama/bakım/iletişim hatası).
SKIP_STATUS_CODES = {8, 23, 24, 25, 26}


def _recipients(rule) -> list[dict]:
    """Kurala göre alıcıları (isim/telefon/e-posta) çözer."""
    from users.models import CustomUser

    if rule.notify_all:
        users = CustomUser.objects.filter(is_active=True)
    elif rule.created_by_id:
        users = CustomUser.objects.filter(pk=rule.created_by_id, is_active=True)
    else:
        users = CustomUser.objects.none()

    out = []
    for u in users:
        phone = (u.phone_number or "").strip() if (rule.send_sms and u.sms_enabled) else ""
        email = (u.email or "").strip() if (rule.send_email and u.email_enabled) else ""
        if phone or email:
            out.append({
                "name": (u.get_full_name() or u.username).strip(),
                "phone": phone,
                "email": email,
            })
    return out


def _channels(rule) -> list[str]:
    chans = []
    if rule.send_sms:
        chans.append("sms")
    if rule.send_email:
        chans.append("email")
    return chans


def _should_fire(rule, now) -> bool:
    """Periyot throttle — son tetiklemeden bu yana period_minutes geçti mi?"""
    if rule.last_triggered_at is None:
        return True
    elapsed = (now - rule.last_triggered_at).total_seconds()
    return elapsed >= rule.period_minutes * 60


def _analog_triggered(rule) -> bool:
    from api.models import SensorLatest

    if not rule.parameter_id:
        return False
    sl = (
        SensorLatest.objects
        .filter(
            sensor__connection__station_id=rule.station_id,
            sensor__parameter_id=rule.parameter_id,
            sensor__sensor_type__in=(0, 1),
        )
        .select_related("status")
        .order_by("-readtime")
        .first()
    )
    if sl is None or sl.value is None:
        return False
    if sl.status and sl.status.code in SKIP_STATUS_CODES:
        return False

    v = sl.value
    cond = rule.condition
    lo, hi = rule.min_value, rule.max_value
    if cond == rule.COND_MIN:
        return lo is not None and v < lo
    if cond == rule.COND_MAX:
        return hi is not None and v > hi
    if cond == rule.COND_MINMAX:
        return (lo is not None and v < lo) or (hi is not None and v > hi)
    return False


def _digital_triggered(rule) -> bool:
    from api.models import SensorLatest

    if not rule.sensor_id:
        return False
    sl = SensorLatest.objects.filter(sensor_id=rule.sensor_id).select_related("sensor").first()
    if sl is None or sl.value is None:
        return False
    raw = bool(sl.value)
    if sl.sensor and sl.sensor.digital_inverse:
        raw = not raw
    return raw


def _offline_triggered(rule, now) -> bool:
    from api.models import SensorLatest

    agg = SensorLatest.objects.filter(
        sensor__connection__station_id=rule.station_id
    ).aggregate(m=Max("readtime"))
    last = agg["m"]
    if last is None:
        return True
    return (now - last).total_seconds() > rule.offline_seconds


def _trigger_output(rule, now) -> None:
    """Ölçüm alarmında tanımlıysa dijital output'a 1 yazacak Command üretir."""
    from api.models import Command

    sensor = rule.trigger_output
    if sensor is None:
        return
    coil_value = 0 if sensor.digital_inverse else 1  # mantıksal "aktif"
    bucket = int(now.timestamp() // (rule.period_minutes * 60 or 60))
    idem = f"alarm_output:{rule.pk}:{bucket}"
    try:
        Command.objects.get_or_create(
            idempotency_key=idem,
            defaults=dict(
                sensor=sensor, value_type="bool", value=coil_value,
                status="pending", priority=10, source="rule",
                expires_at=now + timezone.timedelta(minutes=5), max_attempts=3,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Alarm output Command üretilemedi (rule=%s)", rule.pk)


def _fire(rule, now) -> int:
    """Alarmı tetikler: bildirim gönder + output + damga. Gönderilen sayısı döner."""
    from api.notifications import send_bulk
    from .models import AlarmRule

    recipients = _recipients(rule)
    channels = _channels(rule)
    station_name = rule.station.name if rule.station else ""
    full_msg = f"{station_name} SAİS Alarm: {rule.message}".strip()

    sent = 0
    if recipients and channels:
        results = send_bulk(recipients, channels, full_msg, kind="alarm")
        sent = sum(1 for r in results if r["ok"])

    if rule.rule_type == AlarmRule.RULE_ANALOG:
        _trigger_output(rule, now)

    AlarmRule.objects.filter(pk=rule.pk).update(last_triggered_at=now)
    logger.info("Alarm tetiklendi rule=%s sent=%s/%s", rule.pk, sent, len(recipients) * len(channels))
    return sent


def evaluate_rule(rule, now=None) -> bool:
    """Tek kuralı değerlendirir; tetiklendiyse (ve throttle uygunsa) gönderir.

    Döner: bildirim gönderildi mi (True/False).
    """
    from .models import AlarmRule

    now = now or timezone.now()
    if not rule.enabled:
        return False

    if rule.rule_type == AlarmRule.RULE_ANALOG:
        triggered = _analog_triggered(rule)
    elif rule.rule_type == AlarmRule.RULE_DIGITAL:
        triggered = _digital_triggered(rule)
    elif rule.rule_type == AlarmRule.RULE_OFFLINE:
        triggered = _offline_triggered(rule, now)
    else:
        triggered = False

    if not triggered or not _should_fire(rule, now):
        return False

    _fire(rule, now)
    return True


def run() -> dict:
    """Tüm aktif kuralları değerlendirir. Özet döner."""
    from .models import AlarmRule

    now = timezone.now()
    rules = list(
        AlarmRule.objects.filter(enabled=True, station__active=True)
        .select_related("station", "parameter", "sensor", "trigger_output")
    )
    fired = 0
    for rule in rules:
        try:
            if evaluate_rule(rule, now):
                fired += 1
        except Exception:  # noqa: BLE001 — bir kural diğerlerini bozmasın
            logger.exception("Alarm değerlendirme hatası (rule=%s)", rule.pk)
    return {"evaluated": len(rules), "fired": fired}
