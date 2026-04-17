# api/signals.py
import signal

from django.utils import timezone

from .models import PowerOff, Station


def handle_shutdown(*args, **kwargs):
    station = Station.objects.first()
    if not station:
        return

    open_record = PowerOff.objects.filter(station=station, end_date__isnull=True).first()
    if open_record:
        open_record.end_date = timezone.now()
        open_record.save()


def register_shutdown_handler():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)  # Ctrl+C için
