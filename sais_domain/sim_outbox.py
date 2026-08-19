"""Bakanlık SİM gönderim kuyruğunun (store-and-forward outbox) motoru.

Celery sarmalayıcıları ``sais_domain.tasks`` içindedir; asıl mantık burada
toplanır (``scenario_engine`` ↔ ``tasks.run_scenarios`` ayrımıyla aynı desen).

Akış::

    publish_cabinet_data  ──enqueue──►  SimOutboxEntry(pending)
                                              │
    dispatch_sim_outbox (beat 30sn) ──────────┤
    publish_cabinet_data (zincir)  ───────────┤
                                              ▼
                                      drain_cabinet(cabinet)
                                       (kabin başına Redis kilidi)
                                              │
                                              ▼
                                  SaisSimClient.send_data  ──►  Bakanlık

**Sıkı FIFO:** ``drain_cabinet`` bir şeritte en eski bekleyen kayıttan başlar.
Baştaki kayıt geçici bir hata alırsa **döngü kırılır** — sonraki dakikalar
Bakanlık o veriyi kabul edene kadar gönderilmez (asıl gereksinim). Kalıcı ret
sınırlı sayıda denenir, sonra ``failed`` damgalanıp ilerlenir; sistematik ret
"zehirli kuyruk" modunu tetikleyip ilk denemede ilerlemeyi sağlar.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from . import sim_errors
from .clients import SaisSimClient
from .models import SimOutboxEntry, SystemSwitch


logger = logging.getLogger("sais_domain.sim_outbox")


# --------------------------------------------------------------- Ayar okuma


def _conf(name: str, default):
    return getattr(settings, name, default)


def accept_window_hours() -> int:
    """Bakanlık veri kabul penceresi (saat) — tek doğruluk kaynağı.

    Değer ``SystemSwitch`` üzerinden **operatör tarafından** değiştirilebilir
    (Sistem Kontrol sayfası): Bakanlık normalde son 48 saati kabul eder ama
    bakım vb. durumlarda pencereyi geçici olarak genişletebiliyor.
    """
    try:
        return SystemSwitch.load().accept_window_hours()
    except Exception:  # noqa: BLE001 — DB yoksa/erken çağrıysa güvenli varsayılan
        return int(_conf("SAIS_SIM_OUTBOX_MAX_AGE_HOURS", 48))


def backoff_seconds(attempts: int, *, rejected: bool = False) -> int:
    """Üstel backoff: ``min(BASE * 2**(deneme-1), TAVAN)`` + ±%10 jitter.

    Jitter, çok kabinli sahalarda tüm kuyrukların aynı saniyede Bakanlık'a
    yüklenmesini (thundering herd) engeller. Kalıcı ret için tavan daha
    kısadır: ret zaten sınırlı sayıda denenecek, arada 10 dk beklemenin
    anlamı yok.
    """
    import random

    base = int(_conf("SAIS_SIM_OUTBOX_BACKOFF_BASE_SEC", 30))
    cap = int(
        _conf("SAIS_SIM_OUTBOX_REJECT_BACKOFF_MAX_SEC", 60) if rejected
        else _conf("SAIS_SIM_OUTBOX_BACKOFF_MAX_SEC", 600)
    )
    raw = min(base * (2 ** max(0, int(attempts) - 1)), cap)
    return max(1, int(raw * random.uniform(0.9, 1.1)))


# ------------------------------------------------------------ Bakım işleri


def reap_stuck_entries(now=None) -> int:
    """``sending`` durumunda takılı kalmış kayıtları kuyruğa geri alır.

    Worker SIGKILL yerse (ör. Celery hard time limit) kayıt ``sending``
    durumunda kalır ve kısmi unique kısıt yüzünden o dakika bir daha
    kuyruğa alınamaz — kuyruk sonsuza dek tıkanırdı. Kiralama (lease)
    süresi dolan kayıtlar ``pending``'e döner.
    """
    now = now or timezone.now()
    lease = int(_conf("SAIS_SIM_OUTBOX_CLAIM_LEASE_SEC", 300))
    cutoff = now - timedelta(seconds=lease)
    return (
        SimOutboxEntry.objects
        .filter(status=SimOutboxEntry.STATUS_SENDING, claimed_at__lt=cutoff)
        .update(
            status=SimOutboxEntry.STATUS_PENDING,
            next_attempt_at=now,
            last_error_kind="lease_expired",
            last_error="Gönderim yarıda kaldı (worker yeniden başladı) — kuyruğa alındı.",
            updated_at=now,
        )
    )


def expire_old_entries(now=None) -> int:
    """Kabul penceresini aşan bekleyen kayıtları toplu olarak ``expired`` yapar.

    Tek tek drenaj döngüsünde de yapılabilirdi ama günlerce süren bir
    kesintiden sonra bu O(n) tur demek olurdu; toplu ``UPDATE`` ile kuyruk
    anında gerçekçi boyuta iner.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(hours=accept_window_hours())
    return (
        SimOutboxEntry.objects
        .filter(status=SimOutboxEntry.STATUS_PENDING, readtime__lt=cutoff)
        .update(
            status=SimOutboxEntry.STATUS_EXPIRED,
            payload={},
            last_error_kind="expired",
            last_error="Bakanlık kabul penceresi aşıldı — veri iletilemedi.",
            updated_at=now,
        )
    )


def due_cabinet_ids(now=None) -> list[int]:
    """Gönderilmeyi bekleyen (vadesi gelmiş) kayıtları olan kabin id'leri."""
    now = now or timezone.now()
    return list(
        SimOutboxEntry.objects
        .filter(status=SimOutboxEntry.STATUS_PENDING, next_attempt_at__lte=now)
        .values_list("cabinet_id", flat=True)
        .distinct()
    )


# --------------------------------------------------------------- Zehirlilik


def _poison_mode(cabinet_id: int, priority: int) -> bool:
    """Sistematik ret var mı? (peş peşe N kayıt aynı nedenle reddedildi)

    Kullanıcı kuralı "kalıcı ret N denemeden sonra atlanır" tek başına
    yetmez: sistematik bir ret (ör. Bakanlık'ın tanımadığı bir parametre adı)
    hâlinde HER kayıt N deneme × backoff kadar kuyruğu tıkar → dakikada 1
    kayıt gelirken kuyruk asla yetişemez ve her şey 48 saatte çöpe gider.
    Peş peşe aynı nedenle reddedilen kayıt görülürse ilk denemede ilerlenir.

    Durum DB'den türetilir (Redis değil) — worker yeniden başlasa da doğru
    kalır; ilk başarılı gönderim (``sent``) diziyi kendiliğinden kırar.
    """
    streak = int(_conf("SAIS_SIM_OUTBOX_POISON_STREAK", 3))
    if streak <= 0:
        return False
    recent = list(
        SimOutboxEntry.objects
        .filter(
            cabinet_id=cabinet_id, priority=priority,
            status__in=(SimOutboxEntry.STATUS_SENT, SimOutboxEntry.STATUS_FAILED),
        )
        .order_by("-readtime", "-id")
        .values_list("status", "last_error_kind")[:streak]
    )
    if len(recent) < streak:
        return False
    kinds = {k for _, k in recent}
    return (
        all(st == SimOutboxEntry.STATUS_FAILED for st, _ in recent)
        and len(kinds) == 1
    )


# ------------------------------------------------------------ Tek gönderim


def send_entry(client, entry) -> tuple[str, str, str, int | None]:
    """Tek kaydı Bakanlık'a gönderip sonucu sınıflandırır.

    Dönüş: ``(outcome, category, message, http_status)``.
    """
    try:
        envelope = client.send_data(
            readtime=entry.readtime_str,
            values=entry.payload or {},
            period=entry.period or 1,
        )
    except Exception as exc:  # noqa: BLE001 — sınıflandırıcı hepsini yorumlar
        http_status = getattr(exc, "status_code", None)
        outcome, category, message = sim_errors.classify_send_result(
            exception=exc, http_status=http_status,
        )
        return outcome, category, message, http_status

    outcome, category, message = sim_errors.classify_send_result(envelope=envelope)
    # Bakanlık "bu dakika zaten var" diyorsa bu KABUL'dür: mükerrer gönderim
    # (ACKS_LATE yeniden teslimi / kiralama süresi dolması) kuyruğu zehirlemesin.
    if outcome == sim_errors.PERMANENT and _is_duplicate_message(message):
        logger.info(
            "SİM kuyruk: mükerrer veri yanıtı kabul sayıldı (kayıt=%s): %s",
            entry.pk, message,
        )
        return sim_errors.ACCEPTED, sim_errors.OK, message, 200
    return outcome, category, message, 200


def _is_duplicate_message(message: str) -> bool:
    """Bakanlık mesajı "zaten mevcut / tekrar veri" mi diyor?

    Saha yanıt metni gözlemlendikçe ``SAIS_SIM_DUPLICATE_KEYWORDS`` ile
    genişletilebilir (Bakanlık status kodlarında 205 = "Tekrar Veri").
    """
    if not message:
        return False
    low = message.casefold()
    for kw in _conf("SAIS_SIM_DUPLICATE_KEYWORDS", ()):
        if kw and kw.casefold() in low:
            return True
    return False


# ------------------------------------------------------------ Drenaj döngüsü


def _lock_key(cabinet_id: int) -> str:
    return f"sais:sim:drain:{cabinet_id}"


def drain_cabinet(cabinet, *, budget_sec=None, max_entries=None,
                  use_lock=True) -> dict:
    """Bir kabinin kuyruğunu sıkı FIFO ile boşaltır.

    Şeritler sırayla gezilir: önce canlı dakikalar, sonra geçmiş veri
    backfill'i. Canlı şerit tıkalıysa backfill'e **hiç** geçilmez (Bakanlık
    zaten yanıt vermiyor demektir).
    """
    cabinet_id = cabinet.id if hasattr(cabinet, "id") else int(cabinet)
    token = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
    lock = _lock_key(cabinet_id)
    lock_ttl = int(_conf("SAIS_SIM_OUTBOX_LOCK_TTL_SEC", 280))

    # Kilit: aynı kabin için eşzamanlı drenaj sırayı bozar ve aynı dakikayı iki
    # kez gönderebilir. `cache.add` Redis'te atomik SETNX'tir; TTL sayesinde
    # worker çökse bile kilit kendiliğinden düşer.
    if use_lock and not cache.add(lock, token, lock_ttl):
        return {"cabinet_id": cabinet_id, "skipped": "locked"}

    budget = int(budget_sec if budget_sec is not None
                 else _conf("SAIS_SIM_OUTBOX_DRAIN_BUDGET_SEC", 60))
    cap = int(max_entries if max_entries is not None
              else _conf("SAIS_SIM_OUTBOX_DRAIN_MAX", 60))
    max_attempts = int(_conf("SAIS_SIM_OUTBOX_MAX_ATTEMPTS", 10))
    window = accept_window_hours()
    gap_ms = int(_conf("SAIS_SIM_OUTBOX_SEND_GAP_MS", 0))

    deadline = time.monotonic() + budget
    stats = {
        "cabinet_id": cabinet_id, "sent": 0, "failed": 0,
        "expired": 0, "blocked": False, "reason": "",
    }

    try:
        with SaisSimClient(cabinet) as client:
            for lane in SimOutboxEntry.LANES:
                poison = None  # tembel hesap — yalnız ilk ret gerektiğinde
                while stats["sent"] + stats["failed"] < cap:
                    if time.monotonic() >= deadline:
                        stats["reason"] = "budget"
                        break

                    entry = SimOutboxEntry.head(cabinet_id, lane)
                    if entry is None:
                        break

                    now = timezone.now()

                    # Kabul penceresini aşmış → ilerle (Bakanlık zaten almaz).
                    if entry.is_expired(window, now):
                        SimOutboxEntry.objects.filter(
                            pk=entry.pk, status=SimOutboxEntry.STATUS_PENDING,
                        ).update(
                            status=SimOutboxEntry.STATUS_EXPIRED, payload={},
                            last_error_kind="expired",
                            last_error="Bakanlık kabul penceresi aşıldı.",
                            updated_at=now,
                        )
                        stats["expired"] += 1
                        continue

                    # Backoff bekliyor → SONRAKİ KAYDA ATLAMA (sıkı FIFO).
                    if entry.next_attempt_at and entry.next_attempt_at > now:
                        stats["blocked"] = True
                        stats["reason"] = "backoff"
                        break

                    # Atomik sahiplenme: rowcount 0 ise başkası kaptı.
                    claimed = SimOutboxEntry.objects.filter(
                        pk=entry.pk, status=SimOutboxEntry.STATUS_PENDING,
                    ).update(
                        status=SimOutboxEntry.STATUS_SENDING,
                        claimed_at=now, last_attempt_at=now,
                        attempts=F("attempts") + 1,
                    )
                    if not claimed:
                        break
                    entry.refresh_from_db()

                    outcome, category, message, http_status = send_entry(client, entry)

                    if outcome == sim_errors.ACCEPTED:
                        _mark_sent(entry, message, http_status)
                        stats["sent"] += 1
                        poison = None  # başarı zinciri kırar
                        if gap_ms:
                            time.sleep(gap_ms / 1000.0)
                        continue

                    if outcome == sim_errors.RETRIABLE:
                        _mark_retry(entry, category, message, http_status)
                        stats["blocked"] = True
                        stats["reason"] = category
                        logger.warning(
                            "SİM kuyruk BLOKE (kabin=%s, dakika=%s, %s): %s",
                            cabinet_id, entry.readtime_str, category, message,
                        )
                        break  # kuyruk bloke — asıl gereksinim

                    # outcome == PERMANENT (kalıcı ret)
                    if poison is None:
                        poison = _poison_mode(cabinet_id, lane)
                    if poison or entry.attempts >= max_attempts:
                        _mark_failed(entry, category, message, http_status)
                        stats["failed"] += 1
                        logger.error(
                            "SİM kuyruk: dakika REDDEDİLDİ, atlanıyor "
                            "(kabin=%s, dakika=%s, deneme=%s, zehirli=%s): %s",
                            cabinet_id, entry.readtime_str, entry.attempts,
                            poison, message,
                        )
                        continue  # kuyruk ilerler
                    _mark_retry(entry, category, message, http_status, rejected=True)
                    stats["blocked"] = True
                    stats["reason"] = category
                    break

                if stats["blocked"]:
                    # Canlı şerit tıkalıysa backfill'i denemenin anlamı yok.
                    break
    finally:
        if use_lock and cache.get(lock) == token:
            cache.delete(lock)

    return stats


def _mark_sent(entry, message, http_status) -> None:
    """Kabul edilen kaydı kapat + 'SİM'e son iletim' damgasını güncelle."""
    now = timezone.now()
    SimOutboxEntry.objects.filter(pk=entry.pk).update(
        status=SimOutboxEntry.STATUS_SENT, sent_at=now, payload={},
        last_error_kind="", last_error="", last_http_status=http_status,
        ministry_message=(message or "")[:500], next_attempt_at=now,
        updated_at=now,
    )
    # Damga YALNIZ gerçek kabulde atılır. Eskiden her HTTP 200 kabul sayıldığı
    # için Bakanlık reddederken bile panel yeşil görünüyordu.
    if entry.priority == SimOutboxEntry.PRIORITY_LIVE:
        SystemSwitch.mark_sim_success(entry.readtime_str)


def _mark_retry(entry, category, message, http_status, *, rejected=False) -> None:
    """Kaydı kuyruğa geri koy + backoff uygula (kuyruk bloke kalır)."""
    now = timezone.now()
    SimOutboxEntry.objects.filter(pk=entry.pk).update(
        status=SimOutboxEntry.STATUS_PENDING,
        next_attempt_at=now + timedelta(
            seconds=backoff_seconds(entry.attempts, rejected=rejected)
        ),
        first_error_at=entry.first_error_at or now,
        last_error_kind=category, last_error=(message or "")[:500],
        last_http_status=http_status,
        ministry_message=((message or "")[:500] if rejected else entry.ministry_message),
        claimed_at=None, updated_at=now,
    )


def _mark_failed(entry, category, message, http_status) -> None:
    """Kalıcı reddi kapat — kuyruk bu kaydı atlayıp ilerler."""
    now = timezone.now()
    SimOutboxEntry.objects.filter(pk=entry.pk).update(
        status=SimOutboxEntry.STATUS_FAILED, payload={},
        first_error_at=entry.first_error_at or now,
        last_error_kind=category, last_error=(message or "")[:500],
        last_http_status=http_status, ministry_message=(message or "")[:500],
        claimed_at=None, updated_at=now,
    )
