"""
django_celery_beat PeriodicTask kayıtlarını oluşturur (idempotent).

Kullanım:
    python manage.py seed_periodic_tasks

Bu komut beat scheduler'ın okuyacağı periyodik task'ları DB'ye yazar:
  - scada_io.tasks.dispatch_polls            (her 5 sn)
  - scada_io.tasks.dispatch_commands         (her 3 sn)
  - scada_io.tasks.expire_commands           (her 60 sn)
  - api.tasks.heartbeat_task                  (her 60 sn)  — PC kapanma tespiti damgası
  - api.tasks.aggregate_readings_5m          (cron: * * * * *)
  - api.tasks.aggregate_readings_15m         (cron: */5 * * * *)
  - api.tasks.aggregate_readings_hourly      (cron: 5 * * * *)
  - api.tasks.aggregate_readings_daily       (cron: 0 1 * * *)
  - api.tasks.prune_readings_task            (cron: 30 2 * * *)  — her gece 02:30
  - api.tasks.prune_api_logs_task            (cron: 0 3 * * *)   — her gece 03:00
  - api.tasks.record_public_ip_task          (cron: 17 * * * *)  — public IP değişimini izle
  - api.tasks.dispatch_report_schedules      (her 60 sn)  — vadesi gelen rapor zamanlamalarını üretime gönder
  - sais_domain.tasks.publish_minute_data    (cron: * * * * *)   — her dakika SIM + Envisoft gönderimi
  - sais_domain.tasks.run_scenarios          (cron: * * * * *)   — her dakika numune senaryosu değerlendirme
  - sais_domain.tasks.resend_missing_data    (cron: 0 */6 * * *) — eksik veri yeniden gönderimi

Tüm kayıtlar `enabled=True` ile yaratılır; istemediğiniz task'ı admin'den
disable edebilirsiniz. Komut idempotenttir — aynı task adıyla mevcut kayıt
varsa schedule'ı günceller, yenisini yaratmaz.
"""
from django.core.management.base import BaseCommand


INTERVAL_TASKS = [
    # (task name, interval seconds)
    ("scada_io.tasks.dispatch_polls", 5),
    ("scada_io.tasks.dispatch_commands", 3),
    ("scada_io.tasks.expire_commands", 60),
    # Alarm kurallarını her 60 sn'de değerlendir (SMS/e-posta gönderimi).
    ("api.tasks.run_alarms", 60),
    # Sistem canlılık damgası — PC kapanma tespiti (detect_power_off) buna dayanır.
    ("api.tasks.heartbeat_task", 60),
    # Rapor Stüdyosu — vadesi gelen zamanlanmış raporları üretime gönder.
    ("api.tasks.dispatch_report_schedules", 60),
]

CRONTAB_TASKS = [
    # (task name, minute, hour, day_of_week, day_of_month, month_of_year)
    ("api.tasks.aggregate_readings_5m",       "*",   "*", "*", "*", "*"),
    ("api.tasks.aggregate_readings_15m",      "*/5", "*", "*", "*", "*"),
    ("api.tasks.aggregate_readings_hourly",   "5",   "*", "*", "*", "*"),
    ("api.tasks.aggregate_readings_daily",    "0",   "1", "*", "*", "*"),
    ("api.tasks.prune_readings_task",         "30",  "2", "*", "*", "*"),   # her gece 02:30
    ("api.tasks.prune_api_logs_task",         "0",   "3", "*", "*", "*"),   # her gece 03:00
    # Lisans manifest'ini uzaktan çek/doğrula — her 6 saatte bir.
    ("api.tasks.license_refresh_task",        "0",   "*/6", "*", "*", "*"),
    # Public IP'yi kaydet (değişimi izle) — her saat başı (17. dk).
    ("api.tasks.record_public_ip_task",       "17",  "*", "*", "*", "*"),
    # Bakanlık SIM + Envisoft veri gönderimi — her dakika başında.
    ("sais_domain.tasks.publish_minute_data", "*",   "*", "*", "*", "*"),
    # Numune alma senaryolarını değerlendir (eşik/kademe + Bakanlık talebi) — her dakika.
    ("sais_domain.tasks.run_scenarios",       "*",   "*", "*", "*", "*"),
    # Bakanlık'ın eksik bildirdiği verileri yeniden gönder (GetMissingDates) — 6 saatte bir.
    ("sais_domain.tasks.resend_missing_data", "0",   "*/6", "*", "*", "*"),
    # Geçerli veri istatistiği (günlük/aylık) — günde 1 kez, gün gün GetDataByBetweenTwoDate.
    ("sais_domain.tasks.compute_sim_valid_stats", "30", "1", "*", "*", "*"),  # her gece 01:30
    # Sistem alarmları — Bakanlık veri hatası + kesinti (her 10 dk).
    ("sais_domain.tasks.check_system_alarms",       "*/10", "*", "*", "*", "*"),
    # Sistem alarmları — SSL + lisans + kalibrasyon (günde 1, 08:10).
    ("sais_domain.tasks.check_system_alarms_daily", "10",   "8", "*", "*", "*"),
]

# DB yedekleme — tek task (`backup_database_run`) farklı tier kwargs'ı ile.
# Her satır ayrı PeriodicTask adı (name) alır; gerçekte yedek alınıp alınmayacağına
# BackupPolicy.enabled karar verir (dashboard'dan yönetilir). Cron'lar prune
# (02:30 / 03:00) ile çakışmayacak şekilde seçildi.
BACKUP_TASKS = [
    # (name, task, kwargs, minute, hour, dow, dom, moy)
    ("api.tasks.backup_database_run:daily",   "api.tasks.backup_database_run",
        {"tier": "daily"},   "0",  "2", "*", "*", "*"),               # her gün 02:00
    ("api.tasks.backup_database_run:weekly",  "api.tasks.backup_database_run",
        {"tier": "weekly"},  "15", "2", "0", "*", "*"),               # pazar 02:15
    ("api.tasks.backup_database_run:monthly", "api.tasks.backup_database_run",
        {"tier": "monthly"}, "30", "1", "*", "1", "*"),               # ayın 1'i 01:30
    ("api.tasks.backup_database_run:yearly",  "api.tasks.backup_database_run",
        {"tier": "yearly"},  "45", "1", "*", "1", "1"),               # 1 Ocak 01:45
]


class Command(BaseCommand):
    help = "Celery beat periyodik task kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
        import json

        from django_celery_beat.models import (
            CrontabSchedule,
            IntervalSchedule,
            PeriodicTask,
        )

        created = 0
        updated = 0

        for task_name, seconds in INTERVAL_TASKS:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=seconds, period=IntervalSchedule.SECONDS,
            )
            obj, was_created = PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "task": task_name,
                    "interval": schedule,
                    "crontab": None,
                    "enabled": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  + {task_name} (every {seconds}s)"))
            else:
                updated += 1

        for task_name, minute, hour, dow, dom, moy in CRONTAB_TASKS:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute=minute, hour=hour,
                day_of_week=dow, day_of_month=dom, month_of_year=moy,
            )
            obj, was_created = PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "task": task_name,
                    "crontab": schedule,
                    "interval": None,
                    "enabled": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  + {task_name} (cron: {minute} {hour} {dom} {moy} {dow})"
                ))
            else:
                updated += 1

        # DB yedekleme task'ları (kwargs ile tier ayrımı)
        for name, task, kwargs, minute, hour, dow, dom, moy in BACKUP_TASKS:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute=minute, hour=hour,
                day_of_week=dow, day_of_month=dom, month_of_year=moy,
            )
            obj, was_created = PeriodicTask.objects.update_or_create(
                name=name,
                defaults={
                    "task": task,
                    "crontab": schedule,
                    "interval": None,
                    "kwargs": json.dumps(kwargs),
                    "enabled": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  + {name} (cron: {minute} {hour} {dom} {moy} {dow}, kwargs={kwargs})"
                ))
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Periyodik task seed tamamlandı: {created} yeni, {updated} mevcut güncellendi."
        ))
