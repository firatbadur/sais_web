"""
Config göçü (import_config / loaddata) SONRASI çift kayıt temizliği.

NEDEN: taze DB'ye fixture yüklenirken iki kaynak aynı kayıtları üretir →

  * StatusCode : migration `api.0027` Bakanlık status kodlarını seed eder; eski
                 sahanın export'unda da aynı kodlar vardır (farklı PK) → aynı
                 `code` iki kez. `seed_initial_data` bunu görünce
                 `MultipleObjectsReturned` ile düşer.
  * AlarmRule  : `api.alarm_autocreate` sinyali, fixture'daki Sensor kayıtları
                 yüklenirken tetiklenip varsayılan alarmları kurar; hemen
                 ardından fixture'ın kendi AlarmRule'ları yüklenir → her
                 parametre için iki kural.

HANGİSİ KALIR:
  * StatusCode : başka tablolardan FK referansı olan kayıt (yoksa en küçük id).
  * AlarmRule  : en ESKİ `created_at` — yani sahanın gerçek ayarları. loaddata
                 `raw=True` ile kaydettiği için fixture'ın created_at'i korunur;
                 autocreate ile o an kurulanlar "bugün" damgalıdır.

KULLANIM (saha, göç sonrası, seed_initial_data'dan ÖNCE):
    docker cp post_migration_dedupe.py sais_web-web-1:/app/dedupe.py
    docker compose ... exec -T web python /app/dedupe.py
    docker compose ... exec -T web python manage.py seed_initial_data

Salt-rapor için: `python /app/dedupe.py --dry-run`
"""
from __future__ import annotations

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sais_web.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from api.models import AlarmRule, StatusCode  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv


def _ref_count(obj) -> int:
    """Bu StatusCode'a başka tablolardan kaç FK referansı var?"""
    total = 0
    for rel in StatusCode._meta.related_objects:
        try:
            total += rel.related_model._default_manager.filter(
                **{rel.field.name: obj}
            ).count()
        except Exception:  # noqa: BLE001 — beklenmedik ilişki tipini atla
            continue
    return total


def _dedupe(groups: dict, sort_key, label: str) -> int:
    removed = 0
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=sort_key)
        keep, extras = rows[0], rows[1:]
        for extra in extras:
            print(f"  {label}: id={extra.pk} silinecek (kalan id={keep.pk})")
            if not DRY_RUN:
                extra.delete()
            removed += 1
    return removed


def main() -> None:
    codes: dict = {}
    for sc in StatusCode.objects.order_by("id"):
        codes.setdefault(sc.code, []).append(sc)
    removed_sc = _dedupe(
        codes, lambda r: (-_ref_count(r), r.id), "StatusCode"
    )

    rules: dict = {}
    for rule in AlarmRule.objects.order_by("id"):
        key = (rule.station_id, rule.rule_type, rule.parameter_id, rule.sensor_id)
        rules.setdefault(key, []).append(rule)
    removed_ar = _dedupe(
        rules,
        lambda r: (r.created_at or timezone.now(), r.pk),
        "AlarmRule",
    )

    prefix = "[DRY-RUN] " if DRY_RUN else ""
    print(
        f"{prefix}StatusCode silinen: {removed_sc}, kalan: {StatusCode.objects.count()}"
    )
    print(
        f"{prefix}AlarmRule  silinen: {removed_ar}, kalan: {AlarmRule.objects.count()}"
    )


if __name__ == "__main__":
    main()
