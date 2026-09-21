"""Bağlantı ağacı dışa/içe aktarım (Connection → ScanGroup → Sensor).

Bir tesisin bağlantı konfigürasyonunun tamamını (bağlantı + scan grupları +
sensörler + sensörlerin ihtiyaç duyduğu Parametre tanımları) tek bir taşınabilir
JSON'a serileştirir ve başka bir tesise geri yükler. Amaç: birebir aynı kurulan
sahalarda scan/sensör ağacını tek tuşla klonlamak.

Jenerik SCADA kavramı (Connection/ScanGroup/Sensor/Parameter tümü `api/`'da) →
bu modül `api/`'a aittir. SAIS/Envisoft-özel eşlemeler (EnvisoftChannel) kapsam
dışıdır; onlar tesis-özel Bakanlık yayını konfigürasyonudur.

Serileştirme kuralları:
- **Runtime/kimlik alanları taşınmaz**: id, station FK, created_date ve
  reader/worker'ın yazdığı `last_*` durum alanları export'a girmez.
- **Sensör → ScanGroup** referansı grup **adı** ile tutulur (grup adı bağlantı
  içinde benzersizdir); import'ta yeni grup id'sine yeniden bağlanır.
- **Sensör → Parameter** referansı export sırasında verilen sentetik anahtarla
  (`p0`, `p1`, ...) tutulur; import'ta hedef tesiste `parameter_name` ile
  find-or-create edilir (kod eşleşirse mevcut parametre kullanılır, yoksa
  yeni oluşturulur).
- **Sensör tag'leri boşaltılır** — tag sistem genelinde benzersizdir; hedefte
  kayıtta `Sensor.save()` taze benzersiz tag üretir (çakışma önlenir).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

EXPORT_FORMAT = "sais_web.connection_export"
EXPORT_VERSION = 1

# Config-only alan listeleri — runtime/id/FK alanları kasten hariç.
CONNECTION_FIELDS = (
    "name", "description", "protocol", "transport",
    "host", "port", "serial_port",
    "baudrate", "parity", "stop_bits", "byte_size",
    "xonxoff", "rtscts", "dsrdtr",
    "poll_interval_sec", "save_interval_sec", "timeout_ms",
    "retry_count", "single_session", "auto_reconnect", "reconnect_delay_sec",
    "is_enabled",
)

SCANGROUP_FIELDS = (
    "name", "slave_id", "function", "start_address", "quantity", "is_active",
)

SENSOR_FIELDS = (
    "sensor_type", "brand", "model", "serial_number", "signal_type",
    "slave_id", "byte_order", "word_order",
    "address", "quantity", "function", "bit_position",
    "data_type", "scale", "offset", "decimals",
    "ascii_code", "ascii_request", "ascii_response_regex", "ascii_line_terminator",
    "poll_interval_sec", "timeout_ms", "retry_count",
    "digital_inverse", "is_active", "display_order", "dashboard_hidden",
    "is_simulated", "sim_min", "sim_max", "report_status",
    "save_on_change", "deadband", "cov_heartbeat_sec",
)

PARAMETER_FIELDS = (
    "parameter_name", "parameter_txt", "unit", "unit_txt",
    "channel_number", "device_channel_id",
    "gec_min", "gec_max", "olcum_min", "olcum_max",
    "min_range", "max_range",
)


def export_connection(conn) -> dict:
    """Bir `Connection`'ı tüm alt ağacıyla taşınabilir dict'e serileştirir."""
    from api.models import Sensor
    from django.conf import settings

    scan_groups = list(conn.scan_groups.all().order_by("slave_id", "start_address", "id"))
    sensors = list(
        Sensor.objects.filter(connection=conn)
        .select_related("parameter", "scan_group")
        .order_by("display_order", "id")
    )

    # Sensörlerin referans ettiği benzersiz parametreleri topla; her birine
    # sentetik anahtar ver (sensör bu anahtarla parametreye bağlanır).
    param_key_by_id: dict[int, str] = {}
    parameters: list[dict] = []
    for s in sensors:
        if s.parameter_id and s.parameter_id not in param_key_by_id:
            key = f"p{len(parameters)}"
            param_key_by_id[s.parameter_id] = key
            parameters.append({
                "key": key,
                **{f: getattr(s.parameter, f) for f in PARAMETER_FIELDS},
            })

    scan_group_data = [
        {f: getattr(g, f) for f in SCANGROUP_FIELDS} for g in scan_groups
    ]

    sensor_data = []
    for s in sensors:
        row = {f: getattr(s, f) for f in SENSOR_FIELDS}
        row["scan_group_name"] = s.scan_group.name if s.scan_group_id else None
        row["parameter_key"] = param_key_by_id.get(s.parameter_id)
        sensor_data.append(row)

    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": timezone.now().isoformat(),
        "app_version": getattr(settings, "APP_VERSION", "dev"),
        "source": {
            "station": conn.station.name if conn.station_id else None,
            "connection_name": conn.name,
        },
        "connection": {f: getattr(conn, f) for f in CONNECTION_FIELDS},
        "parameters": parameters,
        "scan_groups": scan_group_data,
        "sensors": sensor_data,
    }


class ImportError_(ValueError):
    """İçe aktarım doğrulama hatası (kullanıcıya gösterilebilir mesaj)."""


def _unique_connection_name(station, desired: str) -> str:
    """Tesis içinde benzersiz bağlantı adı üretir (çakışırsa ' (2)', ' (3)' ...)."""
    from api.models import Connection

    base = (desired or "").strip() or "Bağlantı"
    name = base
    i = 2
    while Connection.objects.filter(station=station, name=name).exists():
        name = f"{base} ({i})"
        i += 1
    return name


def validate_payload(data) -> None:
    """Yüklenen dict'in beklenen export formatında olduğunu doğrular."""
    if not isinstance(data, dict):
        raise ImportError_("Geçersiz dosya: JSON nesnesi bekleniyordu.")
    if data.get("format") != EXPORT_FORMAT:
        raise ImportError_("Bu dosya bir bağlantı dışa aktarımı değil.")
    if int(data.get("version") or 0) > EXPORT_VERSION:
        raise ImportError_(
            "Dosya bu sürümden daha yeni bir formatta; uygulamayı güncelleyin."
        )
    if not isinstance(data.get("connection"), dict):
        raise ImportError_("Dosyada bağlantı verisi yok.")


@transaction.atomic
def import_connection(data, station, name: str | None = None):
    """Export dict'ini `station` tesisine yeni bir bağlantı ağacı olarak yükler.

    Döner: oluşturulan `Connection`. Parametreler hedef tesiste `parameter_name`
    ile find-or-create edilir. Bağlantı adı çakışırsa otomatik son ek eklenir.
    Tümü tek transaction — herhangi bir adım patlarsa hiçbir şey yazılmaz.
    """
    from api.models import Connection, Parameter, ScanGroup, Sensor

    validate_payload(data)

    conn_fields = {
        f: data["connection"].get(f) for f in CONNECTION_FIELDS
        if f in data["connection"]
    }
    desired = (name or "").strip() or conn_fields.get("name") or "Bağlantı"
    conn_fields["name"] = _unique_connection_name(station, desired)

    conn = Connection(station=station, **conn_fields)
    conn.save()

    # Parametreleri hedef tesiste çöz (kod ile eşle, yoksa oluştur).
    param_by_key: dict[str, Parameter] = {}
    for pdata in data.get("parameters") or []:
        key = pdata.get("key")
        fields = {f: pdata.get(f) for f in PARAMETER_FIELDS if f in pdata}
        code = (fields.get("parameter_name") or "").strip()
        param = None
        if code:
            param = Parameter.objects.filter(
                station=station, parameter_name=code
            ).first()
        if param is None:
            param = Parameter.objects.create(station=station, **fields)
        if key:
            param_by_key[key] = param

    # Scan grupları — ada göre map (sensörler ada göre referans eder).
    group_by_name: dict[str, ScanGroup] = {}
    for gdata in data.get("scan_groups") or []:
        fields = {f: gdata.get(f) for f in SCANGROUP_FIELDS if f in gdata}
        group = ScanGroup.objects.create(connection=conn, **fields)
        group_by_name[group.name] = group

    # Sensörler.
    for sdata in data.get("sensors") or []:
        fields = {f: sdata.get(f) for f in SENSOR_FIELDS if f in sdata}
        sensor = Sensor(connection=conn, **fields)
        sensor.tag = ""  # save() taze benzersiz tag üretsin (çakışma önlenir)

        gname = sdata.get("scan_group_name")
        if gname and gname in group_by_name:
            sensor.scan_group = group_by_name[gname]

        pkey = sdata.get("parameter_key")
        if pkey and pkey in param_by_key:
            sensor.parameter = param_by_key[pkey]

        # scan_group set ise save() connection/slave_id/function'ı gruptan
        # miras alır — bu istenen davranış.
        sensor.save()

    return conn
