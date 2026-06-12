"""Numune alma senaryosu yürütme motoru.

`sais_domain.tasks.run_scenarios` periyodik task'ı her dakika `run()`'ı çağırır.
Her istasyonun aktif `auto` senaryosu izlenen parametre ortalamalarına (5dk/15dk
aggregate) karşı değerlendirilir; tetik kombinasyonu (any/all/n_of_m) sağlanınca
bir `ScenarioRun` açılır. Açık run'lar adım adım ilerletilir: her `ScenarioStep`
tetikten `after_seconds` sonra vade bulur ve `actions` listesindeki aksiyonlar
(bildirim / numune alıcı aç-kapa / Bakanlık kod al / SIM Start/Complete/Error /
diagnostik) yürütülür.

Eski sabit-kodlu `controlSampleValues` akışının jenerik, kullanıcı-tanımlı
karşılığıdır. Tüm ilerleme `ScenarioRun`'da kalıcıdır → motor tick'ler arası
stateless, restart güvenli; zamanlama monoton (`after_seconds <= elapsed`) olduğu
için kaçan bir tick adım atlatmaz. SAIS'e özel (Bakanlık + Envisoft entegrasyonu).
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger("sais_domain.scenario_engine")

# Analog değer bu statuslarda değerlendirilmez (iletişim hatası / yıkama / bakım).
SKIP_STATUS_CODES = {8, 23, 24, 25, 26}

# SIM client gerektiren aksiyon tipleri.
SIM_ACTION_TYPES = {
    "ministry_get_code",
    "sim_sample_start",
    "sim_sample_complete",
    "sim_sample_error",
    "send_diagnostic",
}


# --------------------------------------------------------------------------- #
# Ortalama + tetik değerlendirme
# --------------------------------------------------------------------------- #

def _param_sensor(scenario, scp):
    """Bir senaryo parametresinin analog sensörünü çözer (yoksa None)."""
    from api.models import Sensor

    return (
        Sensor.objects
        .filter(
            parameter_id=scp.parameter_id,
            connection__station_id=scenario.station_id,
            sensor_type__in=(0, 1),
        )
        .order_by("id")
        .first()
    )


def _param_average(scenario, scp):
    """Parametrenin en güncel aggregate bucket ortalaması (yoksa None)."""
    sensor = _param_sensor(scenario, scp)
    if sensor is None:
        return None
    Model = scenario.aggregate_model()
    latest = (
        Model.objects
        .filter(sensor_id=sensor.id)
        .order_by("-bucket_start")
        .first()
    )
    return latest.avg_value if latest else None


def _skip_reason(scenario, params):
    """Cycle atlanmalı mı? Yıkama aktifse veya izlenen bir parametrenin anlık
    status'u SKIP_STATUS_CODES içindeyse atlanır (neden string'i döner)."""
    from api.models import SensorLatest

    from .models import SystemSwitch

    if SystemSwitch.load().active_wash_status_code() is not None:
        return "wash_active"

    for scp in params:
        sensor = _param_sensor(scenario, scp)
        if sensor is None:
            continue
        sl = (
            SensorLatest.objects
            .filter(sensor_id=sensor.id)
            .select_related("status")
            .first()
        )
        if sl and sl.status and sl.status.code in SKIP_STATUS_CODES:
            return f"status_{sl.status.code}"
    return ""


def _evaluate(scenario, params):
    """İzlenen parametreleri değerlendirir.

    Döner: (triggered: bool, exceeded_codes: list[str], averages: dict,
            exceeded_scps: list[ScenarioParameter]).
    """
    averages = {}
    exceeded_scps = []
    with_data = 0

    for scp in params:
        avg = _param_average(scenario, scp)
        name = scp.parameter.parameter_name or f"param{scp.parameter_id}"
        averages[name] = round(avg, 2) if avg is not None else None
        if avg is None:
            continue
        with_data += 1
        over = (
            (scp.min_value is not None and avg < scp.min_value)
            or (scp.max_value is not None and avg > scp.max_value)
        )
        if over:
            exceeded_scps.append(scp)

    exceeded_codes = [
        (s.parameter.parameter_name or f"param{s.parameter_id}") for s in exceeded_scps
    ]
    n = len(exceeded_scps)

    if scenario.trigger_mode == scenario.TRIGGER_ALL:
        triggered = with_data > 0 and n == with_data
    elif scenario.trigger_mode == scenario.TRIGGER_N:
        triggered = n >= max(1, scenario.trigger_n)
    else:  # TRIGGER_ANY
        triggered = n >= 1

    return triggered, exceeded_codes, averages, exceeded_scps


# --------------------------------------------------------------------------- #
# Aksiyon yürütme
# --------------------------------------------------------------------------- #

def _recipients(want_sms, want_email):
    """Tüm aktif kullanıcılar; kanal tercihine + telefon/e-posta varlığına göre."""
    from users.models import CustomUser

    out = []
    for u in CustomUser.objects.filter(is_active=True):
        phone = (u.phone_number or "").strip() if (want_sms and u.sms_enabled) else ""
        email = (u.email or "").strip() if (want_email and u.email_enabled) else ""
        if phone or email:
            out.append({
                "name": (u.get_full_name() or u.username).strip(),
                "phone": phone,
                "email": email,
            })
    return out


def _notify(scenario, message, want_sms=True, want_email=True):
    from api.notifications import send_bulk

    channels = []
    if want_sms:
        channels.append("sms")
    if want_email:
        channels.append("email")
    recipients = _recipients(want_sms, want_email)
    station_name = scenario.station.name if scenario.station else ""
    full_msg = f"{station_name} {message}".strip()
    if recipients and channels:
        results = send_bulk(recipients, channels, full_msg, kind="alarm")
        return sum(1 for r in results if r.get("ok"))
    return 0


def _create_command(scenario, run, step, *, value):
    """Numune alıcı çıkışına yazma komutu kuyruğa al (idempotent)."""
    from datetime import timedelta

    from api.models import Command, RequestType

    sensor = scenario.effective_sampler_sensor()
    if sensor is None:
        logger.warning(
            "Senaryo %s: numune alıcı sensörü tanımlı değil — komut atlandı.",
            scenario.pk,
        )
        return None

    code = "ministry_sample" if run.is_ministry else "auto_scenario"
    request_type = RequestType.objects.filter(code=code).first()
    on_off = "on" if value else "off"
    idempotency_key = f"scenario:{run.id}:{step.order}:{on_off}"

    cmd, _created = Command.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults=dict(
            sensor=sensor,
            value_type="bool",
            value=1 if value else 0,
            status="pending",
            priority=10,
            source="rule",
            request_type=request_type,
            correlation_id=run.sample_code or None,
            expires_at=timezone.now() + timedelta(minutes=5),
            max_attempts=3,
        ),
    )
    return cmd


def _extract_sample_code(result):
    """get_sample_code dönüşünden kodu çıkar (string ya da dict olabilir)."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for key in ("SampleCode", "sampleCode", "code", "Code"):
            if result.get(key):
                return str(result[key]).strip()
    return str(result).strip()


def _run_action(scenario, run, step, action, client):
    """Tek bir aksiyonu yürütür. İstisnalar yukarı taşınmaz — log'lanır."""
    from .clients import SaisClientError

    atype = action.get("type")
    try:
        if atype == "notify":
            msg = action.get("message") or step.label or "Numune senaryosu bildirimi"
            _notify(
                scenario, msg,
                want_sms=action.get("sms", True),
                want_email=action.get("email", True),
            )

        elif atype == "sampler_on":
            _create_command(scenario, run, step, value=1)

        elif atype == "sampler_off":
            _create_command(scenario, run, step, value=0)

        elif atype == "ministry_get_code":
            if client is None:
                logger.warning("Senaryo %s: kabin yok — ministry_get_code atlandı.", scenario.pk)
                return
            param = action.get("parameter")
            if not param:
                # İlk tetikleyen parametrenin Bakanlık kodu.
                scp = scenario.parameters.filter(enabled=True).first()
                param = scp.ministry_code() if scp else ""
            result = client.get_sample_code(param)
            code = _extract_sample_code(result)
            if code:
                run.sample_code = code
                run.save(update_fields=["sample_code", "updated_at"])

        elif atype == "sim_sample_start":
            if client and run.sample_code:
                client.sample_request_start(run.sample_code)

        elif atype == "sim_sample_complete":
            if client and run.sample_code:
                client.sample_request_complete(run.sample_code)

        elif atype == "sim_sample_error":
            if client and run.sample_code:
                client.sample_request_error(run.sample_code)

        elif atype == "send_diagnostic":
            if client:
                client.send_diagnostic_with_type(
                    type_no=int(action.get("type_no", 0)),
                    details=action.get("details") or "",
                )
        else:
            logger.warning("Senaryo %s: bilinmeyen aksiyon tipi %r", scenario.pk, atype)

    except SaisClientError as exc:
        logger.warning("Senaryo %s aksiyon %s SIM hatası: %s", scenario.pk, atype, exc)
    except Exception:  # noqa: BLE001 — bir aksiyon cycle'ı bozmasın
        logger.exception("Senaryo %s aksiyon %s beklenmedik hata", scenario.pk, atype)


def _execute_step(scenario, run, step):
    """Adımın tüm aksiyonlarını yürütür; gerekiyorsa tek SIM client açar."""
    from .clients import SaisSimClient

    actions = step.actions or []
    needs_sim = any(a.get("type") in SIM_ACTION_TYPES for a in actions)
    cabinet = scenario.cabinet() if needs_sim else None

    if needs_sim and cabinet is not None:
        with SaisSimClient(cabinet) as client:
            for action in actions:
                _run_action(scenario, run, step, action, client)
    else:
        if needs_sim:
            logger.warning("Senaryo %s: SIM gerekli ama kabin yok.", scenario.pk)
        for action in actions:
            _run_action(scenario, run, step, action, client=None)


# --------------------------------------------------------------------------- #
# Run yaşam döngüsü
# --------------------------------------------------------------------------- #

def _log(scenario, run, *, kind, averages=None, message="", skipped_reason="", step_order=None):
    from .models import ScenarioRunLog

    ScenarioRunLog.objects.create(
        run=run,
        station_id=scenario.station_id,
        kind=kind,
        step_order=step_order,
        averages=averages or {},
        message=message[:500],
        skipped_reason=skipped_reason[:200],
    )


def _advance(scenario, run, triggered, now):
    """Vadesi gelen adımları sırayla işler; son adımda run'ı tamamlar."""
    from .models import ScenarioRun

    steps = list(scenario.steps.order_by("order"))
    if not steps:
        return

    elapsed = (now - run.trigger_at).total_seconds()
    max_order = steps[-1].order

    for step in steps:
        if step.order <= run.last_step_order:
            continue
        if step.after_seconds > elapsed:
            break  # sonraki adımların vadesi henüz gelmedi (sıralı)

        if step.require_still_exceeded and not triggered:
            # Koşul temizlendi — adımı atla ama cursor'ı ilerlet (stall yok).
            run.last_step_order = step.order
            run.save(update_fields=["last_step_order", "updated_at"])
            _log(scenario, run, kind="skip", message=f"Adım atlandı (koşul temizlendi): {step.label}",
                 step_order=step.order)
            continue

        _execute_step(scenario, run, step)
        run.last_step_order = step.order
        run.save(update_fields=["last_step_order", "updated_at"])
        _log(scenario, run, kind="step", message=f"Adım işlendi: {step.label}", step_order=step.order)

    if run.last_step_order >= max_order and run.status == ScenarioRun.STATUS_IN_PROGRESS:
        run.status = ScenarioRun.STATUS_COMPLETED
        run.completed_at = now
        run.save(update_fields=["status", "completed_at", "updated_at"])


def _open_run(scenario, station_id):
    """İstasyon için açık (in_progress) run (yoksa None)."""
    from .models import ScenarioRun

    return (
        ScenarioRun.objects
        .filter(scenario=scenario, status=ScenarioRun.STATUS_IN_PROGRESS)
        .order_by("-trigger_at")
        .first()
    )


def evaluate_auto(scenario, now):
    """Eşik-tetikli (auto) senaryoyu değerlendirir."""
    from .models import ScenarioRun

    params = list(scenario.parameters.filter(enabled=True).select_related("parameter"))

    skip = _skip_reason(scenario, params)
    if skip:
        _, _, averages, _ = _evaluate(scenario, params)
        _log(scenario, None, kind="skip", averages=averages, skipped_reason=skip,
             message="Değerlendirme atlandı")
        return

    triggered, codes, averages, _ = _evaluate(scenario, params)
    run = _open_run(scenario, scenario.station_id)

    if run is None:
        if triggered:
            run = ScenarioRun.objects.create(
                scenario=scenario,
                station_id=scenario.station_id,
                run_date=timezone.localdate(now),
                status=ScenarioRun.STATUS_IN_PROGRESS,
                is_ministry=False,
                trigger_at=now,
                last_step_order=-1,
                triggered_parameters=codes,
            )
            _log(scenario, run, kind="eval", averages=averages,
                 message=f"Senaryo tetiklendi: {', '.join(codes)}")
            _advance(scenario, run, triggered, now)
        else:
            _log(scenario, None, kind="eval", averages=averages, message="Tetik yok")
        return

    _log(scenario, run, kind="eval", averages=averages,
         message=f"Devam ediyor (adım {run.last_step_order})")
    _advance(scenario, run, triggered, now)


def request_ministry(station, code, now=None):
    """Bakanlık numune talebini başlatır — aktif ministry senaryosu için run açar.

    Döner: oluşturulan/var olan ScenarioRun, ya da uygun senaryo yoksa None.
    """
    from .models import Scenario, ScenarioRun

    now = now or timezone.now()
    scenario = (
        Scenario.objects
        .filter(station=station, kind=Scenario.KIND_MINISTRY, is_active=True, enabled=True)
        .first()
    )
    if scenario is None:
        return None

    run = _open_run(scenario, station.id)
    if run is not None:
        return run  # zaten devam eden bir talep var

    run = ScenarioRun.objects.create(
        scenario=scenario,
        station_id=station.id,
        run_date=timezone.localdate(now),
        status=ScenarioRun.STATUS_IN_PROGRESS,
        is_ministry=True,
        trigger_at=now,
        last_step_order=-1,
        sample_code=str(code or ""),
    )
    _log(scenario, run, kind="eval", message=f"Bakanlık numune talebi: {code}")
    _advance(scenario, run, triggered=True, now=now)
    return run


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #

def run(now=None):
    """Tüm aktif auto senaryoları değerlendirir + açık tüm run'ları ilerletir.

    Açık ministry/auto run'lar `run_date`'e bakılmaksızın taranır → gün devrine
    sarkan (örn. 24s) adımlar ertesi gün tamamlanır.
    """
    from .models import Scenario, ScenarioRun

    now = now or timezone.now()
    summary = {"evaluated": 0, "advanced": 0}

    # 1) Aktif auto senaryolar değerlendirilir (yeni tetik + ilerletme).
    auto = (
        Scenario.objects
        .filter(kind=Scenario.KIND_AUTO, is_active=True, enabled=True, station__active=True)
        .select_related("station")
    )
    handled_run_ids = set()
    for scenario in auto:
        try:
            evaluate_auto(scenario, now)
            summary["evaluated"] += 1
            r = _open_run(scenario, scenario.station_id)
            if r:
                handled_run_ids.add(r.id)
        except Exception:  # noqa: BLE001 — bir senaryo diğerlerini bozmasın
            logger.exception("Senaryo değerlendirme hatası (scenario=%s)", scenario.pk)

    # 2) Geri kalan açık run'lar (ministry + gün devrine sarkan auto) ilerletilir.
    open_runs = (
        ScenarioRun.objects
        .filter(status=ScenarioRun.STATUS_IN_PROGRESS)
        .exclude(id__in=handled_run_ids)
        .select_related("scenario", "scenario__station")
    )
    for run_obj in open_runs:
        scenario = run_obj.scenario
        if not scenario.enabled:
            continue
        try:
            # Ministry run'ları koşulsuz ilerler; auto'lar için güncel tetik durumu.
            if run_obj.is_ministry:
                triggered = True
            else:
                params = list(scenario.parameters.filter(enabled=True).select_related("parameter"))
                triggered, _, _, _ = _evaluate(scenario, params)
            _advance(scenario, run_obj, triggered, now)
            summary["advanced"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("Açık run ilerletme hatası (run=%s)", run_obj.pk)

    return summary
