"""SİM gönderim kuyruğundan (SimOutboxEntry) retention'ı geçenleri siler.

İletilmiş kayıtlar kısa süre (denetim/teşhis için) tutulur; iletilemeden
sonuçlanmış (reddedilen / süresi dolan / atlanan) kayıtlar daha uzun saklanır
çünkü operatörün "hangi dakikalar Bakanlık'a gitmedi" sorusunu yanıtlarlar.

**Bekleyen (`pending` / `sending`) kayıtlar ASLA silinmez** — onlar hâlâ
iletilmeyi bekleyen gerçek veridir.

Kullanım:
    python manage.py prune_sim_outbox                  # settings varsayılanları
    python manage.py prune_sim_outbox --days=3         # iletilmişler için özel periyot
    python manage.py prune_sim_outbox --dead-days=60   # ölü kayıtlar için özel periyot
    python manage.py prune_sim_outbox --dry-run        # silmeden sayım
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from sais_domain.models import SimOutboxEntry


#: Tek transaction'da silinecek satır sayısı — uzun kilitlerden kaçınmak için
#: `prune_readings` ile aynı batch deseni.
BATCH = 10000


class Command(BaseCommand):
    help = "SİM gönderim kuyruğundan retention'ı geçen kayıtları siler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=None,
            help="İletilmiş kayıtların saklama süresi (gün). "
                 "Verilmezse settings.SIM_OUTBOX_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dead-days", type=int, default=None,
            help="Reddedilen/süresi dolan/atlanan kayıtların saklama süresi (gün). "
                 "Verilmezse settings.SIM_OUTBOX_DEAD_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Sayımı yapar ama silmez.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = int(getattr(settings, "SIM_OUTBOX_RETENTION_DAYS", 7))
        dead_days = options["dead_days"]
        if dead_days is None:
            dead_days = int(getattr(settings, "SIM_OUTBOX_DEAD_RETENTION_DAYS", 30))
        if days <= 0 or dead_days <= 0:
            self.stderr.write(self.style.ERROR("Gün değerleri pozitif olmalı."))
            return

        now = timezone.now()
        targets = [
            (
                "iletilmiş",
                SimOutboxEntry.objects.filter(
                    status=SimOutboxEntry.STATUS_SENT,
                    sent_at__lt=now - timedelta(days=days),
                ),
            ),
            (
                "iletilemeyen",
                SimOutboxEntry.objects.filter(
                    status__in=SimOutboxEntry.DEAD_STATUSES,
                    updated_at__lt=now - timedelta(days=dead_days),
                ),
            ),
        ]

        total = 0
        for label, qs in targets:
            count = qs.count()
            total += count
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING(
                    f"[dry-run] {label}: {count} kayıt silinecek"
                ))
                continue
            deleted = 0
            while True:
                ids = list(qs.values_list("id", flat=True)[:BATCH])
                if not ids:
                    break
                removed, _ = SimOutboxEntry.objects.filter(id__in=ids).delete()
                deleted += removed
            self.stdout.write(self.style.SUCCESS(f"{label}: {deleted} kayıt silindi"))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"[dry-run] toplam: {total}"))
