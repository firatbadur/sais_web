"""
Sistem uyarı mekanizmaları — değerlendirme motoru.

Beş kategori (her biri Sistem Kontrol → Sistem Alarmları'ndan toggle'lı):

1. **Bakanlık veri hatası** — son 10 dk `GetDataByBetweenTwoDate`; seçili hata
   status'ları `persist` dakikadan uzun tekrarlıysa bildir (anlık tek-sefer
   hatalar boğmasın). ``run_realtime`` (10 dakikada bir).
2. **SSL** — sertifika bitişine `ssl_warn_days` kala (günlük).
3. **Kalibrasyon** — son kalibrasyondan `interval-warn` gün geçince (günlük).
4. **Lisans** — bitişe `license_warn_days` kala (günlük).
5. **PowerOff** — `poweroff_min_minutes`'tan uzun kesinti (her kayıt için bir kez).

Bildirim ``api.notifications.send_bulk(... kind="alarm")`` ile gider; tekrar
bildirim ``SystemAlarmState`` throttle'ı (cooldown) ile sınırlanır. Alıcılar
aktif kullanıcılar (Bakanlık rol=4 hariç), kanal tercihine göre.
"""
from __future__ import annotations

import logging
import socket
import ssl as ssllib
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone

from api.notifications import send_bulk

from .models import SystemAlarmSettings, SystemAlarmState
from .sim_report import STATUS_BY_CODE

logger = logging.getLogger("sais_domain.system_alarms")

ROLE_MINISTRY = 4  # Bakanlık (yalnız-API) kullanıcısı — bildirim almaz.


# --------------------------------------------------------------------- Alıcılar
def _recipients(settings: SystemAlarmSettings) -> list[dict]:
    """Aktif kullanıcılar; kanal tercihine + telefon/e-posta varlığına göre."""
    from users.models import CustomUser

    out: list[dict] = []
    for u in CustomUser.objects.filter(is_active=True).exclude(rol=ROLE_MINISTRY):
        phone = (u.phone_number or "").strip() if (settings.notify_sms and u.sms_enabled) else ""
        email = (u.email or "").strip() if (settings.notify_email and u.email_enabled) else ""
        if phone or email:
            out.append({
                "name": (u.get_full_name() or u.username).strip(),
                "phone": phone,
                "email": email,
            })
    return out


# ---------------------------------------------------------------- Throttle + gönder
def _notify(settings, alarm_type, ref_key, title, message, cooldown_minutes):
    """Throttle uygula + bildir + state damgala.

    ``cooldown_minutes=None`` → tek-sefer (state varsa bir daha bildirme).
    Alıcı/kanal yoksa state damgalanmaz (yapılandırılınca tekrar dener).
    """
    now = timezone.now()
    state = SystemAlarmState.objects.filter(alarm_type=alarm_type, ref_key=ref_key).first()
    if state is not None:
        if cooldown_minutes is None:
            return False  # tek-sefer, zaten bildirildi
        if (now - state.last_notified_at).total_seconds() < cooldown_minutes * 60:
            return False  # cooldown dolmadı

    recipients = _recipients(settings)
    channels = settings.channels()
    if not recipients or not channels:
        logger.info("system alarm: alıcı/kanal yok — '%s' bildirilemedi", title)
        return False

    try:
        send_bulk(recipients, channels, message, subject=f"[SAİS Alarm] {title}", kind="alarm")
    except Exception as exc:  # noqa: BLE001 — gönderim hatası alarmı düşürmesin
        logger.warning("system alarm send hata (%s): %s", alarm_type, exc)

    SystemAlarmState.objects.update_or_create(
        alarm_type=alarm_type, ref_key=ref_key,
        defaults={"last_notified_at": now, "detail": message[:300]},
    )
    return True


# --------------------------------------------------------- 1) Bakanlık veri hatası
def check_data_errors(settings: SystemAlarmSettings) -> int:
    """Son 10 dk Bakanlık verisini çekip seçili hata status'larını sayar."""
    if not settings.data_error_enabled:
        return 0
    from api.licensing import license_active
    if not license_active():
        return 0

    codes = {int(c) for c in (settings.data_error_codes or [])}
    if not codes:
        return 0

    from .clients import SaisSimClient
    from .models import SaisCabinet

    now = timezone.localtime().replace(tzinfo=None)
    start = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    persist = max(1, settings.data_error_persist_minutes)
    cooldown = settings.data_error_cooldown_minutes
    fired = 0

    for cabinet in SaisCabinet.objects.filter(station__active=True).select_related("station"):
        client = SaisSimClient(cabinet)
        try:
            objs = client.get_data_between(period=1, start_date=start, end_date=end)
        except Exception as exc:  # noqa: BLE001
            logger.warning("data_error: kabin %s sorgu hatası: %s", cabinet.id, exc)
            continue
        finally:
            client.close()

        rows = objs if isinstance(objs, list) else []
        # Hata kodu başına kaç dakika (kayıt) görüldü.
        per_code: dict[int, int] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            seen = set()
            for k, v in r.items():
                if k.endswith("_Status") and v in codes:
                    seen.add(v)
            for c in seen:
                per_code[c] = per_code.get(c, 0) + 1

        for code, cnt in per_code.items():
            if cnt < persist:
                continue  # anlık / tek-sefer — boğmamak için atla
            st = STATUS_BY_CODE.get(code, {})
            name = st.get("desc") or st.get("name") or f"kod {code}"
            title = f"{cabinet.station.name}: {name}"
            msg = (
                f"{cabinet.station.name} ({cabinet.name}) istasyonunda "
                f"\"{name}\" (kod {code}) hatası son 10 dakikada {cnt} kez tekrarlandı "
                f"(eşik {persist} dk). Lütfen kontrol edin."
            )
            if _notify(settings, "data_error", f"{cabinet.id}:{code}", title, msg, cooldown):
                fired += 1
    return fired


# --------------------------------------------------------------- 5) PowerOff
def check_poweroff(settings: SystemAlarmSettings) -> int:
    """Son kapanma/açılma kayıtlarından uzun kesintileri bildirir (tek-sefer)."""
    if not settings.poweroff_enabled:
        return 0
    from api.models import PowerOff

    min_min = max(1, settings.poweroff_min_minutes)
    cutoff = timezone.now() - timedelta(days=2)
    fired = 0
    qs = (
        PowerOff.objects
        .filter(start_date__isnull=False, end_date__isnull=False, end_date__gte=cutoff)
        .select_related("station")
    )
    for po in qs:
        dur_min = (po.end_date - po.start_date).total_seconds() / 60
        if dur_min < min_min:
            continue
        ref = f"poweroff:{po.id}"
        title = f"{po.station.name}: Kesinti"
        msg = (
            f"{po.station.name} istasyonu "
            f"{timezone.localtime(po.start_date):%d.%m.%Y %H:%M} – "
            f"{timezone.localtime(po.end_date):%d.%m.%Y %H:%M} arası ~{int(dur_min)} dk "
            f"kapalı kaldı (enerji/PC veya bağlantı kesintisi)."
        )
        if _notify(settings, "poweroff", ref, title, msg, cooldown_minutes=None):
            fired += 1
    return fired


# --------------------------------------------------------------- 2) SSL
def _live_tls_not_after(domain: str):
    """Domain:443'e bağlanıp sertifikanın bitiş tarihini okur (mod-bağımsız)."""
    try:
        ctx = ssllib.create_default_context()
        with socket.create_connection((domain, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ss:
                cert = ss.getpeercert()
        na = cert.get("notAfter") if cert else None
        if not na:
            return None
        return datetime.strptime(na, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt_timezone.utc)
    except Exception as exc:  # noqa: BLE001
        logger.info("SSL canlı kontrol başarısız (%s): %s", domain, exc)
        return None


def ssl_expiry(ws):
    """WebSettings'e göre sertifika bitiş tarihi (mod-duyarlı)."""
    if ws.tls_mode == ws.TLS_MANUAL and ws.manual_cert_not_after:
        return ws.manual_cert_not_after
    if ws.domain and ws.tls_mode == ws.TLS_LETSENCRYPT:
        return _live_tls_not_after(ws.domain)
    return None


def check_ssl(settings: SystemAlarmSettings) -> int:
    if not settings.ssl_enabled:
        return 0
    from api.models import WebSettings

    ws = WebSettings.load()
    if not ws.enabled or not ws.domain:
        return 0
    exp = ssl_expiry(ws)
    if not exp:
        return 0
    days = (exp - timezone.now()).days
    if days < 0:
        title = "SSL Süresi Doldu"
        msg = f"{ws.domain} SSL sertifikası {timezone.localtime(exp):%d.%m.%Y} tarihinde süresi DOLDU."
    elif days <= settings.ssl_warn_days:
        title = "SSL Sertifikası Bitiyor"
        msg = f"{ws.domain} SSL sertifikası {days} gün sonra ({timezone.localtime(exp):%d.%m.%Y}) sona eriyor."
    else:
        return 0
    return 1 if _notify(settings, "ssl", ws.domain, title, msg, cooldown_minutes=1440) else 0


# --------------------------------------------------------------- 4) Lisans
def check_license(settings: SystemAlarmSettings) -> int:
    if not settings.license_enabled:
        return 0
    try:
        from api.licensing import license_status_dict
        st = license_status_dict()
    except Exception as exc:  # noqa: BLE001
        logger.info("lisans durumu okunamadı: %s", exc)
        return 0
    days = st.get("days_remaining")
    if days is None:
        return 0
    if days < 0 or days > settings.license_warn_days:
        return 0
    valid_until = st.get("valid_until")
    vu = ""
    if valid_until is not None:
        try:
            vu = f" ({timezone.localtime(valid_until):%d.%m.%Y})"
        except Exception:  # noqa: BLE001
            vu = f" ({valid_until})"
    msg = f"Yazılım lisansı {days} gün sonra{vu} sona eriyor. Lütfen yenileyin."
    return 1 if _notify(settings, "license", "license", "Lisans Bitiyor", msg, cooldown_minutes=1440) else 0


# --------------------------------------------------------------- 3) Kalibrasyon
def check_calibration(settings: SystemAlarmSettings) -> int:
    if not settings.calibration_enabled:
        return 0
    from django.db.models import Max

    from api.models import Calibration
    from .models import SaisCabinet

    threshold = max(1, settings.calibration_interval_days - settings.calibration_warn_days)
    now = timezone.now()
    fired = 0
    # Yalnız Bakanlık'a raporlayan (kabinli) aktif istasyonlar kalibrasyon gerektirir.
    seen_stations = set()
    for cabinet in SaisCabinet.objects.filter(station__active=True).select_related("station"):
        station = cabinet.station
        if station.id in seen_stations:
            continue
        seen_stations.add(station.id)
        last = (
            Calibration.objects
            .filter(sensor__connection__station=station)
            .aggregate(m=Max("time_iso"))["m"]
        )
        if last is None:
            title = "Kalibrasyon Gerekli"
            msg = (f"{station.name}: kayıtlı kalibrasyon yok. Aylık kalibrasyon "
                   f"({settings.calibration_interval_days} gün) zorunludur.")
        else:
            days = (now - last).days
            if days < threshold:
                continue
            title = "Kalibrasyon Hatırlatma"
            msg = (f"{station.name}: son kalibrasyon {timezone.localtime(last):%d.%m.%Y} "
                   f"({days} gün önce). Aylık kalibrasyon süresi "
                   f"({settings.calibration_interval_days} gün) yaklaşıyor.")
        if _notify(settings, "calibration", f"station:{station.id}", title, msg, cooldown_minutes=1440):
            fired += 1
    return fired


# --------------------------------------------------------------- Koşucular
def run_realtime() -> dict:
    """10 dakikada bir: veri hatası + kesinti."""
    settings = SystemAlarmSettings.load()
    out = {}
    for name, fn in (("data_error", check_data_errors), ("poweroff", check_poweroff)):
        try:
            out[name] = fn(settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("system alarm %s hata", name)
            out[name] = f"error: {exc}"
    return out


def run_daily() -> dict:
    """Günde bir: SSL + lisans + kalibrasyon."""
    settings = SystemAlarmSettings.load()
    out = {}
    for name, fn in (("ssl", check_ssl), ("license", check_license), ("calibration", check_calibration)):
        try:
            out[name] = fn(settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("system alarm %s hata", name)
            out[name] = f"error: {exc}"
    return out


# --------------------------------------------------------------- Sayfa durumu
def current_status(settings: SystemAlarmSettings | None = None) -> dict:
    """Sistem Alarmları sekmesi için canlı durum özeti (AJAX)."""
    from api.models import WebSettings

    if settings is None:
        settings = SystemAlarmSettings.load()

    # SSL
    ws = WebSettings.load()
    ssl_info = {"domain": ws.domain or "", "mode": ws.tls_mode, "days": None, "expiry": None}
    if ws.enabled and ws.domain:
        exp = ssl_expiry(ws)
        if exp:
            ssl_info["days"] = (exp - timezone.now()).days
            ssl_info["expiry"] = timezone.localtime(exp).strftime("%d.%m.%Y")

    # Lisans
    lic_info = {"days": None, "valid_until": None}
    try:
        from api.licensing import license_status_dict
        st = license_status_dict()
        lic_info["days"] = st.get("days_remaining")
        vu = st.get("valid_until")
        if vu is not None:
            try:
                lic_info["valid_until"] = timezone.localtime(vu).strftime("%d.%m.%Y")
            except Exception:  # noqa: BLE001
                lic_info["valid_until"] = str(vu)
    except Exception:  # noqa: BLE001
        pass

    # Kalibrasyon (kabinli istasyonlar)
    from django.db.models import Max

    from api.models import Calibration
    from .models import SaisCabinet

    cal_rows = []
    seen = set()
    for cabinet in SaisCabinet.objects.filter(station__active=True).select_related("station"):
        s = cabinet.station
        if s.id in seen:
            continue
        seen.add(s.id)
        last = (
            Calibration.objects.filter(sensor__connection__station=s)
            .aggregate(m=Max("time_iso"))["m"]
        )
        cal_rows.append({
            "station": s.name,
            "last": timezone.localtime(last).strftime("%d.%m.%Y") if last else None,
            "days": (timezone.now() - last).days if last else None,
        })

    # Son bildirimler
    events = [
        {
            "type": e.alarm_type,
            "ref": e.ref_key,
            "at": timezone.localtime(e.last_notified_at).strftime("%d.%m.%Y %H:%M"),
            "detail": e.detail,
        }
        for e in SystemAlarmState.objects.all()[:20]
    ]

    return {"ssl": ssl_info, "license": lic_info, "calibration": cal_rows, "events": events}
