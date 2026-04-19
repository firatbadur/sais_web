"""
django_celery_beat PeriodicTask kayıtlarını oluşturur (idempotent).

Kullanım:
    python manage.py seed_periodic_tasks

Bu komut beat scheduler'ın okuyacağı 7 periyodik task'ı DB'ye yazar:
  - scada_io.tasks.dispatch_polls       (her 5 sn)
  - scada_io.tasks.dispatch_commands    (her 3 sn)
  - scada_io.tasks.expire_commands      (her 60 sn)
  - api.tasks.aggregate_readings_15m    (cron: */5 * * * *)
  - api.tasks.aggregate_readings_hourly (cron: 5 * * * *)
  - api.tasks.aggregate_readings_daily  (cron: 0 1 * * *)
  - api.tasks.prune_api_logs_task       (cron: 0 3 * * *)

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
]

CRONTAB_TASKS = [
    # (task name, minute, hour, day_of_week, day_of_month, month_of_year)
    ("api.tasks.aggregate_readings_15m",    "*/5", "*", "*", "*", "*"),
    ("api.tasks.aggregate_readings_hourly", "5",   "*", "*", "*", "*"),
    ("api.tasks.aggregate_readings_daily",  "0",   "1", "*", "*", "*"),
    ("api.tasks.prune_api_logs_task",       "0",   "3", "*", "*", "*"),
]


class Command(BaseCommand):
    help = "Celery beat periyodik task kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
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

        self.stdout.write(self.style.SUCCESS(
            f"Periyodik task seed tamamlandı: {created} yeni, {updated} mevcut güncellendi."
        ))
