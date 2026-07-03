"""Rapor Stüdyosu üretim motoru — blok tabanlı şablonlardan HTML/PDF/Excel üretir.

Akış (backup_database deseni):
    generate_report(template_id) →
        GeneratedReport(status="running") →
        blokları çöz (query_binding — Reading/aggregate sorguları) →
        render_report_html → (WeasyPrint varsa) PDF; render_xlsx → Excel →
        dosyaları settings.REPORTS_DIR'e yaz →
        zamanlamada e-posta açıksa send_email(kind="report", attachments=[...]) →
        status=success/failed + boyut + finished_at.

Göreli zaman pencereleri (`window: {"mode":"relative","key":...}`) üretim
anındaki `reference_time`'a göre çözülür — zamanlanmış raporlar her
çalıştırmada güncel dönemi kapsar.

WeasyPrint Windows dev ortamında GTK/pango DLL'leri olmadan import'ta OSError
atar; bu yüzden geniş try/except ile sarılır. PDF_AVAILABLE=False iken PDF
üretimi atlanır (Excel/önizleme/e-posta çalışmaya devam eder).
"""
from __future__ import annotations

import base64
import datetime
import io
import logging
import os
import re

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Opsiyonel bağımlılıklar — zarif bozulma
# --------------------------------------------------------------------------- #

try:
    from weasyprint import HTML as _WeasyHTML

    PDF_AVAILABLE = True
    PDF_UNAVAILABLE_REASON = ""
except Exception as _exc:  # noqa: BLE001 — Windows'ta OSError (GTK eksik) da gelebilir
    _WeasyHTML = None
    PDF_AVAILABLE = False
    PDF_UNAVAILABLE_REASON = str(_exc)

try:
    import matplotlib

    matplotlib.use("Agg")  # GUI'siz backend — Celery worker/thread güvenli
    import matplotlib.pyplot as plt

    CHARTS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    plt = None
    CHARTS_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Zaman penceresi çözümü
# --------------------------------------------------------------------------- #

# UI'da gösterilen göreli pencere seçenekleri (anahtar → etiket).
WINDOW_KEYS = {
    "last_24h": "Son 24 Saat",
    "yesterday": "Dün",
    "last_7d": "Son 7 Gün",
    "last_week": "Geçen Hafta",
    "this_month": "Bu Ay",
    "last_month": "Geçen Ay",
    "last_30d": "Son 30 Gün",
}

DATE_FMT = "%d.%m.%Y %H:%M"


def resolve_window(window: dict | None, now: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """Blok `window` tanımını (start, end) TZ-aware çiftine çözer.

    Göreli anahtarlar yerel takvime göre hesaplanır (dün = yerel 00:00-24:00).
    Tanımsız/boş pencere son 24 saate düşer.
    """
    window = window or {"mode": "relative", "key": "last_24h"}
    tz = timezone.get_current_timezone()
    local_now = timezone.localtime(now)

    def _aware(d: datetime.datetime) -> datetime.datetime:
        return timezone.make_aware(d, tz) if timezone.is_naive(d) else d

    if window.get("mode") == "fixed":
        start_raw, end_raw = window.get("start"), window.get("end")
        try:
            start = _aware(datetime.datetime.fromisoformat(start_raw))
            end = _aware(datetime.datetime.fromisoformat(end_raw))
            if start < end:
                return start, end
        except (TypeError, ValueError):
            pass
        # bozuk sabit tarih → son 24 saat fallback
        return now - datetime.timedelta(hours=24), now

    key = window.get("key") or "last_24h"
    today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    if key == "yesterday":
        return today - datetime.timedelta(days=1), today
    if key == "last_7d":
        return now - datetime.timedelta(days=7), now
    if key == "last_week":
        # Geçen hafta: geçen Pazartesi 00:00 → bu Pazartesi 00:00
        this_monday = today - datetime.timedelta(days=local_now.weekday())
        return this_monday - datetime.timedelta(days=7), this_monday
    if key == "this_month":
        return today.replace(day=1), now
    if key == "last_month":
        first_this = today.replace(day=1)
        last_month_end = first_this
        last_month_start = (first_this - datetime.timedelta(days=1)).replace(day=1)
        return last_month_start, last_month_end
    if key == "last_30d":
        return now - datetime.timedelta(days=30), now
    # default: last_24h
    return now - datetime.timedelta(hours=24), now


def window_label(window: dict | None, now: datetime.datetime) -> str:
    """Pencerenin insan-okur etiketi (rapor alt başlıklarında kullanılır)."""
    window = window or {}
    if window.get("mode") == "fixed":
        start, end = resolve_window(window, now)
        return f"{timezone.localtime(start):{DATE_FMT}} — {timezone.localtime(end):{DATE_FMT}}"
    return WINDOW_KEYS.get(window.get("key") or "last_24h", "Son 24 Saat")


# --------------------------------------------------------------------------- #
# Veri bağlama sorguları
# --------------------------------------------------------------------------- #

# Blok başına satır limiti — kullanıcı değeri bunu aşamaz.
REPORT_MAX_ROWS = 5000
DEFAULT_MAX_ROWS = 500

BUCKET_LABELS = {
    "raw": "Ham Veri",
    "15min": "15 Dakikalık",
    "hourly": "Saatlik",
    "daily": "Günlük",
}


def _bucket_model(bucket: str):
    from api.models import Reading, ReadingDaily, ReadingFifteenMin, ReadingHourly

    return {
        "raw": Reading,
        "15min": ReadingFifteenMin,
        "hourly": ReadingHourly,
        "daily": ReadingDaily,
    }.get(bucket or "hourly", ReadingHourly)


def _resolve_sensors(station_id: int, parameter_ids: list[int]):
    """İstasyon + parametre listesi → {parameter_id: sensor} eşlemesi.

    Kapsam kuralı: parametreler istasyona SENSÖRLER üzerinden bağlanır
    (`Parameter.station` FK'sına güvenilmez). Parametre başına ilk aktif sensör.
    """
    from api.models import Sensor

    sensors = (
        Sensor.objects.filter(
            connection__station_id=station_id,
            parameter_id__in=parameter_ids,
            is_active=True,
        )
        .select_related("parameter")
        .order_by("parameter_id", "id")
    )
    mapping = {}
    for s in sensors:
        mapping.setdefault(s.parameter_id, s)
    return mapping


def query_binding(binding: dict, now: datetime.datetime) -> dict:
    """Bir veri bloğunun bağlamasını çözer ve seriyi döndürür.

    Dönüş:
        {
          "station_name": str, "bucket": str, "window_label": str,
          "start": dt, "end": dt,
          "series": [{"parameter_id", "label", "unit", "rows": [...]}, ...],
        }
    raw satır:      (ts_local, value, quality, status_name)
    aggregate satır: (ts_local, avg, min, max, count, bad_count)
    """
    from api.models import Station

    station_id = binding.get("station_id")
    parameter_ids = [int(p) for p in (binding.get("parameter_ids") or []) if p]
    bucket = binding.get("bucket") or "hourly"
    start, end = resolve_window(binding.get("window"), now)
    max_rows = min(int(binding.get("max_rows") or DEFAULT_MAX_ROWS), REPORT_MAX_ROWS)

    station = Station.objects.filter(pk=station_id).first()
    result = {
        "station_name": station.name if station else "—",
        "bucket": bucket,
        "bucket_label": BUCKET_LABELS.get(bucket, bucket),
        "window_label": window_label(binding.get("window"), now),
        "start": start,
        "end": end,
        "series": [],
    }
    if not station_id or not parameter_ids:
        return result

    sensor_map = _resolve_sensors(station_id, parameter_ids)
    model = _bucket_model(bucket)

    for pid in parameter_ids:
        sensor = sensor_map.get(pid)
        if not sensor:
            continue
        param = sensor.parameter
        entry = {
            "parameter_id": pid,
            "label": param.display_name if param else f"Parametre {pid}",
            "unit": (param.unit_txt or param.unit or "") if param else "",
            "rows": [],
        }
        if bucket == "raw":
            qs = (
                model.objects.filter(sensor=sensor, time_iso__gte=start, time_iso__lte=end)
                .select_related("status")
                .order_by("time_iso")[:max_rows]
            )
            entry["rows"] = [
                (
                    timezone.localtime(r.time_iso),
                    r.value,
                    r.quality,
                    r.status.name if r.status_id else "",
                )
                for r in qs
                if r.time_iso
            ]
        else:
            qs = (
                model.objects.filter(sensor=sensor, bucket_start__gte=start, bucket_start__lt=end)
                .order_by("bucket_start")[:max_rows]
            )
            entry["rows"] = [
                (
                    timezone.localtime(r.bucket_start),
                    r.avg_value,
                    r.min_value,
                    r.max_value,
                    r.count,
                    r.bad_count,
                )
                for r in qs
            ]
        result["series"].append(entry)

    return result


def _kpi_value(card: dict, now: datetime.datetime) -> dict:
    """Tek KPI kartını çözer → {label, value_str, unit, sub}."""
    from django.db.models import Avg, Max, Min

    from api.models import Parameter, Reading, SensorLatest

    station_id = card.get("station_id")
    pid = card.get("parameter_id")
    agg = card.get("agg") or "last"

    param = Parameter.objects.filter(pk=pid).first()
    label = card.get("label") or (param.display_name if param else f"Parametre {pid}")
    unit = (param.unit_txt or param.unit or "") if param else ""
    out = {"label": label, "unit": unit, "value_str": "—", "sub": ""}

    sensor_map = _resolve_sensors(station_id, [pid]) if (station_id and pid) else {}
    sensor = sensor_map.get(pid)
    if not sensor:
        out["sub"] = "sensör bulunamadı"
        return out

    def _fmt(v):
        if v is None:
            return "—"
        decimals = sensor.decimals if sensor.decimals is not None else 2
        return f"{v:.{max(int(decimals), 0)}f}"

    if agg == "last":
        latest = SensorLatest.objects.filter(sensor=sensor).first()
        out["value_str"] = _fmt(latest.value if latest else None)
        out["sub"] = "anlık değer"
        return out

    start, end = resolve_window(card.get("window"), now)
    stats = Reading.objects.filter(
        sensor=sensor, time_iso__gte=start, time_iso__lte=end,
    ).exclude(quality="bad").aggregate(
        v_avg=Avg("value"), v_min=Min("value"), v_max=Max("value"),
    )
    agg_map = {"avg": ("v_avg", "ortalama"), "min": ("v_min", "minimum"), "max": ("v_max", "maksimum")}
    key, sub = agg_map.get(agg, ("v_avg", "ortalama"))
    out["value_str"] = _fmt(stats.get(key))
    out["sub"] = f"{sub} · {window_label(card.get('window'), now)}"
    return out


# --------------------------------------------------------------------------- #
# Grafik (matplotlib → PNG data URI)
# --------------------------------------------------------------------------- #

# Metronic paletiyle hizalı seri renkleri
_CHART_COLORS = [
    "#009ef7", "#50cd89", "#f1416c", "#ffc700", "#7239ea",
    "#43ced7", "#fd7e14", "#20c997", "#e83e8c", "#6610f2",
]


def render_chart_png(binding_data: dict, chart_type: str = "line") -> str | None:
    """Çözülmüş binding verisinden PNG grafik üretir; base64 data URI döner.

    Grafik değer ekseni: raw'da değer, aggregate'te ortalama (avg).
    Veri yoksa None döner (çağıran "veri yok" gösterir).
    """
    if not CHARTS_AVAILABLE:
        return None

    series = [s for s in binding_data.get("series", []) if s["rows"]]
    if not series:
        return None

    fig, ax = plt.subplots(figsize=(9.6, 3.6), dpi=110)
    try:
        for idx, s in enumerate(series):
            xs = [row[0] for row in s["rows"]]
            ys = [row[1] for row in s["rows"]]  # raw: value, aggregate: avg
            color = _CHART_COLORS[idx % len(_CHART_COLORS)]
            lbl = s["label"] + (f" ({s['unit']})" if s["unit"] else "")
            if chart_type == "bar":
                # Bar genişliği bucket aralığına göre kabaca ölçeklenir
                width = 0.8 * (
                    (xs[1] - xs[0]).total_seconds() / 86400 if len(xs) > 1 else 0.02
                )
                ax.bar(xs, ys, width=width, label=lbl, color=color, alpha=0.85)
            else:
                ax.plot(xs, ys, label=lbl, color=color, linewidth=1.8)

        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        ax.tick_params(labelsize=8)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")
    finally:
        plt.close(fig)


# --------------------------------------------------------------------------- #
# Blok çözümü → render-hazır context
# --------------------------------------------------------------------------- #

def _logo_data_uri() -> str:
    """Rapor başlığındaki logo (static → data URI; WeasyPrint dış istek yapmaz)."""
    logo_path = os.path.join(settings.BASE_DIR, "static", "images", "logo", "logo.png")
    try:
        with open(logo_path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""


def _resolve_blocks(blocks: list, now: datetime.datetime) -> list[dict]:
    """Şablonun blok listesini render-hazır dict listesine çevirir.

    Her blok tek tek çözülür; bir bloğun sorgu hatası diğerlerini bozmaz
    (blok yerine hata kutusu render edilir).
    """
    resolved = []
    table_no = 0
    chart_no = 0

    for block in blocks or []:
        btype = block.get("type")
        try:
            if btype == "heading":
                resolved.append({
                    "type": "heading",
                    "text": str(block.get("text") or ""),
                    "level": min(max(int(block.get("level") or 1), 1), 3),
                })
            elif btype == "text":
                resolved.append({"type": "text", "text": str(block.get("text") or "")})
            elif btype == "spacer":
                resolved.append({"type": "spacer"})
            elif btype == "page_break":
                resolved.append({"type": "page_break"})
            elif btype == "kpi_cards":
                cards = [_kpi_value(c, now) for c in (block.get("cards") or [])]
                resolved.append({"type": "kpi_cards", "cards": cards})
            elif btype == "chart":
                chart_no += 1
                data = query_binding(block.get("binding") or {}, now)
                img = render_chart_png(data, block.get("chart_type") or "line")
                resolved.append({
                    "type": "chart",
                    "no": chart_no,
                    "title": block.get("title") or f"Grafik {chart_no}",
                    "station_name": data["station_name"],
                    "subtitle": f"{data['station_name']} · {data['bucket_label']} · {data['window_label']}",
                    "img": img,
                    "charts_available": CHARTS_AVAILABLE,
                    "has_data": any(s["rows"] for s in data["series"]),
                    # Excel üretimi ham seriye + grafik tipine ihtiyaç duyar
                    "_data": data,
                    "_chart_type": block.get("chart_type") or "line",
                })
            elif btype == "table":
                table_no += 1
                data = query_binding(block.get("binding") or {}, now)
                resolved.append(_build_table_block(block, data, table_no))
            else:
                resolved.append({"type": "error", "message": f"Bilinmeyen blok tipi: {btype}"})
        except Exception as exc:  # noqa: BLE001 — tek blok hatası raporu düşürmesin
            logger.exception("Rapor bloğu çözülemedi (type=%s)", btype)
            resolved.append({"type": "error", "message": f"Blok işlenemedi ({btype}): {exc}"})

    return resolved


# Tablo kolon seçenekleri (aggregate). raw'da sabit: zaman/değer/kalite/status.
AGG_COLUMNS = {
    "avg": "Ortalama",
    "min": "Min",
    "max": "Max",
    "count": "Okuma",
    "bad_count": "Hatalı",
}
_AGG_ROW_INDEX = {"avg": 1, "min": 2, "max": 3, "count": 4, "bad_count": 5}


def _build_table_block(block: dict, data: dict, table_no: int) -> dict:
    """Çözülmüş binding verisinden tablo bloğu context'i üretir.

    Satır düzeni: zaman + (seri × seçili kolonlar). Zamanlar tüm serilerin
    birleşimidir (dış birleşim — eksik hücre "—").
    """
    bucket = data["bucket"]
    is_raw = bucket == "raw"
    sel_cols = [c for c in (block.get("binding", {}).get("columns") or ["avg"]) if c in AGG_COLUMNS]
    if not sel_cols:
        sel_cols = ["avg"]

    # Kolon başlıkları
    columns = ["Zaman"]
    for s in data["series"]:
        unit = f" ({s['unit']})" if s["unit"] else ""
        if is_raw:
            columns.append(f"{s['label']}{unit}")
        else:
            for c in sel_cols:
                columns.append(f"{s['label']}{unit} — {AGG_COLUMNS[c]}")

    # Zaman birleşimi. Ham veride sensörler aynı dakika içinde farklı
    # saniyelerde okunur; birebir zaman eşleşmesi her okumaya ayrı satır açar
    # (seyrek '—' matrisi). Bu yüzden ham tablo DAKİKA çözünürlüğünde
    # birleştirilir — seri başına o dakikanın son okuması gösterilir.
    if is_raw:
        def _key(ts):
            return ts.replace(second=0, microsecond=0)
    else:
        def _key(ts):
            return ts

    all_ts = sorted({_key(row[0]) for s in data["series"] for row in s["rows"]})
    # rows zaman sırasında geldiği için dict'te son yazan kazanır (dakikanın son değeri)
    by_series = [{_key(row[0]): row for row in s["rows"]} for s in data["series"]]

    def _fmt(v):
        if v is None:
            return "—"
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)

    rows = []
    for ts in all_ts:
        row = [ts.strftime(DATE_FMT)]
        for smap in by_series:
            r = smap.get(ts)
            if is_raw:
                row.append(_fmt(r[1]) if r else "—")
            else:
                for c in sel_cols:
                    row.append(_fmt(r[_AGG_ROW_INDEX[c]]) if r else "—")
        rows.append(row)

    return {
        "type": "table",
        "no": table_no,
        "title": block.get("title") or f"Tablo {table_no}",
        "subtitle": f"{data['station_name']} · {data['bucket_label']} · {data['window_label']}",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        # Excel üretimi ham seriye ihtiyaç duyar
        "_data": data,
        "_sel_cols": sel_cols,
        "_is_raw": is_raw,
    }


# --------------------------------------------------------------------------- #
# HTML / PDF / Excel render
# --------------------------------------------------------------------------- #

def render_report_html(template, now: datetime.datetime, *, for_pdf: bool = False) -> str:
    """Şablonu (kaydedilmiş veya in-memory ReportTemplate) HTML'e render eder.

    Aynı HTML hem editör önizlemesinde (iframe srcdoc) hem WeasyPrint PDF
    girdisinde kullanılır.
    """
    resolved = _resolve_blocks(template.blocks or [], now)

    # Önizleme (ekran) için blokları sayfalara böl — her page_break yeni bir
    # "kağıt" başlatır (PDF'te bunu @page + page-break-before halleder).
    pages = [[]]
    for b in resolved:
        if b["type"] == "page_break":
            pages.append([])
        else:
            pages[-1].append(b)

    ctx = {
        "report": template,
        "blocks": resolved,
        "pages": pages,
        "generated_at": timezone.localtime(now),
        "logo_uri": _logo_data_uri() if template.show_logo else "",
        "for_pdf": for_pdf,
        "app_version": getattr(settings, "APP_VERSION", "dev"),
    }
    return render_to_string("reporting/report.html", ctx)


def render_pdf(html: str) -> bytes:
    """HTML → PDF (WeasyPrint). PDF_AVAILABLE kontrolü çağıranın sorumluluğu."""
    return _WeasyHTML(string=html).write_pdf()


def render_xlsx(template, now: datetime.datetime) -> bytes:
    """Şablonu Excel çalışma kitabına render eder (openpyxl).

    "Özet" sayfası (rapor adı + üretim zamanı + KPI'lar) + tablo/grafik bloğu
    başına bir sayfa. Grafikler native LineChart/BarChart olarak eklenir.
    """
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Özet"

    head_font = Font(bold=True, size=13)
    col_font = Font(bold=True, color="FFFFFF")
    col_fill = PatternFill("solid", fgColor="3F4254")

    ws["A1"] = template.name
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = f"Üretim zamanı: {timezone.localtime(now):{DATE_FMT}}"
    if template.description:
        ws["A3"] = template.description
    row_cursor = 5

    resolved = _resolve_blocks(template.blocks or [], now)

    # Özet sayfasına KPI'lar
    kpis = [b for b in resolved if b["type"] == "kpi_cards"]
    if kpis:
        ws.cell(row=row_cursor, column=1, value="Özet Değerler").font = head_font
        row_cursor += 1
        for cell_ref, title in ((1, "Etiket"), (2, "Değer"), (3, "Birim"), (4, "Açıklama")):
            c = ws.cell(row=row_cursor, column=cell_ref, value=title)
            c.font = col_font
            c.fill = col_fill
        row_cursor += 1
        for kb in kpis:
            for card in kb["cards"]:
                ws.cell(row=row_cursor, column=1, value=card["label"])
                ws.cell(row=row_cursor, column=2, value=card["value_str"])
                ws.cell(row=row_cursor, column=3, value=card["unit"])
                ws.cell(row=row_cursor, column=4, value=card["sub"])
                row_cursor += 1
    for col, width in (("A", 32), ("B", 16), ("C", 12), ("D", 34)):
        ws.column_dimensions[col].width = width

    # Tablo + grafik sayfaları
    for b in resolved:
        if b["type"] == "table":
            wst = wb.create_sheet(f"Tablo {b['no']}"[:31])
            wst["A1"] = b["title"]
            wst["A1"].font = head_font
            wst["A2"] = b["subtitle"]
            for ci, col_name in enumerate(b["columns"], start=1):
                c = wst.cell(row=4, column=ci, value=col_name)
                c.font = col_font
                c.fill = col_fill
                c.alignment = Alignment(horizontal="center")
                wst.column_dimensions[get_column_letter(ci)].width = max(len(str(col_name)) + 2, 14)
            for ri, row in enumerate(b["rows"], start=5):
                for ci, val in enumerate(row, start=1):
                    # Sayısal hücreler Excel'de sayı olarak yazılsın
                    if ci > 1 and val not in ("—", ""):
                        try:
                            val = float(val)
                        except (TypeError, ValueError):
                            pass
                    wst.cell(row=ri, column=ci, value=val)
        elif b["type"] == "chart" and b.get("has_data"):
            # Grafik verisini sayfaya yaz + üzerine native Excel grafiği koy
            data = b["_data"]
            wsc = wb.create_sheet(f"Grafik {b['no']}"[:31])
            wsc["A1"] = b["title"]
            wsc["A1"].font = head_font
            wsc["A2"] = b["subtitle"]

            series_list = [s for s in data["series"] if s["rows"]]
            all_ts = sorted({row[0] for s in series_list for row in s["rows"]})
            by_series = [{row[0]: row for row in s["rows"]} for s in series_list]

            wsc.cell(row=4, column=1, value="Zaman").font = col_font
            wsc.cell(row=4, column=1).fill = col_fill
            for ci, s in enumerate(series_list, start=2):
                unit = f" ({s['unit']})" if s["unit"] else ""
                c = wsc.cell(row=4, column=ci, value=f"{s['label']}{unit}")
                c.font = col_font
                c.fill = col_fill
                wsc.column_dimensions[get_column_letter(ci)].width = 20
            wsc.column_dimensions["A"].width = 18

            for ri, ts in enumerate(all_ts, start=5):
                wsc.cell(row=ri, column=1, value=ts.strftime(DATE_FMT))
                for ci, smap in enumerate(by_series, start=2):
                    r = smap.get(ts)
                    wsc.cell(row=ri, column=ci, value=r[1] if r else None)

            if all_ts:
                chart_cls = BarChart if b.get("_chart_type") == "bar" else LineChart
                chart = chart_cls()
                chart.title = b["title"]
                chart.height = 9
                chart.width = 24
                last_row = 4 + len(all_ts)
                values = Reference(wsc, min_col=2, max_col=1 + len(series_list),
                                   min_row=4, max_row=last_row)
                cats = Reference(wsc, min_col=1, min_row=5, max_row=last_row)
                chart.add_data(values, titles_from_data=True)
                chart.set_categories(cats)
                wsc.add_chart(chart, f"{get_column_letter(len(series_list) + 3)}4")

    return _wb_to_bytes(wb)


def _wb_to_bytes(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# --------------------------------------------------------------------------- #
# E-posta dağıtımı
# --------------------------------------------------------------------------- #

class _SafeDict(dict):
    """format_map için — bilinmeyen placeholder olduğu gibi kalır."""

    def __missing__(self, key):
        return "{" + key + "}"


def _parse_recipients(raw: str) -> list[str]:
    parts = re.split(r"[,;\n]+", raw or "")
    return [p.strip() for p in parts if p.strip() and "@" in p]


def _send_report_email(report, schedule, attachments) -> tuple[str, str]:
    """Raporu zamanlamanın alıcılarına gönderir → (email_status, email_info)."""
    from api.notifications import send_email

    recipients = _parse_recipients(schedule.recipients)
    if not recipients:
        return "failed", "Alıcı listesi boş."

    fmt = _SafeDict(
        report_name=report.template_name,
        date=timezone.localtime(report.reference_time).strftime("%d.%m.%Y"),
    )
    subject = (schedule.email_subject or "{report_name} - {date}").format_map(fmt)
    body_text = (schedule.email_body or "").format_map(fmt) or (
        f"{report.template_name} raporu ektedir.\n"
        f"Üretim zamanı: {timezone.localtime(report.reference_time):{DATE_FMT}}"
    )
    html_body = "<p>" + body_text.replace("\n", "<br>") + "</p>"

    ok_count = 0
    infos = []
    for to in recipients:
        ok, info = send_email(
            to, subject, html_body, text_body=body_text,
            kind="report", attachments=attachments,
        )
        ok_count += 1 if ok else 0
        infos.append(f"{to}: {'OK' if ok else info}")

    if ok_count == len(recipients):
        return "sent", "; ".join(infos)
    if ok_count > 0:
        return "partial", "; ".join(infos)
    return "failed", "; ".join(infos)


# --------------------------------------------------------------------------- #
# Ana üretim akışı
# --------------------------------------------------------------------------- #

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def generate_report(template_id: int, *, schedule_id: int | None = None,
                    trigger: str = "manual", user_id: int | None = None,
                    formats: list[str] | None = None):
    """Bir şablondan rapor üretir → GeneratedReport kaydı döndürür.

    Asla exception fırlatmaz; hata kayda (`error` + status="failed") yazılır.
    `formats` verilmezse zamanlamanın (varsa) format seçimleri, o da yoksa
    ["pdf"] kullanılır.
    """
    from api.events import EventType, log_event
    from api.models import GeneratedReport, ReportSchedule, ReportTemplate
    from users.models import CustomUser

    template = ReportTemplate.objects.filter(pk=template_id).first()
    schedule = ReportSchedule.objects.filter(pk=schedule_id).first() if schedule_id else None
    user = CustomUser.objects.filter(pk=user_id).first() if user_id else None
    now = timezone.now()

    report = GeneratedReport.objects.create(
        template=template,
        template_name=template.name if template else f"Şablon #{template_id}",
        schedule=schedule,
        trigger=trigger,
        triggered_by=user,
        reference_time=now,
        status="running",
    )

    if not template:
        report.status = "failed"
        report.error = "Rapor şablonu bulunamadı."
        report.finished_at = timezone.now()
        report.save(update_fields=["status", "error", "finished_at"])
        return report

    if formats is None:
        if schedule:
            formats = [f for f, on in (("pdf", schedule.output_pdf), ("excel", schedule.output_excel)) if on]
        else:
            formats = ["pdf"]
    formats = formats or ["pdf"]

    warnings: list[str] = []
    attachments: list[tuple[str, bytes, str]] = []
    stamp = timezone.localtime(now).strftime("%Y%m%d_%H%M%S")
    base_name = f"report_{template.pk}_{stamp}"

    try:
        os.makedirs(settings.REPORTS_DIR, exist_ok=True)

        # --- PDF ---
        if "pdf" in formats:
            if PDF_AVAILABLE:
                html = render_report_html(template, now, for_pdf=True)
                pdf_bytes = render_pdf(html)
                fname = f"{base_name}.pdf"
                with open(os.path.join(settings.REPORTS_DIR, fname), "wb") as f:
                    f.write(pdf_bytes)
                report.pdf_file = fname
                report.pdf_size = len(pdf_bytes)
                attachments.append((f"{template.name}.pdf", pdf_bytes, "application/pdf"))
            else:
                warnings.append(
                    f"PDF bu sunucuda üretilemedi (WeasyPrint kullanılamıyor: "
                    f"{PDF_UNAVAILABLE_REASON[:200]})"
                )

        # --- Excel ---
        if "excel" in formats:
            xlsx_bytes = render_xlsx(template, now)
            fname = f"{base_name}.xlsx"
            with open(os.path.join(settings.REPORTS_DIR, fname), "wb") as f:
                f.write(xlsx_bytes)
            report.xlsx_file = fname
            report.xlsx_size = len(xlsx_bytes)
            attachments.append((f"{template.name}.xlsx", xlsx_bytes, XLSX_MIME))

        produced_any = bool(report.pdf_file or report.xlsx_file)

        # --- E-posta (yalnız zamanlanmış + etkinse) ---
        if schedule and schedule.email_enabled and produced_any:
            report.email_status, report.email_info = _send_report_email(report, schedule, attachments)
        elif schedule and schedule.email_enabled and not produced_any:
            report.email_status, report.email_info = "failed", "Üretilen dosya yok — e-posta atlanmadı."

        report.status = "success" if produced_any else "failed"
        if not produced_any and not warnings:
            warnings.append("Hiçbir çıktı üretilmedi.")
        report.error = "\n".join(warnings)
    except Exception as exc:  # noqa: BLE001 — üretim hatası kayda düşer
        logger.exception("Rapor üretimi başarısız (template=%s)", template_id)
        report.status = "failed"
        report.error = "\n".join([*warnings, str(exc)])

    report.finished_at = timezone.now()
    report.save()

    # Zamanlama durumunu damgala
    if schedule:
        schedule.last_run_at = report.finished_at
        schedule.last_status = report.status
        schedule.save(update_fields=["last_run_at", "last_status"])

    log_event(
        EventType.SYSTEM,
        f"Rapor üretildi: {report.template_name} [{report.status}]"
        + (f" — {report.error[:120]}" if report.error else ""),
        severity="info" if report.status == "success" else "warning",
        user=user,
    )
    return report
