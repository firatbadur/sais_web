"""Açılışta PC kapalı kalma süresini tespit edip PowerOff kaydı düşer.

Heartbeat deseni: çalışan stack `api.tasks.heartbeat_task` ile her dakika
`SystemHeartbeat.last_seen`'i tazeler. PC kapanınca damga durur. Bu komut
container açılış zincirinde (migrate sonrası, gunicorn öncesi) bir kez çalışır:
son damga ile şimdiki zaman arasındaki boşluğu ölçer ve
`settings.POWEROFF_DETECT_THRESHOLD_MIN` (varsayılan 5 dk) eşiğini aşarsa o
aralığı **her istasyon için** bir `PowerOff` kaydına yazar
(start_date=son damga ≈ kapanma anı, end_date=şimdi ≈ açılış anı).

Graceful sinyale (SIGTERM) bağlı olmadığı için elektrik kesintisi / sert
kapanmayı da yakalar. Kısa container restart'ları (Watchtower güncellemesi vb.)
eşiğin altında kaldığı için kayıt üretmez.

Komut sonunda damgayı şimdiki zamana çeker — hızlı tekrar açılışlarda
(crash-loop) aynı aralığın ikinci kez kaydedilmesini önler (idempotent).

Manuel debug:
    python manage.py detect_power_off
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import PowerOff, Station, SystemHeartbeat


class Command(BaseCommand):
    help = "Açılışta PC kapalı kalma süresini tespit edip PowerOff kaydı düşer."

    def handle(self, *args, **options):
        now = timezone.now()
        threshold_min = float(getattr(settings, "POWEROFF_DETECT_THRESHOLD_MIN", 5))

        hb = SystemHeartbeat.objects.filter(pk=1).first()
        if hb is None:
            # İlk açılış — referans damga yok, sadece başlat.
            SystemHeartbeat.objects.create(pk=1, last_seen=now)
            self.stdout.write("İlk açılış: heartbeat başlatıldı, kapanma kaydı yok.")
            return

        last_seen = hb.last_seen
        gap_min = (now - last_seen).total_seconds() / 60.0

        if gap_min >= threshold_min:
            stations = list(Station.objects.all())
            records = [
                PowerOff(station=s, start_date=last_seen, end_date=now)
                for s in stations
            ]
            if records:
                PowerOff.objects.bulk_create(records)
            self.stdout.write(self.style.WARNING(
                f"PC kapali kalmis: {last_seen:%d.%m.%Y %H:%M} -> {now:%d.%m.%Y %H:%M} "
                f"(~{gap_min:.0f} dk) - {len(records)} istasyon icin PowerOff kaydi dusuldu."
            ))
        else:
            self.stdout.write(
                f"Boşluk {gap_min:.1f} dk < eşik {threshold_min:.0f} dk — kapanma kaydı yok."
            )

        # Damgayı tazele: hızlı tekrar açılışta aynı aralık ikinci kez kaydedilmesin.
        hb.last_seen = now
        hb.save(update_fields=["last_seen", "updated_at"])
