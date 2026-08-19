"""SİM gönderim kuyruğunu senkron (worker'sız) boşaltır — debug/saha müdahalesi.

Celery worker'ı beklemeden kuyruğun neden ilerlemediğini görmek için kullanılır;
`resend_missing_data_once` / `run_scenarios_once` ile aynı desen.

Kullanım:
    python manage.py drain_sim_outbox_once                  # tüm aktif kabinler
    python manage.py drain_sim_outbox_once --cabinet 1
    python manage.py drain_sim_outbox_once --max 5          # en fazla 5 kayıt
    python manage.py drain_sim_outbox_once --no-lock        # takılı kilidi yoksay
"""
from django.core.management.base import BaseCommand

from sais_domain import sim_outbox
from sais_domain.models import SaisCabinet, SystemSwitch


class Command(BaseCommand):
    help = "SİM gönderim kuyruğunu senkron boşaltır (debug)."

    def add_arguments(self, parser):
        parser.add_argument("--cabinet", type=int, default=None,
                            help="Yalnız bu kabin (verilmezse tüm aktif kabinler).")
        parser.add_argument("--max", type=int, default=None,
                            help="Bu turda gönderilecek en fazla kayıt sayısı.")
        parser.add_argument("--budget", type=int, default=None,
                            help="Duvar-saati bütçesi (sn).")
        parser.add_argument("--no-lock", action="store_true",
                            help="Drenaj kilidini atla (takılı kilit varsa).")

    def handle(self, *args, **options):
        switch = SystemSwitch.load()
        if not switch.sim_enabled:
            self.stdout.write(self.style.WARNING(
                "UYARI: SystemSwitch.sim_enabled KAPALI — normal akışta drenaj "
                "çalışmaz. Bu komut yine de deneyecek."
            ))

        reaped = sim_outbox.reap_stuck_entries()
        expired = sim_outbox.expire_old_entries()
        if reaped:
            self.stdout.write(f"Takılı kayıt kurtarıldı: {reaped}")
        if expired:
            self.stdout.write(f"Kabul penceresini aşan kayıt düşürüldü: {expired}")

        cabinets = SaisCabinet.objects.select_related("station").filter(
            station__active=True
        )
        if options["cabinet"]:
            cabinets = cabinets.filter(pk=options["cabinet"])
        cabinets = list(cabinets)
        if not cabinets:
            self.stderr.write(self.style.ERROR("Aktif kabin bulunamadı."))
            return

        for cabinet in cabinets:
            result = sim_outbox.drain_cabinet(
                cabinet,
                budget_sec=options["budget"],
                max_entries=options["max"],
                use_lock=not options["no_lock"],
            )
            style = self.style.ERROR if result.get("blocked") else self.style.SUCCESS
            self.stdout.write(style(
                f"kabin={cabinet.id} ({cabinet.device_id}) → {result}"
            ))
