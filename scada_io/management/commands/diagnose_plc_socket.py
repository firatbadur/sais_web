"""Bir TCP bağlantısı için "kaç oturum açıyoruz / cihaz hangi deseni kaldırıyor"
sorusunu ölçerek cevaplar (salt okuma).

Saha vakası (Van Erciş, Mikrodev PLC): Modbus Poll tek soketle kesintisiz
okurken bizim yazılım sürekli `Connection reset by peer` alıyor. İki ayrı
senaryo aynı hata metnini üretir ve **çözümleri birbirinin tersidir**:

  a) Cihaz eşzamanlı oturum sayısını sınırlıyor  → tek soket bırakmalıyız
     (bağlantıda "Tek Oturumlu Cihaz" açık: tur bitince soket kapatılır).
  b) Cihaz kapatılan oturumu geç serbest bırakıyor → açıp kapamak oturum
     tablosunu doldurur; tek KALICI soket bırakıp hiç kapatmamak gerekir
     (polling'i tek process'e almak — ayrı kuyruk + --concurrency=1).

Komut ikisini ayırır:

  1. Bağlantı ayarları + scan group/sensör ölçeği (tur başına kaç istek,
     en kötü tur süresi okuma periyodunu aşıyor mu).
  2. Cihaza **gerçekten** kaç TCP oturumu açık (`/proc/net/tcp`; worker'lara
     `scada_pool_stats` control command'ı ile broadcast edilir — pool worker
     process'inin belleğinde yaşadığı için dışarıdan okunamaz).
  3. İki soak testi: `persistent` (tek kalıcı soket — Modbus Poll davranışı)
     ve `reconnect` (her turda aç/kapa — "Tek Oturumlu Cihaz" davranışı).
  4. Sonuçlara göre hangi modun uygun olduğu.

Soak testi cihaza BİR oturum daha açar; temiz ölçüm için önce dashboard →
Sistem Kontrol → "Sensör Okuması" kapatılmalı (komut kontrol edip uyarır).
Hiçbir Reading yazılmaz, hiçbir ayar değiştirilmez.

Kullanım:
    python manage.py diagnose_plc_socket --connection 2
    python manage.py diagnose_plc_socket --connection 2 --seconds 120 --mode persistent
    python manage.py diagnose_plc_socket --connection 2 --skip-soak
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from api.models import Connection


class Command(BaseCommand):
    help = "TCP bağlantısı için oturum/soket teşhisi (salt okuma, soak testli)."

    def add_arguments(self, parser):
        parser.add_argument("--connection", type=int, required=True,
                            help="Connection ID (Bağlantılar sayfasında görünür).")
        parser.add_argument("--seconds", type=int, default=60,
                            help="Her soak testinin süresi (varsayılan 60).")
        parser.add_argument("--interval", type=float, default=0,
                            help="Turlar arası bekleme; 0 = bağlantının "
                                 "poll_interval_sec değeri.")
        parser.add_argument("--mode", choices=("both", "persistent", "reconnect"),
                            default="both", help="Hangi soak testi çalışsın.")
        parser.add_argument("--skip-soak", action="store_true",
                            help="Yalnız ayar + açık soket özetini yaz.")

    def handle(self, *args, **opts):
        try:
            conn = Connection.objects.get(pk=opts["connection"])
        except Connection.DoesNotExist:
            raise CommandError(f"Connection {opts['connection']} bulunamadı.")

        if conn.transport != "tcp":
            raise CommandError(
                f"Bu komut TCP bağlantılar içindir ({conn} transport={conn.transport})."
            )

        self._report_config(conn)
        self._report_sockets(conn)

        if opts["skip_soak"]:
            return

        self._warn_if_polling_on()

        seconds = max(10, opts["seconds"])
        interval = opts["interval"] or (conn.poll_interval_sec or 10)
        results = {}
        if opts["mode"] in ("both", "persistent"):
            results["persistent"] = self._soak(conn, seconds, interval, persistent=True)
        if opts["mode"] in ("both", "reconnect"):
            results["reconnect"] = self._soak(conn, seconds, interval, persistent=False)

        self._verdict(results)

    # ------------------------------------------------------------------ #
    def _h(self, text):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(text))

    # -- 1) ayarlar ----------------------------------------------------- #
    def _report_config(self, conn):
        from api.models import Sensor

        self._h(f"1) Bağlantı ayarları — {conn}")
        groups = list(conn.scan_groups.filter(is_active=True))
        sensors = Sensor.objects.filter(connection=conn, is_active=True)
        legacy = sensors.filter(scan_group__isnull=True).count()
        requests = len(groups) + legacy
        blocking = conn.timeout_ms * (1 + (conn.retry_count or 0)) / 1000

        rows = [
            ("Hedef", f"{conn.host}:{conn.port or 502} ({conn.protocol})"),
            ("Okuma periyodu", f"{conn.poll_interval_sec} sn"),
            ("Timeout / retry", f"{conn.timeout_ms} ms / {conn.retry_count} "
                                f"→ cevapsız okuma ~{blocking:.1f} sn bloklar"),
            ("Tek Oturumlu Cihaz", "AÇIK" if conn.single_session else "kapalı"),
            ("Aktif scan group", f"{len(groups)}"),
            ("Aktif sensör", f"{sensors.count()} (scan group'suz: {legacy})"),
            ("Tur başına Modbus isteği", f"{requests}"),
        ]
        for label, value in rows:
            self.stdout.write(f"   {label:<26}: {value}")

        if requests and conn.poll_interval_sec:
            worst = requests * blocking
            if worst > conn.poll_interval_sec:
                self.stdout.write(self.style.WARNING(
                    f"   ! En kötü durumda tur ~{worst:.0f} sn sürer ama okuma "
                    f"periyodu {conn.poll_interval_sec} sn — turlar üst üste biner. "
                    f"retry/timeout'u düşür veya periyodu büyüt."
                ))
        if legacy:
            self.stdout.write(self.style.WARNING(
                f"   ! {legacy} sensör scan group'suz (her biri AYRI istek). "
                f"Scan group'a almak tur süresini ciddi düşürür."
            ))

    # -- 2) açık soketler ----------------------------------------------- #
    def _report_sockets(self, conn):
        from scada_io import netdiag

        self._h("2) Cihaza açık TCP oturumları")
        host, port = conn.host, conn.port or 502

        local = netdiag.count_sockets(host, port)
        if local is None:
            self.stdout.write("   bu container: ölçülemedi (/proc yok — Windows dev?)")
        else:
            self.stdout.write(f"   bu container ({host}:{port}): {local or 'yok'}")

        try:
            from sais_web.celery import app
            replies = app.control.broadcast(
                "scada_pool_stats", arguments={"host": host, "port": port},
                reply=True, timeout=5,
            ) or []
        except Exception as exc:  # noqa: BLE001 — broker yoksa teşhis sürsün
            self.stdout.write(self.style.WARNING(f"   worker'lara ulaşılamadı: {exc}"))
            return

        if not replies:
            self.stdout.write(self.style.WARNING(
                "   hiçbir worker yanıt vermedi (worker kapalı mı?)"
            ))
            return

        for reply in replies:
            for node, payload in reply.items():
                if not isinstance(payload, dict):
                    continue
                socks = payload.get("sockets") or {}
                pool = payload.get("pool") or {}
                self.stdout.write(f"   {node}")
                self.stdout.write(f"      cihaza açık soket : {socks or 'yok/ölçülemedi'}")
                self.stdout.write(f"      pool'daki bağlantı: {pool.get('open_count')}")
                for cid, info in (pool.get("connections") or {}).items():
                    self.stdout.write(
                        f"         conn={cid} {info.get('host')} "
                        f"connected={info.get('connected')} "
                        f"idle={info.get('idle_sec')}sn fail={info.get('failures')}"
                    )
                established = socks.get("ESTABLISHED", 0)
                if established > 1:
                    self.stdout.write(self.style.ERROR(
                        f"      ! Aynı cihaza {established} açık oturum var — küçük "
                        f"PLC'ler bunu kaldırmaz ('Tek Oturumlu Cihaz' açık mı?)."
                    ))

    def _warn_if_polling_on(self):
        try:
            from sais_domain.models import SystemSwitch
            if SystemSwitch.load().polling_enabled:
                self.stdout.write(self.style.WARNING(
                    "\n   ! Sensör Okuması AÇIK. Soak testi cihaza BİR oturum daha "
                    "açar; temiz ölçüm için Sistem Kontrol'den kapatıp tekrar "
                    "çalıştırmak daha doğru sonuç verir."
                ))
        except Exception:  # noqa: BLE001
            pass

    # -- 3) soak --------------------------------------------------------- #
    def _soak(self, conn, seconds, interval, *, persistent):
        from scada_io.readers import build_reader

        mode = ("persistent (tek kalıcı soket)" if persistent
                else "reconnect (her turda aç/kapa)")
        self._h(f"3) Soak testi — {mode}; {seconds} sn, {interval} sn aralık")

        groups = list(conn.scan_groups.filter(is_active=True))
        if not groups:
            self.stdout.write(self.style.WARNING(
                "   Aktif scan group yok — soak testi atlandı."
            ))
            return None

        stat = {"rounds": 0, "ok": 0, "fail": 0, "first_error": "",
                "opens": 0, "open_fail": 0}
        reader = None
        deadline = time.monotonic() + seconds

        try:
            while time.monotonic() < deadline:
                stat["rounds"] += 1
                if reader is None:
                    reader = build_reader(conn)
                    stat["opens"] += 1
                    if not reader.open():
                        stat["open_fail"] += 1
                        stat["fail"] += 1
                        err = f"open: {reader.last_error}"
                        stat["first_error"] = stat["first_error"] or err
                        self.stdout.write(
                            f"   tur {stat['rounds']:>3}: AÇILAMADI — {err[:90]}")
                        reader = None
                        time.sleep(interval)
                        continue

                round_ok, err = True, ""
                for sg in groups:
                    _regs, err = reader.read_raw(
                        slave_id=sg.slave_id, function=sg.function,
                        address=sg.start_address, count=sg.quantity,
                    )
                    if err:
                        round_ok = False
                        err = f"{sg.name}: {err}"
                        stat["first_error"] = stat["first_error"] or err
                        break

                stat["ok" if round_ok else "fail"] += 1
                self.stdout.write(
                    f"   tur {stat['rounds']:>3}: "
                    + ("OK" if round_ok else f"HATA — {err[:90]}")
                )

                # Kalıcı modda soketi yalnız hata sonrası tazele; reconnect
                # modunda her turda kapat (test edilen desen bu).
                if not persistent or not round_ok:
                    try:
                        reader.close()
                    except Exception:  # noqa: BLE001
                        pass
                    reader = None

                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("   (kullanıcı durdurdu)")
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:  # noqa: BLE001
                    pass

        total = stat["ok"] + stat["fail"]
        rate = (stat["fail"] / total * 100) if total else 0
        self.stdout.write(
            f"   → {stat['ok']}/{total} tur başarılı, hata oranı %{rate:.1f}, "
            f"{stat['opens']} kez bağlantı açıldı ({stat['open_fail']} açılamadı)"
        )
        return stat

    # -- 4) karar -------------------------------------------------------- #
    def _verdict(self, results):
        self._h("4) Değerlendirme")

        def rate(key):
            s = results.get(key)
            if not s:
                return None
            total = s["ok"] + s["fail"]
            return (s["fail"] / total) if total else None

        persistent, reconnect = rate("persistent"), rate("reconnect")

        if persistent is not None and persistent <= 0.02:
            tail = (
                " Reconnect testi de temiz → 'Tek Oturumlu Cihaz' AÇIK tutulabilir."
                if reconnect is not None and reconnect <= 0.02 else
                " Reconnect testi HATALI → cihaz kapatılan oturumu geç serbest "
                "bırakıyor; 'Tek Oturumlu Cihaz' KAPALI olmalı ve polling tek "
                "process'e alınmalı (ayrı kuyruk + --concurrency=1)."
                if reconnect is not None else ""
            )
            self.stdout.write(self.style.SUCCESS(
                "   Tek kalıcı soketle temiz çalışıyor (Modbus Poll gibi).\n"
                "   → Cihaz eşzamanlı oturumdan rahatsız; sorun bizim birden çok "
                "soket açmamız." + tail
            ))
        elif persistent is not None:
            self.stdout.write(self.style.ERROR(
                f"   Tek soketle bile hata var (%{persistent * 100:.0f}).\n"
                "   → Sorun eşzamanlı oturum DEĞİL. Sıradaki şüpheliler: ağ yolu "
                "(WSL2/NAT, switch, kablo), cihazın TCP yığını, yanlış "
                "slave_id/quantity, cihaza bağlı başka bir istemci.\n"
                "   → Aynı anda aynı PC'den Modbus Poll çalıştırıp karşılaştır: "
                "o temizse fark ağda değil, istemci deseninde."
            ))

        if reconnect is not None and persistent is None:
            self.stdout.write(f"   reconnect hata oranı: %{reconnect * 100:.1f}")
