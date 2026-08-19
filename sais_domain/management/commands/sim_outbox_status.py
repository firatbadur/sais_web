"""SİM gönderim kuyruğunun durumunu yazdırır (salt okuma, saha teşhisi).

"Veri Bakanlığa gitmiyor" şikayetinde ilk bakılacak yer: kuyruk tıkalı mı,
tıkalıysa baştaki kayıt hangi dakika ve hangi hata ile bekliyor.

Kullanım:
    python manage.py sim_outbox_status
    python manage.py sim_outbox_status --cabinet 1
    python manage.py sim_outbox_status --head 10     # baştaki 10 kaydı da listele
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from sais_domain import sim_errors, sim_outbox
from sais_domain.models import SimOutboxEntry, SystemSwitch


def _human(seconds):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds} sn"
    if seconds < 3600:
        return f"{seconds // 60} dk"
    return f"{seconds // 3600} sa {(seconds % 3600) // 60} dk"


class Command(BaseCommand):
    help = "SİM gönderim kuyruğu durumunu gösterir (salt okuma)."

    def add_arguments(self, parser):
        parser.add_argument("--cabinet", type=int, default=None,
                            help="Yalnız bu kabin.")
        parser.add_argument("--head", type=int, default=0,
                            help="Her kabin için baştaki N bekleyen kaydı listele.")

    def handle(self, *args, **options):
        switch = SystemSwitch.load()
        window = sim_outbox.accept_window_hours()
        self.stdout.write(
            f"SIM iletimi: {'AÇIK' if switch.sim_enabled else 'KAPALI'}   "
            f"Bakanlık kabul penceresi: {window} saat   "
            f"Son başarılı iletim: {switch.last_sim_success_readtime or '-'}"
        )
        self.stdout.write("")

        rows = sim_outbox.summary_rows() if hasattr(sim_outbox, "summary_rows") \
            else SimOutboxEntry.summary()
        if options["cabinet"]:
            rows = [r for r in rows if r["cabinet_id"] == options["cabinet"]]
        if not rows:
            self.stdout.write(self.style.WARNING("Kuyrukta hiç kayıt yok."))
            return

        for r in rows:
            state = self.style.ERROR("TIKALI") if r["blocked"] else (
                self.style.WARNING("BEKLIYOR") if r["pending"] else
                self.style.SUCCESS("AKIYOR")
            )
            self.stdout.write(
                f"[{state}] {r['station']} / {r['cabinet']} (id={r['cabinet_id']})"
            )
            self.stdout.write(
                f"    bekleyen={r['pending']}  24s iletilen={r['sent_24h']}  "
                f"24s reddedilen={r['failed_24h']}  24s süresi dolan={r['expired_24h']}"
            )
            if r["head_readtime"]:
                kind = r["head_error_kind"]
                self.stdout.write(
                    f"    baştaki kayıt: {timezone.localtime(r['head_readtime']):%d.%m.%Y %H:%M}"
                    f"  tıkanma={_human(r['head_blocked_seconds'])}"
                    + (f"  hata={sim_errors.category_label(kind)}" if kind else "")
                )
                if r["head_message"]:
                    self.stdout.write(f"    mesaj: {r['head_message'][:160]}")
            self.stdout.write("")

            limit = options["head"]
            if limit:
                qs = (
                    SimOutboxEntry.objects
                    .filter(cabinet_id=r["cabinet_id"],
                            status__in=SimOutboxEntry.ACTIVE_STATUSES)
                    .order_by("priority", "readtime")[:limit]
                )
                for e in qs:
                    self.stdout.write(
                        f"      {e.readtime_str}  {e.get_status_display()}  "
                        f"öncelik={e.get_priority_display()}  deneme={e.attempts}  "
                        f"{sim_errors.category_label(e.last_error_kind) if e.last_error_kind else ''}"
                    )
                self.stdout.write("")
