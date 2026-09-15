"""
İstasyon taşıma — konfigürasyonun dışa/içe aktarımı (Yedekleme sayfası).

Bir kurulumun tüm konfigürasyonunu (istasyon/bağlantı/sensör/parametre/kabin/
senaryo/alarm/rapor/mimik + seçime bağlı kullanıcı/web/bildirim/periyodik task)
**ölçüm geçmişi olmadan** tek bir JSON dosyasına alır ve başka bir kuruluma
"tamamen değiştir" semantiğiyle yükler.

`export_config` / `import_config` (MSSQL→PG göçü, boş DB) ile aynı serileştirme
ayarlarını kullanır ama farkları:
  - hedef DB boş olmak ZORUNDA DEĞİL: seçili bölümlerin kayıtları silinip dosyadaki
    yüklenir (tek transaction; hata olursa hiçbir şey değişmez),
  - lookup tabloları (StatusCode vb.) silinmez, PK ile upsert edilir — silmek
    SystemLog / Command gibi runtime kayıtlarını cascade ile götürürdü,
  - seçilmeyen bölümlere işaret eden referanslar (ör. kullanıcılar taşınmadıysa
    `MimicScreen.created_by`) hedefte yoksa null'lanır / kayıt atlanır,
  - lisans ASLA taşınmaz (yeni kurulumda grace/kilitlenme riski).

Tek doğruluk kaynağı: `SECTIONS`.
"""
from __future__ import annotations

import socket

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.utils import timezone

from api.config_migration import reset_sequences

FORMAT = "envisoft_webx.config_transfer"
VERSION = 1

# Bölüm anahtarı → (etiket, zorunlu mu, model listesi). Sıra ebeveyn → çocuk;
# yükleme bu sırayla, silme ters sırayla yapılır. Kullanıcılar en başta —
# CustomUser doğal anahtarla (username) referanslandığından diğer kayıtlar
# deserialize edilirken hedefte bulunmalı.
SECTIONS: dict[str, dict] = {
    "users": {
        "label": "Kullanıcılar ve gruplar",
        "required": False,
        "models": ["auth.Group", "users.CustomUser"],
    },
    "core": {
        "label": "İstasyon konfigürasyonu",
        "required": True,
        "models": [
            "api.StationType",
            "api.Station",
            "api.StationAuthority",
            "api.Connection",
            "api.ScanGroup",
            "api.StatusCode",
            "api.Parameter",
            "api.Sensor",
            "api.RequestType",
            "api.LogType",
            "api.Calibration",
            "api.BackupPolicy",
            "api.MessageTemplate",
            "api.AlarmRule",
            "api.Reminder",
            "api.ReportTemplate",
            "api.ReportSchedule",
            "dashboard.MimicScreen",
            "sais_domain.SaisCabinet",
            "sais_domain.EnvisoftChannel",
            "sais_domain.SystemSwitch",
            "sais_domain.SystemAlarmSettings",
            "sais_domain.SimStatusPolicy",
            "sais_domain.Scenario",
            "sais_domain.ScenarioParameter",
            "sais_domain.ScenarioStep",
            "sais_domain.ScenarioGraph",
        ],
    },
    "web": {
        "label": "Web erişim / SSL ayarları",
        "required": False,
        "models": ["api.WebSettings"],
    },
    "notifications": {
        "label": "Bildirim (SMTP / SMS) ayarları",
        "required": False,
        "models": ["api.NotificationSettings"],
    },
    "periodic_tasks": {
        "label": "Periyodik görev tanımları",
        "required": False,
        "models": [
            "django_celery_beat.IntervalSchedule",
            "django_celery_beat.CrontabSchedule",
            "django_celery_beat.PeriodicTask",
        ],
    },
}

# Silinmeyen, PK ile upsert edilen lookup tabloları (runtime kayıtları CASCADE ile
# bunlara bağlı: SystemLog.type, Command.request_type ...).
LOOKUP_MODELS: set[str] = {
    "api.StationType",
    "api.StatusCode",
    "api.RequestType",
    "api.LogType",
}


class ConfigTransferError(Exception):
    """Dosya geçersiz / uyumsuz / uygulanamaz — kullanıcıya gösterilecek mesaj."""


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #

def normalize_sections(sections) -> list[str]:
    """Geçerli bölüm anahtarları (zorunlular her zaman dahil), SECTIONS sırasıyla."""
    wanted = set(sections or [])
    return [
        key for key, spec in SECTIONS.items()
        if spec["required"] or key in wanted
    ]


def labels_for(sections) -> list[str]:
    out: list[str] = []
    for key in normalize_sections(sections):
        out.extend(SECTIONS[key]["models"])
    return out


def _label(model) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def _model_for(label: str):
    return apps.get_model(label)


# --------------------------------------------------------------------------- #
# Dışa aktarım
# --------------------------------------------------------------------------- #

def build_export(sections) -> dict:
    """Seçili bölümlerin kayıtlarını taşınabilir zarfa serileştirir.

    `EncryptedCharField` Python'da düz metin sunduğundan kabin şifreleri dosyada
    düz metin olur ve hedefte hedefin kendi anahtarıyla yeniden şifrelenir.
    """
    from api.db_admin import current_migration_state

    chosen = normalize_sections(sections)
    objects: list[dict] = []
    counts: dict[str, int] = {}
    for label in labels_for(chosen):
        model = _model_for(label)
        qs = model._default_manager.order_by("pk")
        data = serializers.serialize(
            "python", qs,
            use_natural_foreign_keys=True,
            use_natural_primary_keys=False,
        )
        counts[label] = len(data)
        objects.extend(data)

    return {
        "format": FORMAT,
        "version": VERSION,
        "app_version": getattr(settings, "APP_VERSION", "dev"),
        "migration_state": current_migration_state(),
        "exported_at": timezone.now().isoformat(),
        "hostname": socket.gethostname(),
        "sections": chosen,
        "counts": counts,
        "objects": objects,
    }


# --------------------------------------------------------------------------- #
# İnceleme (yükleme öncesi)
# --------------------------------------------------------------------------- #

def inspect_payload(data, sections=None) -> dict:
    """Zarfı doğrular, uyumluluk + özet döndürür. Geçersizse ConfigTransferError.

    `sections` verilirse yalnız dosyada bulunan ∩ istenen bölümler uygulanır.
    """
    from api.db_admin import compare_schema

    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ConfigTransferError("Dosya bir Envisoft WebX konfigürasyon aktarım dosyası değil.")
    if data.get("version") != VERSION:
        raise ConfigTransferError(f"Desteklenmeyen dosya sürümü: {data.get('version')}.")
    if not isinstance(data.get("objects"), list):
        raise ConfigTransferError("Dosyada kayıt listesi bulunamadı.")

    file_sections = [s for s in (data.get("sections") or []) if s in SECTIONS]
    if "core" not in file_sections:
        raise ConfigTransferError("Dosyada istasyon konfigürasyonu bölümü yok.")
    if sections is None:
        applied = normalize_sections(file_sections)
    else:
        applied = [s for s in normalize_sections(sections) if s in file_sections]

    verdict = compare_schema(data.get("migration_state"))
    if verdict["verdict"] == "block":
        raise ConfigTransferError(
            "Dosya bu uygulamadan daha yeni bir sürümde alınmış; önce uygulamayı "
            f"{data.get('app_version') or 'o sürüme'} yükseltin. ({verdict['message']})"
        )

    applied_labels = set(labels_for(applied))
    counts: dict[str, int] = {}
    for obj in data["objects"]:
        model = obj.get("model", "")
        label = _canonical_label(model)
        if label in applied_labels:
            counts[label] = counts.get(label, 0) + 1

    if "users" in applied and not _has_active_admin(data["objects"]):
        raise ConfigTransferError(
            "Dosyadaki kullanıcılar arasında aktif bir Sistem Yöneticisi yok; "
            "yüklenirse panele kimse giremez. Kullanıcılar bölümünü hariç tutun."
        )

    return {
        "app_version": data.get("app_version") or "",
        "hostname": data.get("hostname") or "",
        "exported_at": data.get("exported_at") or "",
        "file_sections": file_sections,
        "sections": applied,
        "counts": counts,
        "verdict": verdict["verdict"],
        "verdict_message": verdict["message"],
    }


def _canonical_label(model_lower: str) -> str:
    """Serializer 'api.station' → 'api.Station' (bilinmeyen model → olduğu gibi)."""
    try:
        return _label(apps.get_model(model_lower))
    except (LookupError, ValueError):
        return model_lower


def _has_active_admin(objects) -> bool:
    user_label = settings.AUTH_USER_MODEL.lower()
    for obj in objects:
        if obj.get("model") != user_label:
            continue
        f = obj.get("fields") or {}
        if f.get("is_active", True) and (f.get("is_superuser") or f.get("rol") == 1):
            return True
    return False


# --------------------------------------------------------------------------- #
# İçe aktarım
# --------------------------------------------------------------------------- #

def apply_import(data, sections=None) -> dict:
    """Seçili bölümleri hedefte "tamamen değiştirir" (tek transaction).

    Dönen dict: {"sections", "loaded": {label: n}, "skipped": n, "nulled": n}.
    """
    summary = inspect_payload(data, sections)
    applied = summary["sections"]
    labels = labels_for(applied)
    label_set = set(labels)
    reloaded_models = {_model_for(lbl) for lbl in labels}

    # Yükleme sırası dosya sırasından bağımsız olarak SECTIONS sırası.
    order = {lbl: i for i, lbl in enumerate(labels)}
    objects = [o for o in data["objects"] if _canonical_label(o.get("model", "")) in label_set]
    objects.sort(key=lambda o: order[_canonical_label(o["model"])])

    # Dosyayı üreten sürümde var olmayan bir model (ör. eski sürümde henüz
    # eklenmemiş MimicScreen) hedefte silinmesin — yalnız dosyada sayımı olanlar.
    present = set(data.get("counts") or labels)

    loaded: dict[str, int] = {}
    skipped = 0
    nulled = 0

    with transaction.atomic():
        # 1) Sil (ters sıra) — lookup tabloları hariç.
        for lbl in reversed(labels):
            if lbl in LOOKUP_MODELS or lbl not in present:
                continue
            _model_for(lbl)._base_manager.all().delete()

        # 2) Yükle. Seçilmeyen bölümlere işaret eden sarkık referansları temizle.
        for obj in objects:
            model = _model_for(obj["model"])
            keep, n = _prune_dangling_refs(obj, model, reloaded_models)
            nulled += n
            if not keep:
                skipped += 1
                continue
            for des in serializers.deserialize(
                "python", [obj], ignorenonexistent=True,
                handle_forward_references=False,
            ):
                des.save()
            lbl = _label(model)
            loaded[lbl] = loaded.get(lbl, 0) + 1

        # 3) PostgreSQL sequence'leri max(id)'ye taşı.
        reset_sequences(labels)

        # 4) Raw kayıtta atlanan türetilmiş alanlar / yan etkiler.
        _post_import_fixups(applied)

        transaction.on_commit(lambda: _after_commit(applied))

    return {"sections": applied, "loaded": loaded, "skipped": skipped, "nulled": nulled}


def _ref_exists(related_model, value) -> bool:
    manager = related_model._default_manager
    if isinstance(value, (list, tuple)):
        try:
            manager.get_by_natural_key(*value)
            return True
        except (ObjectDoesNotExist, AttributeError):
            return False
    return manager.filter(pk=value).exists()


def _prune_dangling_refs(obj: dict, model, reloaded_models: set) -> tuple[bool, int]:
    """Yeniden yüklenmeyen modellere işaret eden ve hedefte bulunmayan FK/M2M'leri
    temizler. Null olabilen FK → None; olamayan → kayıt atlanır (False)."""
    fields = obj.get("fields") or {}
    nulled = 0
    for field in model._meta.get_fields():
        if not getattr(field, "concrete", False) or field.name not in fields:
            continue
        related = getattr(field, "related_model", None)
        if related is None or related in reloaded_models:
            continue
        value = fields[field.name]
        if isinstance(field, models.ManyToManyField):
            kept = [v for v in (value or []) if _ref_exists(related, v)]
            if len(kept) != len(value or []):
                fields[field.name] = kept
                nulled += 1
        elif isinstance(field, models.ForeignKey):
            if value is None or _ref_exists(related, value):
                continue
            if field.null:
                fields[field.name] = None
                nulled += 1
            else:
                return False, nulled
    return True, nulled


def _post_import_fixups(applied) -> None:
    ReportSchedule = _model_for("api.ReportSchedule")
    for sched in ReportSchedule.objects.all():
        sched.next_run_at = None  # save() bir sonraki koşuyu yeniden hesaplar
        sched.save()

    # Kabin ↔ Bakanlık kullanıcısı senkronu (raw kayıtta sinyal atlanır).
    from sais_domain.models import SaisCabinet
    from sais_domain.signals import sync_cabinet_ministry_user

    for cabinet in SaisCabinet.objects.all():
        sync_cabinet_ministry_user(cabinet)

    if "periodic_tasks" in applied:
        from django_celery_beat.models import PeriodicTasks

        PeriodicTasks.update_changed()  # beat toplu değişikliği görsün


# --------------------------------------------------------------------------- #
# Kayıtlı akış (dashboard + komut + Celery ortak)
# --------------------------------------------------------------------------- #

TRANSFER_SUBDIR = "config_transfers"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def transfer_dir() -> str:
    import os

    path = os.path.join(settings.BACKUP_DIR, TRANSFER_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def export_filename() -> str:
    host = "".join(c if c.isalnum() or c in "-_" else "-" for c in socket.gethostname())
    return f"envisoft-config-{host}-{timezone.localtime():%Y%m%d-%H%M}.json"


def record_export(payload: dict, filename: str, user=None):
    from api.models import ConfigTransfer

    return ConfigTransfer.objects.create(
        direction="export", status="success", filename=filename,
        sections=payload["sections"], source_app_version=payload["app_version"],
        source_hostname=payload["hostname"], exported_at=payload["exported_at"],
        counts=payload["counts"], user=user, finished_at=timezone.now(),
    )


def stage_import(raw: bytes, filename: str, sections, user=None):
    """Yüklenen dosyayı doğrular, diske yazar ve onay bekleyen kayıt açar.

    Önceki onay bekleyen içe aktarımlar iptal edilir (tek bekleyen kural).
    """
    import json
    import os

    from api.models import ConfigTransfer

    if len(raw) > MAX_UPLOAD_BYTES:
        raise ConfigTransferError("Dosya çok büyük (en fazla 20 MB).")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ConfigTransferError(f"Dosya okunamadı (geçerli JSON değil): {exc}")
    summary = inspect_payload(data, sections)

    discard_pending()
    safe = os.path.basename(filename or "config.json")[:150]
    path = os.path.join(transfer_dir(), f"{timezone.localtime():%Y%m%d_%H%M%S}__{safe}")
    with open(path, "wb") as fh:
        fh.write(raw)

    return ConfigTransfer.objects.create(
        direction="import", status="pending", filename=safe, path=path,
        sections=summary["sections"], source_app_version=summary["app_version"],
        source_hostname=summary["hostname"], exported_at=summary["exported_at"],
        compatibility=summary["verdict"], counts=summary["counts"], user=user,
    )


def discard_pending() -> int:
    from api.models import ConfigTransfer

    pending = list(ConfigTransfer.objects.filter(direction="import", status="pending"))
    for rec in pending:
        rec.delete_file()
        rec.delete()
    return len(pending)


def run_import(transfer_id: int, skip_safety_backup: bool = False) -> dict:
    """Onaylanmış içe aktarımı yürütür: güvenlik yedeği → apply_import.

    Güvenlik yedeği alınamazsa konfigürasyona DOKUNULMAZ.
    """
    import json

    from django.core.management import call_command

    from api.models import ConfigTransfer, DatabaseBackup

    rec = ConfigTransfer.objects.get(pk=transfer_id)
    if rec.status not in ("pending", "running"):
        return {"skipped": rec.status}
    user_id = rec.user_id
    ConfigTransfer.objects.filter(pk=rec.pk).update(status="running", error="")

    def fail(message: str, backup=None):
        ConfigTransfer.objects.filter(pk=rec.pk).update(
            status="failed", error=message[:4000], safety_backup=backup,
            finished_at=timezone.now(),
        )
        return {"error": message}

    backup = None
    if not skip_safety_backup:
        started = timezone.now()
        try:
            call_command("backup_database", tier="manual", force=True, user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            return fail(f"Güvenlik yedeği alınamadı, konfigürasyon değiştirilmedi: {exc}")
        backup = (
            DatabaseBackup.objects.filter(tier="manual", started_at__gte=started)
            .order_by("-started_at").first()
        )
        if backup is None or backup.status != "success":
            return fail("Güvenlik yedeği doğrulanamadı, konfigürasyon değiştirilmedi.", backup)

    try:
        with open(rec.path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8-sig"))
        result = apply_import(data, rec.sections)
    except Exception as exc:  # noqa: BLE001 — transaction geri alındı
        return fail(f"İçe aktarım başarısız (değişiklik geri alındı): {exc}", backup)

    ConfigTransfer.objects.filter(pk=rec.pk).update(
        status="success", result=result, safety_backup=backup, finished_at=timezone.now(),
    )
    try:
        from api.events import EventType, log_event

        log_event(
            EventType.BACKUP,
            f"Konfigürasyon içe aktarıldı ({rec.filename}, kaynak={rec.source_hostname} "
            f"{rec.source_app_version}, bölümler={','.join(result['sections'])})",
            severity="critical",
        )
    except Exception:  # noqa: BLE001 — audit kaydı işlemi bozmasın
        pass
    return result


def _after_commit(applied) -> None:
    from api import serial_bridge

    serial_bridge.apply()
    if "web" in applied:
        from api import web_proxy

        try:
            web_proxy.apply()
        except Exception:  # noqa: BLE001 — Caddyfile üretimi import'u geri almaz
            import logging

            logging.getLogger("api.config_transfer").exception("web_proxy.apply başarısız")
