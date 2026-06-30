"""Sistem alarmlarını senkron çalıştır (debug).

Kullanım::

    python manage.py check_system_alarms_once            # realtime + daily
    python manage.py check_system_alarms_once --daily     # yalnız SSL/lisans/kalibrasyon
    python manage.py check_system_alarms_once --realtime  # yalnız veri hatası/kesinti

Celery olmadan elle tetiklemek / bildirim akışını denemek için. Lisans gate'i
yok; gerçek bildirim gönderir (NotificationSettings + alıcılar yapılandırılmışsa).
"""
from django.core.management.base import BaseCommand

from sais_domain.system_alarms import run_daily, run_realtime


class Command(BaseCommand):
    help = "Sistem alarmlarını senkron değerlendirir (debug)."

    def add_arguments(self, parser):
        parser.add_argument("--daily", action="store_true", help="Yalnız günlük kontroller")
        parser.add_argument("--realtime", action="store_true", help="Yalnız 10dk kontroller")

    def handle(self, *args, **options):
        only_daily = options.get("daily")
        only_rt = options.get("realtime")
        run_both = not (only_daily or only_rt)
        if run_both or only_rt:
            self.stdout.write(self.style.SUCCESS(f"realtime: {run_realtime()}"))
        if run_both or only_daily:
            self.stdout.write(self.style.SUCCESS(f"daily: {run_daily()}"))
