"""
api app'i için periyodik Celery wrap task'lar.

Bu task'lar mevcut management komutlarını çağırır — komutlar manuel
çalıştırma için de korunur (tek yerde mantık, iki tetikleyici).
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command


logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.run_alarms")
def run_alarms():
    """Aktif alarm kurallarını değerlendirir ve gereken SMS/e-posta'ları gönderir.

    Lisans bitmişse alarm değerlendirmesi de durur (polling/yayın gibi).
    """
    from api.licensing import license_active
    if not license_active():
        return {"skipped": "license_inactive"}
    from api import alarms
    return alarms.run()


@shared_task(name="api.tasks.heartbeat_task")
def heartbeat_task():
    """Sistem canlılık damgasını tazeler (her dakika).

    PC kapanma tespiti buna dayanır: açılışta `detect_power_off` son damga ile
    şimdiki zaman arasındaki boşluğu ölçer; eşiği aşarsa o aralık PC kapalı kalma
    süresi olarak `PowerOff` kayıtlarına yazılır.

    Lisans bitse de çalışır (gate'lenmez) — PC ayakta olduğu sürece damga
    tazelenmeli, aksi halde lisans/polling kesintisi yanlışlıkla 'kapanma' gibi
    görünür.
    """
    from django.utils import timezone
    from api.models import SystemHeartbeat
    hb = SystemHeartbeat.load()
    hb.last_seen = timezone.now()
    hb.save(update_fields=["last_seen", "updated_at"])
    return {"last_seen": hb.last_seen.isoformat()}


@shared_task(name="api.tasks.aggregate_readings_5m")
def aggregate_readings_5m():
    """5 dakikalık aggregate — son 2 saatlik aralığı yeniden hesaplar.

    Numune senaryosu motorunun poll-sıklığından bağımsız kısa-pencere
    ortalamasını beslemek için her dakika çalışır.
    """
    call_command("aggregate_readings", bucket="5m", hours=2)


@shared_task(name="api.tasks.aggregate_readings_15m")
def aggregate_readings_15m():
    """15 dakikalık aggregate — son 2 saatlik aralığı yeniden hesaplar."""
    call_command("aggregate_readings", bucket="15m", hours=2)


@shared_task(name="api.tasks.aggregate_readings_hourly")
def aggregate_readings_hourly():
    """Saatlik aggregate — son 6 saatlik aralığı yeniden hesaplar."""
    call_command("aggregate_readings", bucket="hour", hours=6)


@shared_task(name="api.tasks.aggregate_readings_daily")
def aggregate_readings_daily():
    """Günlük aggregate — son 48 saatlik aralığı kapsar (gün başı kayması için marj)."""
    call_command("aggregate_readings", bucket="day", hours=48)


@shared_task(name="api.tasks.prune_api_logs_task")
def prune_api_logs_task():
    """settings.API_LOG_RETENTION_DAYS'den eski ApiLog satırlarını siler."""
    call_command("prune_api_logs")


@shared_task(name="api.tasks.prune_readings_task")
def prune_readings_task():
    """Reading + aggregate tablolarından retention'ı geçenleri siler.

    Her level (raw/15m/hour/day) kendi settings değişkenini kullanır:
    READING_RETENTION_{RAW,15M,HOURLY,DAILY}_DAYS.
    """
    call_command("prune_readings")


@shared_task(name="api.tasks.backup_database_run")
def backup_database_run(tier="manual", force=False, user_id=None):
    """Bir tier için DB yedeği alır (beat + manuel tetik ortak).

    Beat çağrıları `force=False` gelir → `BackupPolicy.enabled` kapalıysa atlanır.
    Manuel (dashboard) tetik `force=True` ile gelir.
    """
    call_command("backup_database", tier=tier, force=force, user_id=user_id)


@shared_task(name="api.tasks.restore_database_run")
def restore_database_run(backup_id, run_migrate=True, user_id=None):
    """Bir yedekten DB'yi geri yükler (ağır iş — worker'da çalışır)."""
    call_command(
        "restore_database",
        backup_id=backup_id,
        no_migrate=not run_migrate,
        yes=True,
        user_id=user_id,
    )


@shared_task(name="api.tasks.license_refresh_task")
def license_refresh_task():
    """Lisans manifest'ini uzaktan çekip doğrular ve uygular (beat: her 6 saat).

    Lisans bitse bile bu task çalışmaya devam eder — yenilenince sistem kendini
    toparlasın diye.
    """
    call_command("refresh_license")


def fetch_public_ip():
    """Sunucunun dış (public) IP adresini bir public servisten döndürür; hata/
    erişimsizlikte None. ApiLog'u şişirmemek için bilinçli olarak
    `log_outbound_call` ile sarılmadı (port_check ile tutarlı).

    Birden çok servis denenir (biri saha firewall'ında bloklanmış/yavaş
    olabilir); ilk geçerli IPv4/IPv6 yanıtı döner. Bazı servisler default
    `Python-urllib` UA'sını reddettiği için tarayıcı benzeri UA gönderilir.
    """
    import ipaddress
    import urllib.request

    services = (
        "https://api.ipify.org",
        "https://checkip.amazonaws.com",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
        "https://ipinfo.io/ip",
        "https://api64.ipify.org",
    )
    headers = {"User-Agent": "Mozilla/5.0 (EnvisoftWebX)"}
    for svc in services:
        try:
            req = urllib.request.Request(svc, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                ip = r.read().decode("utf-8", "replace").strip()
            if not ip:
                continue
            # Yanıt gerçekten bir IP adresi mi? (HTML hata sayfası vb. ele)
            ipaddress.ip_address(ip)
            return ip
        except Exception:  # noqa: BLE001 — ağ/parse hatası -> diğer servisi dene
            continue
    return None


@shared_task(name="api.tasks.record_public_ip_task")
def record_public_ip_task():
    """Public IP'yi periyodik kontrol eder; değişmişse `PublicIpRecord`'a yeni
    satır yazar (saha dinamik IP'li olabilir — değişimi izleyebilmek için)."""
    from api.models import PublicIpRecord

    ip = fetch_public_ip()
    if ip:
        PublicIpRecord.record(ip)
    return ip
