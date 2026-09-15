"""
Config-only veri göçü için model listesi (MSSQL → PostgreSQL geçişinde kullanılır).

`export_config` / `import_config` yönetim komutları bu listeyi paylaşır. Liste
SADECE konfigürasyon kayıtlarını içerir; historian / runtime / audit tabloları
KASTEN dışarıda bırakılır (geçmiş ölçüm verisi taşınmaz, yeni DB'de sıfırdan
birikmeye başlar).

Sıra ebeveyn → çocuk olacak şekilde düzenlidir (okunabilirlik için; PostgreSQL FK
kısıtları Django'da DEFERRABLE INITIALLY DEFERRED olduğundan loaddata sırası FK
bütünlüğü için kritik DEĞİL).
"""
from __future__ import annotations

# dumpdata/loaddata için "app_label.ModelName" etiketleri.
CONFIG_MODELS: list[str] = [
    # --- Kimlik / yetki ---
    "auth.Group",
    "users.CustomUser",
    # --- Jenerik SCADA çekirdeği (api) ---
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
    "api.License",
    "api.WebSettings",
    "api.NotificationSettings",
    "api.MessageTemplate",
    "api.AlarmRule",
    "api.Reminder",
    "api.ReportTemplate",
    "api.ReportSchedule",
    "dashboard.MimicScreen",
    # --- SAIS-özel (sais_domain) ---
    "sais_domain.SaisCabinet",
    "sais_domain.EnvisoftChannel",
    "sais_domain.SystemSwitch",
    "sais_domain.SystemAlarmSettings",
    "sais_domain.SimStatusPolicy",
    "sais_domain.Scenario",
    "sais_domain.ScenarioParameter",
    "sais_domain.ScenarioStep",
    "sais_domain.ScenarioGraph",
    # --- Periyodik task tanımları (Celery beat) ---
    "django_celery_beat.IntervalSchedule",
    "django_celery_beat.CrontabSchedule",
    "django_celery_beat.PeriodicTask",
]

# import_config boşluk kontrolü bu modeller üzerinden yapılır (varsa DB boş değil).
EMPTINESS_GUARD_MODELS: list[str] = ["api.Station", "api.Parameter"]


def reset_sequences(labels) -> int:
    """PK korunarak yüklenen modellerin sequence'lerini max(id)'ye taşır (PG).

    Atlanırsa yükleme başarılı görünür ama ilk YENİ kayıtta duplicate-PK ile
    patlar. Dönen değer çalıştırılan SQL sayısıdır.
    """
    from django.apps import apps
    from django.core.management.color import no_style
    from django.db import connection

    models = [apps.get_model(label) for label in labels]
    statements = connection.ops.sequence_reset_sql(no_style(), models)
    if statements:
        with connection.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)
    return len(statements)
