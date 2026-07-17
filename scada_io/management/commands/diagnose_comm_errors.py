"""İletişim (Modbus/ASCII) hatalarını mevcut veriden teşhis eder — SALT OKUMA.

"Bazı tesislerde yoğun iletişim hatası oluyor; bizden mi PLC/ağdan mı?" sorusuna
sahanın **mevcut** verisinden (şema değişikliği olmadan) cevap arar. Sorunlu sahanın
DB'sinde doğrudan çalıştırılır. Hiçbir kayıt yazmaz/değiştirmez.

Her bağlantı için üç sinyal üretir:

1. **Bağlantı sağlığı** — last_polled/connected/error zamanları + son ham hata metni
   (`classify_comm_error` ile kategorize).
2. **Hata oranı** — pencere içindeki `Reading` kayıtlarını `status.code`'a göre sayar
   (1=iyi, 8=İletişim, 4=Geçersiz Veri, 0=Veri Yok); bağlantı ve sensör kırılımı.
3. **Bağlantı-seviyesi vs sensör-seviyesi** (en keskin us-vs-PLC sinyali) — tüm sensörler
   birlikte mi bad (-> PLC/ağ) yoksa belirli sensörler mi hep bad (-> bizim adres/slave
   config'imiz).

Ayrıca (varsa) `CommErrorEvent` tablosundan gerçek kategori dağılımını ve bad okumaların
saatlik dağılımını (sabit mi / patlamalı mı) raporlar.

Kullanım:
    python manage.py diagnose_comm_errors                 # tüm bağlantılar, son 7 gün
    python manage.py diagnose_comm_errors --days 3
    python manage.py diagnose_comm_errors --station 2
    python manage.py diagnose_comm_errors --connection 5
    python manage.py diagnose_comm_errors --csv /tmp/sensor_breakdown.csv
"""
from __future__ import annotations

import csv
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import TruncHour
from django.utils import timezone

from api.models import Connection, Reading
from scada_io.comm_errors import category_label, category_source, classify_comm_error

# İletişim/veri HATASI sayılan status kodları. Operasyonel kodlar (yıkama/bakım
# 23-26, aralık-dışı 39 vb.) iletişim hatası DEĞİLDİR → hata oranına katılmaz
# (aksi halde çok yıkanan bir istasyon yanlışlıkla "hatalı" görünür). None status
# (kod yok) da hata sayılır (belirsiz).
ERROR_CODES = {0, 4, 8}
# status.code -> kısa etiket (bilinen SAIS kodları; kalanı "kod N" gösterilir).
CODE_LABELS = {1: "iyi", 8: "İletişim", 4: "Geçersiz", 0: "Veri Yok", 39: "Aralık Dışı"}
# Bir sensörün "bad" sayılması için bad-oran eşiği (bağlantı-vs-sensör kararında).
BAD_SENSOR_THRESHOLD = 0.5


def _is_error_code(code) -> bool:
    return code is None or code in ERROR_CODES


class Command(BaseCommand):
    help = "İletişim hatalarını mevcut Reading/Connection verisinden teşhis eder (salt okuma)."

    def add_arguments(self, parser):
        parser.add_argument("--station", type=int, default=None, help="Sadece bu istasyon.")
        parser.add_argument("--connection", type=int, default=None, help="Sadece bu bağlantı.")
        parser.add_argument("--days", type=int, default=7, help="Analiz penceresi (gün, default 7).")
        parser.add_argument("--top", type=int, default=10,
                            help="En hatalı kaç sensör listelensin (default 10).")
        parser.add_argument("--csv", default=None,
                            help="Sensör kırılımını CSV'ye yaz (opsiyonel).")

    def handle(self, *args, **options):
        days = max(1, options["days"])
        cutoff = timezone.now() - timedelta(days=days)
        top = options["top"]

        conns = Connection.objects.all().select_related("station")
        if options["station"]:
            conns = conns.filter(station_id=options["station"])
        if options["connection"]:
            conns = conns.filter(pk=options["connection"])
        conns = conns.order_by("station__id", "name")

        if not conns:
            self.stdout.write(self.style.WARNING("Eşleşen bağlantı yok."))
            return

        self.stdout.write(self.style.HTTP_INFO(
            f"\n=== İletişim Teşhisi — son {days} gün (cutoff {cutoff:%Y-%m-%d %H:%M}) ===\n"
        ))

        csv_rows: list[dict] = []
        for conn in conns:
            csv_rows += self._analyze_connection(conn, cutoff, top)

        if options["csv"] and csv_rows:
            self._write_csv(options["csv"], csv_rows)
            self.stdout.write(self.style.SUCCESS(
                f"\nSensör kırılımı yazıldı: {options['csv']} ({len(csv_rows)} satır)"
            ))

    # ------------------------------------------------------------------ #

    def _analyze_connection(self, conn, cutoff, top) -> list[dict]:
        now = timezone.now()
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(
            f"\n[{conn.pk}] {conn.station or '—'} / {conn.name}  "
            f"({conn.protocol}, {conn.host or conn.serial_port or '?'}:{conn.port or ''})"
        ))

        # --- 1) Bağlantı sağlığı ---
        polled_age = self._age(conn.last_polled_at, now)
        conn_age = self._age(conn.last_connected_at, now)
        w(f"  Son poll: {polled_age}   Son başarılı bağlantı: {conn_age}   "
          f"poll_interval={conn.poll_interval_sec}s  timeout={conn.timeout_ms}ms")
        if conn.last_error_message:
            cat = classify_comm_error(conn.last_error_message)
            w(f"  Son hata ({self._age(conn.last_error_at, now)}): "
              f"{conn.last_error_message[:120]}")
            w(f"    -> kategori: {category_label(cat)}  [kaynak: {category_source(cat)}]")

        # --- 2) Reading status kırılımı (pencere içinde) ---
        rows = list(
            Reading.objects
            .filter(sensor__connection=conn, time_iso__gte=cutoff)
            .values("sensor_id", "status__code")
            .annotate(n=Count("id"))
        )
        total = sum(r["n"] for r in rows)
        if total == 0:
            w(self.style.WARNING("  Bu pencerede Reading kaydı yok (polling durmuş olabilir)."))
            return []

        # status kodu dağılımı (bağlantı geneli) — hata yalnız ERROR_CODES/None.
        by_code: dict[int | None, int] = {}
        for r in rows:
            by_code[r["status__code"]] = by_code.get(r["status__code"], 0) + r["n"]
        bad = sum(n for code, n in by_code.items() if _is_error_code(code))
        err_rate = bad / total
        w(f"  Toplam okuma: {total:,}   İletişim/veri hata oranı: {self._pct(err_rate)}"
          f"{self._rate_flag(err_rate)}")
        w("    " + "  ".join(
            f"{self._code_label(code)}={n:,} ({self._pct(n / total)})"
            for code, n in sorted(by_code.items(), key=lambda kv: -kv[1])
        ))

        # --- Sensör bazlı bad-oran (yalnız hata kodları) ---
        per_sensor: dict[int, dict] = {}
        for r in rows:
            s = per_sensor.setdefault(r["sensor_id"], {"total": 0, "err": 0})
            s["total"] += r["n"]
            if _is_error_code(r["status__code"]):
                s["err"] += r["n"]
        sensor_labels = self._sensor_labels(per_sensor.keys())
        sensor_stats = []
        for sid, s in per_sensor.items():
            bad_rate = (s["err"] / s["total"]) if s["total"] else 0
            sensor_stats.append((sid, s["total"], bad_rate))
        sensor_stats.sort(key=lambda t: -t[2])

        # --- 3) Bağlantı-seviyesi vs sensör-seviyesi karar ---
        n_sensors = len(sensor_stats)
        bad_sensors = [t for t in sensor_stats if t[2] >= BAD_SENSOR_THRESHOLD]
        clean_sensors = [t for t in sensor_stats if t[2] < 0.05]
        w("  " + self._verdict(err_rate, n_sensors, bad_sensors, clean_sensors, sensor_labels))

        # En hatalı sensörler
        if bad_sensors:
            w(f"  En hatalı sensörler (bad-oran >= %{int(BAD_SENSOR_THRESHOLD*100)}):")
            for sid, tot, br in sensor_stats[:top]:
                if br < BAD_SENSOR_THRESHOLD:
                    break
                w(f"    - {sensor_labels.get(sid, sid)}: bad {self._pct(br)}  ({tot:,} okuma)")

        # --- Ekstra: CommErrorEvent kategori dağılımı (varsa) ---
        self._category_breakdown_from_events(conn, cutoff)

        # --- Ekstra: bad okumaların saatlik dağılımı (sabit mi / patlamalı mı) ---
        self._time_distribution(conn, cutoff)

        return [
            {
                "connection_id": conn.pk,
                "connection": conn.name,
                "station": str(conn.station or ""),
                "sensor": sensor_labels.get(sid, sid),
                "readings": tot,
                "bad_rate": round(br, 4),
            }
            for sid, tot, br in sensor_stats
        ]

    # ------------------------------------------------------------------ #

    def _category_breakdown_from_events(self, conn, cutoff):
        """CommErrorEvent tablosu varsa gerçek kategori dağılımını göster."""
        try:
            from api.models import CommErrorEvent
        except ImportError:
            return
        try:
            rows = list(
                CommErrorEvent.objects
                .filter(connection=conn, time_iso__gte=cutoff)
                .values("category")
                .annotate(n=Count("id"))
                .order_by("-n")
            )
        except Exception:  # noqa: BLE001 — tablo henüz migrate edilmemiş olabilir
            return
        if not rows:
            return
        total = sum(r["n"] for r in rows)
        self.stdout.write(f"  Hata kategorileri ({total:,} olay, CommErrorEvent):")
        for r in rows:
            cat = r["category"]
            self.stdout.write(
                f"    - {category_label(cat)} [{category_source(cat)}]: "
                f"{r['n']:,} ({self._pct(r['n']/total)})"
            )

    def _time_distribution(self, conn, cutoff):
        """Bad okumaların saatlik dağılımı -> sabit hata mı, patlamalı mı?"""
        hourly = list(
            Reading.objects
            .filter(sensor__connection=conn, time_iso__gte=cutoff,
                    status__code__in=ERROR_CODES)
            .annotate(h=TruncHour("time_iso"))
            .values("h").annotate(n=Count("id")).order_by("h")
        )
        if not hourly:
            return
        counts = [r["n"] for r in hourly]
        active_hours = len(counts)
        avg = sum(counts) / active_hours
        peak = max(counts)
        pattern = "patlamalı/dalgalı" if peak > avg * 3 else "sabit/yaygın"
        self.stdout.write(
            f"  Bad okuma zaman dağılımı: {active_hours} saatte hata var, "
            f"ort {avg:.0f}/saat, tepe {peak}/saat -> {pattern}"
        )

    # ---- yardımcılar ----

    def _verdict(self, err_rate, n_sensors, bad_sensors, clean_sensors, labels) -> str:
        if err_rate < 0.02:
            return self.style.SUCCESS("Değerlendirme: SAĞLIKLI (hata oranı ihmal edilebilir).")
        frac_bad = len(bad_sensors) / n_sensors if n_sensors else 0
        if frac_bad >= 0.8:
            return self.style.ERROR(
                "Değerlendirme: BAĞLANTI SEVİYESİ — sensörlerin çoğu birlikte hatalı. "
                "Şüphe: PLC/ağ (erişilemezlik, reset, timeout, eşzamanlı socket)."
            )
        if bad_sensors and clean_sensors:
            names = ", ".join(str(labels.get(t[0], t[0])) for t in bad_sensors[:5])
            return self.style.WARNING(
                "Değerlendirme: SENSÖR SEVİYESİ — bazı sensörler sürekli hatalı, "
                f"diğerleri sağlıklı. Şüphe: BİZİM config (adres/slave/quantity). "
                f"Hatalı: {names}"
            )
        return self.style.WARNING(
            "Değerlendirme: KARIŞIK — hem bağlantı hem sensör kaynaklı olabilir; "
            "kategori dağılımına ve son hata metnine bak."
        )

    @staticmethod
    def _age(dt, now) -> str:
        if not dt:
            return "hiç"
        secs = (now - dt).total_seconds()
        if secs < 90:
            return f"{int(secs)}sn önce"
        if secs < 5400:
            return f"{int(secs/60)}dk önce"
        if secs < 172800:
            return f"{secs/3600:.1f}sa önce"
        return f"{secs/86400:.1f}g önce"

    @staticmethod
    def _pct(x: float) -> str:
        return f"%{x*100:.1f}"

    @staticmethod
    def _rate_flag(err_rate) -> str:
        if err_rate >= 0.25:
            return "  [!!] COK YUKSEK"
        if err_rate >= 0.05:
            return "  [!] yuksek"
        return ""

    @staticmethod
    def _code_label(code) -> str:
        if code is None:
            return "status-yok"
        return CODE_LABELS.get(code, f"kod{code}")

    @staticmethod
    def _sensor_labels(sensor_ids) -> dict:
        from api.models import Sensor
        labels = {}
        for s in Sensor.objects.filter(pk__in=list(sensor_ids)).select_related("parameter"):
            code = s.parameter.parameter_name if s.parameter_id else ""
            tag = s.tag or ""
            base = tag or code or f"#{s.pk}"
            extra = f" (slave {s.slave_id}, adr {s.address}, fn {s.function})"
            labels[s.pk] = f"{base}{extra}"
        return labels

    @staticmethod
    def _write_csv(path, rows):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
