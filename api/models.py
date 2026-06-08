"""
SCADA çekirdek veri modelleri.

Bu uygulama (`api`) her türlü endüstriyel izleme / SCADA senaryosunda
kullanılmaya uygun, alan-özel kavramlardan arındırılmış temel modelleri
içerir. Atıksu (SAIS), Envisoft gibi alan-özel uzantılar ayrı bir
uygulamada (`sais_domain`) tanımlıdır.
"""
from django.core.exceptions import ValidationError
from django.db import models

from users.models import CustomUser


# Modbus data_type → register sayısı (ScanGroup validation'da kullanılır).
# scada_io.decoders'daki REGISTER_COUNT ile senkron tutulmalı; circular import
# önlemek için burada kopyalandı.
_REGISTERS_PER_DATA_TYPE = {
    "int16": 1, "uint16": 1, "bool": 1, "bit": 1,
    "int32": 2, "uint32": 2, "float32": 2,
    "int64": 4, "uint64": 4, "float64": 4,
}


class StationType(models.Model):
    """İstasyon tipi lookup'u. Hardcoded enum yerine DB'de kayıt."""

    code = models.CharField(
        max_length=30, unique=True,
        verbose_name="Kod", help_text="Makine-okur kod (örn. 'wastewater_domestic')",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="Ad", help_text="İstasyon tipi adı",
    )
    description = models.CharField(
        max_length=250, blank=True, default="",
        verbose_name="Açıklama",
    )

    class Meta:
        db_table = "station_type"
        verbose_name_plural = "İstasyon Tipleri"
        ordering = ["code"]

    def __str__(self):
        return self.name


class Station(models.Model):
    """İzleme istasyonu (saha)."""

    name = models.CharField(
        max_length=100, verbose_name="İstasyon Adı", help_text="İstasyon Adı",
    )
    station_type = models.ForeignKey(
        StationType, on_delete=models.SET_NULL,
        blank=True, null=True,
        verbose_name="İstasyon Tipi",
    )
    address = models.CharField(
        max_length=250, blank=True, default="",
        verbose_name="İstasyon Adresi", help_text="İstasyon Adresi",
    )
    domain = models.URLField(
        max_length=100, blank=True, default="",
        verbose_name="Domain", help_text="İstasyonun bağlı olduğu uygulamanın domaini",
    )
    port = models.IntegerField(
        blank=True, null=True, default=443,
        verbose_name="Port No", help_text="Port No",
    )
    company = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name="Kurum Adı", help_text="Kurum Adı",
    )
    active = models.BooleanField(
        default=True, verbose_name="Aktif mi?", help_text="Aktif mi?",
    )
    sample_request_sensor = models.ForeignKey(
        "Sensor", on_delete=models.SET_NULL,
        blank=True, null=True,
        related_name="+",
        verbose_name="Numune Alma Sensörü",
        help_text="StartSample servisinin tetikleyeceği dijital-out sensörü",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "station"
        verbose_name_plural = "İstasyon Bilgileri"
        ordering = ["created_at"]

    def __str__(self):
        return self.name


class Connection(models.Model):
    """SCADA haberleşme bağlantısı (Modbus + ASCII).

    Bir `Station`'a bağlıdır ve tek bir protokol + taşıma katmanı tanımlar.
    Desteklenen protokoller:

    - Modbus TCP (network)
    - Modbus RTU / Modbus ASCII (serial)
    - Özel ASCII (serial veya TCP üzerinden request-response)

    Network protokolleri için `host` + `port`, serial için `serial_port` +
    baudrate/parity alanları kullanılır. Diğer alanlar protokole göre
    anlamlı olanlar doldurulur.
    """

    PROTOCOL_CHOICES = (
        ("modbus_tcp", "Modbus TCP"),
        ("modbus_rtu", "Modbus RTU (Serial)"),
        ("modbus_ascii", "Modbus ASCII (Serial)"),
        ("modbus_rtu_over_tcp", "Modbus RTU over TCP (Gateway)"),
        ("modbus_ascii_over_tcp", "Modbus ASCII over TCP (Gateway)"),
        ("ascii_custom", "Özel ASCII (Request-Response)"),
    )
    TRANSPORT_CHOICES = (
        ("tcp", "TCP/IP"),
        ("serial", "Serial (RS-232/RS-485)"),
    )
    BAUDRATES = (
        (300, "300"), (600, "600"), (1200, "1200"), (2400, "2400"),
        (4800, "4800"), (9600, "9600"), (14400, "14400"), (19200, "19200"),
        (38400, "38400"), (57600, "57600"), (115200, "115200"),
        (230400, "230400"), (460800, "460800"),
    )
    PARITY = ((0, "None"), (1, "Odd"), (2, "Even"))
    STOP_BITS = ((1, "1"), (2, "2"))
    BYTE_SIZE = ((7, "7"), (8, "8"))

    # ---- Kimlik ----
    station = models.ForeignKey(
        Station, on_delete=models.CASCADE,
        blank=True, null=True, related_name="connections",
        verbose_name="İstasyon",
    )
    name = models.CharField(
        max_length=100, default="",
        verbose_name="Bağlantı Adı",
        help_text="Bu istasyondaki bağlantının özgün adı",
    )
    description = models.CharField(
        max_length=500, blank=True, default="",
        verbose_name="Açıklama",
    )

    # ---- Protokol & taşıma ----
    protocol = models.CharField(
        max_length=30, choices=PROTOCOL_CHOICES, default="modbus_tcp",
        verbose_name="Protokol",
    )
    transport = models.CharField(
        max_length=10, choices=TRANSPORT_CHOICES, default="tcp",
        verbose_name="Taşıma Katmanı",
    )

    # ---- Network (TCP) ----
    host = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Host / IP",
        help_text="Hostname veya IP (IPv4/IPv6); serial için boş",
    )
    port = models.IntegerField(
        blank=True, null=True, default=502,
        verbose_name="Port", help_text="Ağ portu (Modbus TCP: 502)",
    )

    # ---- Serial ----
    serial_port = models.CharField(
        max_length=50, blank=True, default="",
        verbose_name="Serial Port",
        help_text="COM4 (Windows) veya /dev/ttyUSB0 (Linux); TCP için boş",
    )
    baudrate = models.IntegerField(choices=BAUDRATES, default=9600, blank=True, null=True, verbose_name="Baudrate")
    parity = models.IntegerField(choices=PARITY, default=0, blank=True, null=True, verbose_name="Parity")
    stop_bits = models.IntegerField(choices=STOP_BITS, default=1, blank=True, null=True, verbose_name="Stop Bits")
    byte_size = models.IntegerField(choices=BYTE_SIZE, default=8, blank=True, null=True, verbose_name="Data Bits")
    xonxoff = models.BooleanField(default=False, verbose_name="XON/XOFF")
    rtscts = models.BooleanField(default=False, verbose_name="RTS/CTS")
    dsrdtr = models.BooleanField(default=False, verbose_name="DSR/DTR")

    # ---- Polling / güvenilirlik ----
    poll_interval_sec = models.IntegerField(
        default=10, verbose_name="Okuma Periyodu (sn)",
        help_text="Cihaza ne sıklıkta bağlanıp okuma yapılacak. "
                  "Snapshot (SensorLatest) her okumada güncellenir.",
    )
    save_interval_sec = models.IntegerField(
        blank=True, null=True,
        verbose_name="Kayıt Periyodu (sn)",
        help_text="Reading tablosuna ne sıklıkta yazılacak. "
                  "Null = her okumada Reading insert. "
                  "Örn: poll=5sn, save=60sn ise anlık değer 5 sn'de bir güncellenir "
                  "ama tarihsel veri dakikada 1 yazılır (DB tasarrufu).",
    )
    timeout_ms = models.IntegerField(
        default=2000, verbose_name="Bağlantı Timeout (ms)",
    )
    retry_count = models.IntegerField(
        default=1, verbose_name="Retry Sayısı",
    )
    auto_reconnect = models.BooleanField(
        default=True, verbose_name="Otomatik Yeniden Bağlan",
    )
    reconnect_delay_sec = models.IntegerField(
        default=5, verbose_name="Yeniden Bağlanma Gecikmesi (sn)",
    )

    # ---- Config durumu ----
    is_enabled = models.BooleanField(
        default=True, verbose_name="Aktif",
        help_text="Reader bu bağlantıyı tarasın mı?",
    )

    # ---- Runtime durumu (reader/worker tarafından güncellenir) ----
    last_polled_at = models.DateTimeField(
        blank=True, null=True, db_index=True,
        verbose_name="Son Polling Zamanı",
        help_text="Bu bağlantı için son polling girişiminin zamanı (başarı/başarısızlık fark etmez). dispatch_polls due-check için kullanır.",
    )
    last_connected_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Son Bağlantı Zamanı",
        help_text="Son başarılı bağlantı zamanı.",
    )
    last_error_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Son Hata Zamanı",
    )
    last_error_message = models.CharField(
        max_length=500, blank=True, default="",
        verbose_name="Son Hata Mesajı",
    )

    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "connection"
        verbose_name_plural = "Bağlantılar"
        ordering = ["station", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["station", "name"],
                name="connection_unique_station_name",
            ),
        ]

    def __str__(self):
        if self.station_id:
            return f"{self.station} / {self.name}"
        return self.name or f"Connection-{self.pk}"


class StatusCode(models.Model):
    """Veri kalitesi / status kodları lookup'u."""

    code = models.IntegerField(blank=True, null=True, verbose_name="Kod Numarası")
    name = models.CharField(max_length=200, blank=True, null=True, verbose_name="Status Kod Adı")

    class Meta:
        db_table = "status_code"
        verbose_name_plural = "Status Kodları"
        ordering = ["id"]

    def __str__(self):
        return self.name or f"Code {self.code}"


class Parameter(models.Model):
    """Ölçülen büyüklük (pH, sıcaklık, debi vb.) tanımı."""

    station = models.ForeignKey(
        Station, on_delete=models.CASCADE,
        blank=True, null=True,
        related_name="parameters",
    )
    parameter_name = models.CharField(max_length=50, blank=True, null=True, verbose_name="Parametre Adı")
    parameter_txt = models.CharField(max_length=50, blank=True, null=True, verbose_name="Parametre Txt")
    unit = models.CharField(max_length=150, blank=True, null=True, verbose_name="Parametre Birim")
    unit_txt = models.CharField(max_length=50, blank=True, null=True, verbose_name="Parametre Birim Txt")
    channel_number = models.IntegerField(blank=True, null=True, verbose_name="Kanal No")
    device_channel_id = models.CharField(
        max_length=250, blank=True, null=True,
        verbose_name="Cihaz Kanal ID",
        help_text="Uzak cihaz tarafındaki kanal kimliği (UUID vb.)",
    )
    gec_min = models.FloatField(blank=True, null=True, verbose_name="Geçerli Veri Min")
    gec_max = models.FloatField(blank=True, null=True, verbose_name="Geçerli Veri Max")
    olcum_min = models.FloatField(blank=True, null=True, verbose_name="Ölçüm Altı")
    olcum_max = models.FloatField(blank=True, null=True, verbose_name="Ölçüm Üstü")
    min_range = models.FloatField(blank=True, null=True, verbose_name="Range Min")
    max_range = models.FloatField(blank=True, null=True, verbose_name="Range Max")

    class Meta:
        db_table = "parameter"
        verbose_name_plural = "Parametreler"
        ordering = ["id"]

    def __str__(self):
        return self.parameter_name or f"Parameter {self.pk}"


class ScanGroup(models.Model):
    """Modbus batch register okuma bloğu (Geo SCADA 'scanner' pattern).

    Tek Connection üzerinde; slave_id + function + start_address + quantity
    tanımlayarak bir register bloğu oluşturulur. Polling cycle'da tek bir
    Modbus request ile tüm blok okunur; bu grupdaki sensörler `sensor.address`
    (absolute Modbus adresi) üzerinden offset hesaplayıp decode ederler.

    Avantaj: 100 sensörlük bir blok tek request'te okunur — 100× hızlanma.
    1000+ tag'lık sahalarda tek connection üstünde çalışmanın tek yolu budur.
    """

    # Modbus okuma fonksiyonları — write fonksiyonları (5/6/15/16) scan group
    # kavramına uymaz.
    READ_FUNCTIONS = (
        (1, "Read Coils"),
        (2, "Read Discrete Inputs"),
        (3, "Read Holding Registers"),
        (4, "Read Input Registers"),
    )

    connection = models.ForeignKey(
        "Connection", on_delete=models.CASCADE,
        related_name="scan_groups",
        verbose_name="Bağlantı",
    )
    name = models.CharField(
        max_length=100, verbose_name="Grup Adı",
        help_text="Açıklayıcı isim (örn. 'Analog girişler 0-20', 'Status bitleri 100-150')",
    )
    slave_id = models.IntegerField(
        default=1, verbose_name="Slave ID",
    )
    function = models.IntegerField(
        choices=READ_FUNCTIONS, default=3,
        verbose_name="Fonksiyon",
    )
    start_address = models.IntegerField(
        verbose_name="Başlangıç Adresi",
        help_text="Modbus register başlangıç adresi (0-tabanlı)",
    )
    quantity = models.IntegerField(
        verbose_name="Register Sayısı",
        help_text="Okunacak register/coil sayısı. Max: function 3/4 → 125, function 1/2 → 2000",
    )
    is_active = models.BooleanField(
        default=True, verbose_name="Aktif",
        help_text="False ise bu grup polling'de atlanır",
    )

    class Meta:
        db_table = "scan_group"
        verbose_name_plural = "Scan Grupları"
        ordering = ["connection", "slave_id", "start_address"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "name"],
                name="scan_group_unique_connection_name",
            ),
        ]

    def __str__(self):
        return f"{self.name} (slave={self.slave_id} fn={self.function} [{self.start_address}..{self.end_address}))"

    @property
    def end_address(self) -> int:
        """Grup'un kapsadığı son adresin bir fazlası (exclusive)."""
        return (self.start_address or 0) + (self.quantity or 0)

    def clean(self):
        errors = {}
        if self.start_address is not None and self.start_address < 0:
            errors["start_address"] = "Negatif olamaz."
        if self.quantity is None or self.quantity <= 0:
            errors["quantity"] = "1 veya daha büyük olmalı."
        else:
            # Modbus protokol limitleri (hard enforce)
            if self.function in (3, 4) and self.quantity > 125:
                errors["quantity"] = (
                    f"Function {self.function} (Holding/Input Register) için maksimum 125 register okunabilir."
                )
            elif self.function in (1, 2) and self.quantity > 2000:
                errors["quantity"] = (
                    f"Function {self.function} (Coil/Discrete) için maksimum 2000 bit okunabilir."
                )
        if self.function not in (1, 2, 3, 4):
            errors["function"] = "Sadece okuma fonksiyonları (1, 2, 3, 4) desteklenir."
        if errors:
            raise ValidationError(errors)


class Sensor(models.Model):
    """Fiziksel / mantıksal sensör kanalı.

    Modbus TCP, Modbus RTU/ASCII ve özel ASCII protokollü (istek-yanıt veya
    streaming) cihazları aynı şemada temsil eder. Protokolün kendisi
    `connection.communication_type` / `connection.con_type` üzerinden
    belirlenir; bu modeldeki alanlar protokole göre anlamlı olanlar
    doldurulur, kalanları null bırakılır.
    """

    SENSOR_TYPE = (
        (0, "Analog Input"), (1, "Analog Output"),
        (2, "Dijital Input"), (3, "Dijital Output"),
    )
    BYTE_ORDER = (("little", "Endian.Little"), ("big", "Endian.Big"))
    SIGNAL_TYPE = ((0, "4-20mA"), (1, "0-20mA"), (2, "0-10mV"))
    FUNCTION = (
        (1, "Read Coils"), (2, "Read Discrete Inputs"),
        (3, "Read Holding Registers"), (4, "Read Input Registers"),
        (5, "Write Single Coil"), (6, "Write Single Register"),
        (15, "Write Multiple Coils"), (16, "Write Multiple Registers"),
    )
    DATA_TYPES = (
        ("int16", "Int16 / Short (signed, 1 register)"),
        ("uint16", "UInt16 / Word (unsigned, 1 register)"),
        ("int32", "Int32 / DInt (signed, 2 register)"),
        ("uint32", "UInt32 / DWord (unsigned, 2 register)"),
        ("int64", "Int64 / LInt (signed, 4 register)"),
        ("uint64", "UInt64 / LWord (unsigned, 4 register)"),
        ("float32", "Float32 / Real / Float (IEEE-754, 2 register)"),
        ("float64", "Float64 / Double / LReal (IEEE-754, 4 register)"),
        ("bool", "Bool (coil / 1-bit)"),
        ("bit", "Bit (register içinden bit_position)"),
        ("string", "String (ASCII metin)"),
        ("raw", "Raw bytes"),
    )
    LINE_TERMINATORS = (
        ("\r\n", "CRLF"),
        ("\n", "LF"),
        ("\r", "CR"),
    )

    parameter = models.ForeignKey(
        Parameter, on_delete=models.CASCADE,
        blank=True, null=True, related_name="sensors",
    )
    sensor_type = models.IntegerField(
        choices=SENSOR_TYPE, default=0, blank=True, null=True,
        verbose_name="Sensör Tipi",
    )
    brand = models.CharField(max_length=100, blank=True, null=True, verbose_name="Sensör Marka")
    model = models.CharField(max_length=100, blank=True, null=True, verbose_name="Sensör Model")
    serial_number = models.CharField(max_length=150, blank=True, null=True, verbose_name="Seri No")
    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE,
        related_name="sensors", verbose_name="Bağlantı",
    )
    scan_group = models.ForeignKey(
        ScanGroup, on_delete=models.SET_NULL,
        blank=True, null=True, related_name="sensors",
        verbose_name="Scan Grubu",
        help_text=(
            "Set edilirse bu sensör, grup'un batch Modbus okumasından "
            "decode edilir (tek request, 100× hızlı). slave_id, function "
            "ve address grup ile uyumlu olmalı. Null ise sensör başına "
            "ayrı Modbus request atılır (legacy mod)."
        ),
    )
    signal_type = models.IntegerField(
        choices=SIGNAL_TYPE, default=0, blank=True, null=True,
        verbose_name="Sinyal Tipi",
    )

    # ---- Modbus alanları ----
    slave_id = models.IntegerField(default=1, blank=True, null=True, verbose_name="Slave ID")
    byte_order = models.CharField(
        max_length=20, choices=BYTE_ORDER, default="big",
        blank=True, null=True, verbose_name="Byte Order",
    )
    word_order = models.CharField(
        max_length=20, choices=BYTE_ORDER, default="little",
        blank=True, null=True, verbose_name="Word Order",
    )
    address = models.IntegerField(
        blank=True, null=True,
        verbose_name="Haberleşme Adresi",
        help_text="Modbus register adresi / ASCII kayıt sırası",
    )
    quantity = models.IntegerField(
        blank=True, null=True, default=2,
        verbose_name="Adres Aralığı", help_text="Okunacak register sayısı",
    )
    function = models.IntegerField(
        choices=FUNCTION, default=3, blank=True, null=True, verbose_name="Fonksiyon",
    )
    bit_position = models.IntegerField(
        blank=True, null=True,
        verbose_name="Bit Pozisyonu",
        help_text="data_type='bit' için register içindeki bit indeksi (0-15)",
    )

    # ---- Veri tipi & mühendislik dönüşümü (protokol seviyesi) ----
    data_type = models.CharField(
        max_length=20, choices=DATA_TYPES, default="float32",
        blank=True, null=True,
        verbose_name="Veri Tipi",
        help_text="Raw byte/register'ların nasıl yorumlanacağı",
    )
    scale = models.FloatField(
        default=1.0, verbose_name="Ölçek (scale)",
        help_text="engineering_value = raw * scale + offset",
    )
    offset = models.FloatField(
        default=0.0, verbose_name="Ofset (offset)",
        help_text="engineering_value = raw * scale + offset",
    )
    decimals = models.IntegerField(
        blank=True, null=True,
        verbose_name="Ondalık Hassasiyet",
        help_text="Kayıt öncesi kaç basamağa yuvarlanacak. None = yuvarlama yok. "
                  "Örn: 2 → 12.3456 → 12.35. Sadece sayısal (float/int) değerlere uygulanır.",
    )

    # ---- Özel ASCII protokolü (NMEA, custom request-response vb.) ----
    ascii_code = models.CharField(
        max_length=20, blank=True, null=True,
        verbose_name="ASCII Cihaz Adresi",
        help_text="Multi-drop ASCII bus'larda cihaz/node adresi",
    )
    ascii_request = models.CharField(
        max_length=100, blank=True, null=True,
        verbose_name="ASCII İstek Komutu",
        help_text="Request-response ASCII için cihaza gönderilecek komut",
    )
    ascii_response_regex = models.CharField(
        max_length=250, blank=True, null=True,
        verbose_name="ASCII Yanıt Regex'i",
        help_text="Değeri ayıklamak için regex; ilk capture group değer olarak alınır",
    )
    ascii_line_terminator = models.CharField(
        max_length=4, choices=LINE_TERMINATORS, blank=True, null=True,
        verbose_name="Satır Sonu",
    )

    # ---- Polling / zamanlama ----
    poll_interval_sec = models.IntegerField(
        blank=True, null=True,
        verbose_name="Okuma Periyodu (sn)",
        help_text="Null ise bağlantı seviyesi varsayılanı kullanılır",
    )
    timeout_ms = models.IntegerField(
        default=2000, verbose_name="Timeout (ms)",
        help_text="Tek okuma için maksimum bekleme süresi",
    )
    retry_count = models.IntegerField(
        default=1, verbose_name="Retry Sayısı",
        help_text="Hatalı okumada tekrar deneme sayısı",
    )

    digital_inverse = models.BooleanField(default=False, verbose_name="Dijital Ters mi ?")
    is_active = models.BooleanField(default=True, verbose_name="Aktif")
    is_simulated = models.BooleanField(
        default=False, verbose_name="Simülasyon Modu",
        help_text="True ise reader cihazdan değer okumak yerine rastgele üretir (test/demo için)",
    )
    report_status = models.BooleanField(
        default=False, verbose_name="Status Raporla",
        help_text="True ise bu sensörün status'ü dış sisteme (Bakanlık) gönderilir",
    )

    # ---- Değişimde kaydet (Change-of-Value / deadband) ----
    save_on_change = models.BooleanField(
        default=False, verbose_name="Sadece Değişimde Kaydet",
        help_text=(
            "True ise Reading sadece değer değiştiğinde (analogda deadband'i "
            "aşan değişim, dijitalde herhangi bir değişim) ya da status "
            "değiştiğinde yazılır. SensorLatest snapshot'ı yine her okumada "
            "güncellenir (HMI taze kalır). Connection.save_interval_sec minimum "
            "aralık olarak yine uygulanır."
        ),
    )
    deadband = models.FloatField(
        blank=True, null=True,
        verbose_name="Ölü Bant (deadband)",
        help_text=(
            "save_on_change=True iken analog için son KAYDEDİLEN değere göre "
            "izin verilen sapma (mühendislik birimi, mutlak). |yeni - son_kayıt| "
            "> deadband ise kayıt yapılır. None/0 = her farklı değer kaydedilir. "
            "Bool/dijital sensörlerde göz ardı edilir (her değişim kaydedilir)."
        ),
    )
    cov_heartbeat_sec = models.IntegerField(
        blank=True, null=True,
        verbose_name="Heartbeat (sn)",
        help_text=(
            "save_on_change=True iken değer değişmese bile en geç bu sürede bir "
            "kayıt yapılır (historian'da sonsuz boşluk oluşmasın). None = "
            "heartbeat yok, değişene kadar hiç yazılmaz."
        ),
    )

    class Meta:
        db_table = "sensor"
        verbose_name_plural = "Sensörler"
        ordering = ["id"]

    def __str__(self):
        if self.parameter_id and self.parameter.parameter_name:
            return self.parameter.parameter_name
        return f"Sensor-{self.pk}"

    def registers_used(self) -> int:
        """data_type'a göre sensörün kaç register tükettiğini döner.

        Bit/coil/bool → 1, int/uint/float32 → 2 veya 4 ...
        data_type bilinmiyorsa `quantity` alanına düşer.
        """
        from_dt = _REGISTERS_PER_DATA_TYPE.get(self.data_type or "")
        if from_dt is not None:
            return from_dt
        return int(self.quantity or 1)

    def _inherit_from_scan_group(self):
        """Scan group set ise connection/slave_id/function'ı gruptan miras al.

        Kullanıcı bu üç alanı tekrar girmek zorunda değil — scan_group zaten
        bunları tanımlıyor. Override edilmemişse (None ise) gruptan dolar;
        mevcut değer scan_group'unkiyle çakışıyorsa **scan_group otoritedir**
        ve sensör buna göre düzeltilir (UI'da görünmeyen alanlardan gelen
        eski/yanlış değerleri sessizce normalize eder).
        """
        if not self.scan_group_id:
            return
        sg = ScanGroup.objects.only(
            "connection_id", "slave_id", "function", "start_address", "quantity",
        ).get(pk=self.scan_group_id)
        self.connection_id = sg.connection_id
        self.slave_id = sg.slave_id
        self.function = sg.function

    def full_clean(self, exclude=None, validate_unique=True, validate_constraints=True):
        """Field validation öncesi scan_group inheritance'ı uygular.

        Django sıralaması: clean_fields() → clean() → validate_unique() →
        validate_constraints(). connection_id null=False olduğu için
        clean_fields() inheritance'tan önce patlatıyordu (inline form'da
        connection alanı yok). Inheritance'ı en başa alarak field validation'a
        kadar tüm zorunlu alanlar dolu olur.
        """
        self._inherit_from_scan_group()
        super().full_clean(
            exclude=exclude,
            validate_unique=validate_unique,
            validate_constraints=validate_constraints,
        )

    def save(self, *args, **kwargs):
        """Scan group'a bağlı sensörlerde connection/slave_id/function senkronize.

        full_clean()'i bypass eden code path'ler için (örn. management komutları,
        ORM doğrudan create) güvenlik ağı — inheritance'ı burada da uygular.
        """
        self._inherit_from_scan_group()
        super().save(*args, **kwargs)

    def clean(self):
        """scan_group set ise address sınır doğrulaması.

        connection/slave_id/function inheritance full_clean() başında zaten
        uygulanmış; burada yalnız address range kontrolü kalır.
        """
        if not self.scan_group_id:
            return
        sg = self.scan_group

        if self.address is None:
            raise ValidationError({"address": "Scan group modunda address zorunlu."})
        if self.address < sg.start_address:
            raise ValidationError({"address": (
                f"Scan group start_address={sg.start_address} değerinden küçük olamaz."
            )})
        count = self.registers_used()
        if self.address + count > sg.end_address:
            raise ValidationError({"address": (
                f"Sensör aralığı ({self.address}..{self.address + count}) "
                f"scan group sınırlarını ({sg.start_address}..{sg.end_address}) aşıyor."
            )})


QUALITY_CHOICES = (
    ("good", "Good"),
    ("bad", "Bad"),
    ("uncertain", "Uncertain"),
    ("stale", "Stale"),
    ("substituted", "Substituted"),
    ("manual", "Manual"),
)


class SensorLatest(models.Model):
    """Sensörün en son anlık snapshot'ı — HMI/dashboard hızlı okuma için.

    Sensör başına 1 satır. Reader her başarılı okumada günceller.
    Tarihsel history için bkz. `Reading`.
    """

    sensor = models.OneToOneField(
        Sensor, on_delete=models.CASCADE, related_name="latest",
        verbose_name="Sensör",
    )
    value = models.FloatField(
        blank=True, null=True, default=0,
        verbose_name="Anlık Değer",
        help_text="scale/offset uygulanmış mühendislik değeri",
    )
    status = models.ForeignKey(
        StatusCode, on_delete=models.SET_NULL, blank=True, null=True,
        verbose_name="Status Kodu",
    )
    quality = models.CharField(
        max_length=15, choices=QUALITY_CHOICES, default="good",
        verbose_name="Kalite",
        help_text="Anlık değerin güvenilirlik düzeyi",
    )
    readtime = models.DateTimeField(
        blank=True, null=True, db_index=True,
        verbose_name="Okuma Zamanı",
    )
    last_change_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Son Değişim Zamanı",
        help_text="Değer en son ne zaman bir önceki okumadan farklıydı (deadband/COV için)",
    )
    last_saved_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Son Reading Kayıt Zamanı",
        help_text="Reading tablosuna son insert zamanı. "
                  "Connection.save_interval_sec için kullanılır.",
    )
    last_saved_value = models.FloatField(
        blank=True, null=True,
        verbose_name="Son Kaydedilen Değer",
        help_text="Reading tablosuna en son yazılan değer. save_on_change "
                  "(deadband) karşılaştırmasının referansı — anlık değil son "
                  "KAYIT değeridir (yavaş drift'in birikip tetiklemesi için).",
    )
    last_saved_status = models.ForeignKey(
        StatusCode, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+",
        verbose_name="Son Kaydedilen Status",
        help_text="Reading tablosuna en son yazılan status. save_on_change "
                  "modunda status değişimini de kayıt tetikleyicisi yapar.",
    )
    update_count = models.BigIntegerField(
        default=0, verbose_name="Güncelleme Sayısı",
        help_text="Bu sensör için kaç okuma yapıldı (debug/health monitor)",
    )

    class Meta:
        db_table = "sensor_latest"
        verbose_name_plural = "Sensör Anlık Snapshot"
        ordering = ["sensor"]

    def __str__(self):
        return str(self.sensor) if self.sensor_id else f"Latest-{self.pk}"


class Reading(models.Model):
    """Sensör ölçüm kaydı (tarihsel time-series).

    Her başarılı poll için bir satır. Aggregate sorgular için
    `ReadingFifteenMin` / `ReadingHourly` / `ReadingDaily` tablolarını
    kullan; raw Reading'i taramaktan kaçın.
    """

    ORIGIN_CHOICES = (
        ("polled", "Polled (cihazdan okundu)"),
        ("manual", "Manual (operatör girişi)"),
        ("calculated", "Calculated (hesaplanmış)"),
        ("simulated", "Simulated (rastgele/test)"),
        ("interpolated", "Interpolated (eksik veri dolduruldu)"),
    )

    sensor = models.ForeignKey(
        Sensor, on_delete=models.CASCADE, related_name="readings", verbose_name="Sensör",
    )
    value = models.FloatField(blank=True, null=True, verbose_name="Değer")
    status = models.ForeignKey(StatusCode, on_delete=models.SET_NULL, blank=True, null=True)
    quality = models.CharField(
        max_length=15, choices=QUALITY_CHOICES, default="good",
        verbose_name="Kalite",
    )
    origin = models.CharField(
        max_length=15, choices=ORIGIN_CHOICES, default="polled",
        verbose_name="Veri Kaynağı",
    )
    time_iso = models.DateTimeField(blank=True, null=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "reading"
        verbose_name_plural = "Okumalar"
        ordering = ["-time_iso"]
        indexes = [
            # Sensör başına son N okuma (HMI/grafik en sık sorgu)
            models.Index(fields=["sensor", "-time_iso"], name="reading_sensor_time_idx"),
            # Global zaman penceresi (tüm sensörlerin belirli aralıkta)
            models.Index(fields=["time_iso", "sensor"], name="reading_time_sensor_idx"),
        ]

    def __str__(self):
        return f"{self.sensor} @ {self.time_iso}"


class ReadingAggregateBase(models.Model):
    """Reading aggregation tabloları için ortak şema.

    Her bucket (15dk / saat / gün) için sensör başına 1 satır:
    avg/min/max/count + bad_count (kalite "bad" olan okuma adedi).
    `aggregate_readings` management komutu doldurur.
    """

    sensor = models.ForeignKey(
        Sensor, on_delete=models.CASCADE, related_name="+", verbose_name="Sensör",
    )
    bucket_start = models.DateTimeField(
        db_index=True, verbose_name="Bucket Başlangıcı",
        help_text="Aggregate periyodunun başlangıç zamanı (bucket'a yuvarlanmış)",
    )
    avg_value = models.FloatField(blank=True, null=True, verbose_name="Ortalama")
    min_value = models.FloatField(blank=True, null=True, verbose_name="Min")
    max_value = models.FloatField(blank=True, null=True, verbose_name="Max")
    count = models.IntegerField(default=0, verbose_name="Toplam Okuma")
    bad_count = models.IntegerField(default=0, verbose_name="Bad Kalite Sayısı")
    computed_at = models.DateTimeField(auto_now=True, verbose_name="Hesaplanma Zamanı")

    class Meta:
        abstract = True
        ordering = ["-bucket_start", "sensor"]


class ReadingFifteenMin(ReadingAggregateBase):
    """Sensör başına 15 dakikalık aggregate."""

    class Meta(ReadingAggregateBase.Meta):
        db_table = "reading_15m"
        verbose_name_plural = "15 Dakikalık Aggregate"
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "bucket_start"], name="reading_15m_unique_bucket",
            ),
        ]
        indexes = [
            models.Index(fields=["sensor", "-bucket_start"], name="r15m_sensor_bucket_idx"),
        ]


class ReadingHourly(ReadingAggregateBase):
    """Sensör başına saatlik aggregate."""

    class Meta(ReadingAggregateBase.Meta):
        db_table = "reading_hourly"
        verbose_name_plural = "Saatlik Aggregate"
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "bucket_start"], name="reading_hourly_unique_bucket",
            ),
        ]
        indexes = [
            models.Index(fields=["sensor", "-bucket_start"], name="rhourly_sensor_bucket_idx"),
        ]


class ReadingDaily(ReadingAggregateBase):
    """Sensör başına günlük aggregate."""

    class Meta(ReadingAggregateBase.Meta):
        db_table = "reading_daily"
        verbose_name_plural = "Günlük Aggregate"
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "bucket_start"], name="reading_daily_unique_bucket",
            ),
        ]
        indexes = [
            models.Index(fields=["sensor", "-bucket_start"], name="rdaily_sensor_bucket_idx"),
        ]


class PowerOff(models.Model):
    """İstasyonun elektrik/kapalı kalma aralıkları."""

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="power_offs")
    start_date = models.DateTimeField(blank=True, null=True, verbose_name="Başlangıç Tarihi")
    end_date = models.DateTimeField(blank=True, null=True, verbose_name="Bitiş Tarihi")
    time_iso = models.DateTimeField(auto_now_add=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "power_off"
        verbose_name_plural = "Kapalı Kalma Kayıtları"
        ordering = ["-time_iso"]

    def __str__(self):
        return str(self.time_iso)


class Calibration(models.Model):
    """Sensör kalibrasyon kayıtları."""

    CAL_TYPE = ((0, "Zero"), (1, "Span"), (2, "Multi"))

    sensor = models.ForeignKey(
        Sensor, on_delete=models.CASCADE,
        blank=True, null=True, related_name="calibrations",
    )
    type = models.IntegerField(choices=CAL_TYPE, blank=True, null=True, verbose_name="Kalibrasyon Tipi")
    period = models.IntegerField(default=60, blank=True, null=True, verbose_name="Kalibrasyon Periyodu")
    cal_ref = models.FloatField(blank=True, null=True, verbose_name="Referans Değer")
    cal_average = models.FloatField(blank=True, null=True, verbose_name="Ortalama Değer")
    cal_std = models.FloatField(blank=True, null=True, verbose_name="Standart Sapma")
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, blank=True, null=True)
    is_valid = models.BooleanField(blank=True, null=True, verbose_name="Geçerli mi ?")
    time_iso = models.DateTimeField(auto_now_add=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "calibration"
        verbose_name_plural = "Kalibrasyon Kayıtları"
        ordering = ["-time_iso"]

    def __str__(self):
        return str(self.time_iso)


class LogType(models.Model):
    """Log sınıflandırma lookup'u."""

    name = models.CharField(max_length=200, blank=True, null=True, verbose_name="Log Tipi Adı")

    class Meta:
        db_table = "log_type"
        verbose_name_plural = "Log Tipleri"

    def __str__(self):
        return self.name or f"LogType-{self.pk}"


class SystemLog(models.Model):
    """Sistem olay kaydı."""

    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, null=True, blank=True, related_name="system_logs",
    )
    type = models.ForeignKey(LogType, on_delete=models.CASCADE)
    description = models.CharField(max_length=1000, blank=True, null=True, verbose_name="Açıklama")
    time_iso = models.DateTimeField(auto_now_add=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "system_log"
        verbose_name_plural = "Sistem Log Kayıtları"
        ordering = ["-time_iso"]

    def __str__(self):
        return str(self.time_iso)


class ApiLog(models.Model):
    """Gelen ve giden API isteklerinin tek tablodaki birleşik kaydı.

    `direction='in'`: dış client'tan bizim server'a gelen istek (middleware
    tarafından otomatik doldurulur).
    `direction='out'`: bizim kodumuzdan dış servise yapılan çağrı
    (`api.api_logging.log_outbound_call` veya `record_outbound_call` ile
    manuel doldurulur).

    Body alanları truncate ve hassas veri (Authorization header, password
    gibi key'ler) maskelenmiş olarak tutulur.
    """

    DIRECTION_CHOICES = (
        ("in", "Gelen (Inbound)"),
        ("out", "Giden (Outbound)"),
    )

    # ---- Ortak alanlar ----
    direction = models.CharField(
        max_length=3, choices=DIRECTION_CHOICES, db_index=True,
        verbose_name="Yön",
    )
    method = models.CharField(max_length=10, db_index=True, verbose_name="HTTP Method")
    url = models.CharField(
        max_length=2048, db_index=True,
        verbose_name="URL",
        help_text="Inbound: request.path; Outbound: tam URL",
    )
    query_string = models.TextField(blank=True, default="", verbose_name="Query String")
    request_headers = models.TextField(
        blank=True, default="",
        verbose_name="Request Headers",
        help_text="JSON-serialized; Authorization/Cookie/X-API-Key maskeli",
    )
    request_body = models.TextField(blank=True, default="", verbose_name="Request Body")
    response_status = models.IntegerField(
        blank=True, null=True, db_index=True, verbose_name="Response Status",
    )
    response_body = models.TextField(blank=True, default="", verbose_name="Response Body")
    duration_ms = models.IntegerField(blank=True, null=True, verbose_name="Süre (ms)")
    error_message = models.CharField(
        max_length=1000, blank=True, default="", verbose_name="Hata Mesajı",
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name="Kayıt Zamanı",
    )

    # ---- Inbound-özel alanlar (giden için boş) ----
    remote_ip = models.GenericIPAddressField(
        blank=True, null=True, verbose_name="Client IP",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        blank=True, null=True, related_name="api_logs",
        verbose_name="Kullanıcı",
    )
    user_agent = models.CharField(
        max_length=500, blank=True, default="", verbose_name="User-Agent",
    )

    # ---- Outbound-özel alanlar (gelen için boş) ----
    target_host = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Hedef Host",
        help_text="Giden çağrıda hedef hostname (filtre için)",
    )
    source_component = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name="Kaynak Modül",
        help_text="Çağrıyı yapan iç bileşen (örn. bakanlik_uploader)",
    )
    retry_count = models.IntegerField(
        blank=True, null=True,
        verbose_name="Deneme No",
        help_text="0 = ilk deneme, >0 = retry",
    )

    class Meta:
        db_table = "api_log"
        verbose_name_plural = "API Log Kayıtları"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="apilog_created_idx"),
            models.Index(fields=["direction", "-created_at"], name="apilog_dir_created_idx"),
            models.Index(fields=["url", "response_status"], name="apilog_url_status_idx"),
        ]

    def __str__(self):
        return f"[{self.direction}] {self.method} {self.url} → {self.response_status}"


class RequestType(models.Model):
    """Çıkış / numune / alarm talebinin sınıflandırması lookup'u."""

    code = models.CharField(
        max_length=30, unique=True,
        verbose_name="Kod", help_text="Makine-okur kod (örn. 'operator', 'auto_scenario')",
    )
    name = models.CharField(
        max_length=100, verbose_name="Ad", help_text="İnsan-okur ad",
    )

    class Meta:
        db_table = "request_type"
        verbose_name_plural = "Talep Tipleri"
        ordering = ["code"]

    def __str__(self):
        return self.name


class Command(models.Model):
    """Bir sensöre (genellikle analog/dijital output) yazma / aksiyon tetikleme komutu.

    Dış API, operatör, zamanlayıcı veya kural motorundan tetiklenebilir.
    Tam bir durum makinesi yaşar: pending → queued → executing →
    completed / failed / timeout / expired / cancelled. Bir command
    executor worker'ı (bu repo dışında) pending komutları sırayla alır,
    sensör+bağlantı protokolüne göre Modbus/ASCII write uygular,
    sonucu günceller.
    """

    STATUS_CHOICES = (
        ("pending", "Beklemede"),
        ("queued", "Kuyruğa alındı"),
        ("executing", "Yürütülüyor"),
        ("completed", "Tamamlandı"),
        ("failed", "Başarısız"),
        ("timeout", "Timeout"),
        ("expired", "Süresi doldu"),
        ("cancelled", "İptal"),
    )

    VALUE_TYPE_CHOICES = (
        ("bool", "Bool (coil on/off)"),
        ("int", "Integer"),
        ("float", "Float"),
        ("string", "ASCII metin"),
    )

    SOURCE_CHOICES = (
        ("api", "Dış API"),
        ("operator", "Operatör"),
        ("scheduler", "Zamanlayıcı"),
        ("rule", "Kural Motoru"),
        ("system", "Sistem"),
    )

    # ---- Hedef ----
    sensor = models.ForeignKey(
        Sensor, on_delete=models.CASCADE, related_name="commands",
        verbose_name="Hedef Sensör",
    )

    # ---- Değer (tipli; ya value ya value_text doldurulur) ----
    value_type = models.CharField(
        max_length=10, choices=VALUE_TYPE_CHOICES, default="int",
        verbose_name="Değer Tipi",
    )
    value = models.FloatField(
        blank=True, null=True,
        verbose_name="Sayısal Değer",
        help_text="bool/int/float tipleri için (bool: 0/1)",
    )
    value_text = models.CharField(
        max_length=500, blank=True, null=True,
        verbose_name="Metin Değeri",
        help_text="value_type='string' için gönderilecek ASCII komut",
    )

    # ---- Durum makinesi ----
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default="pending",
        verbose_name="Durum",
    )
    attempt_count = models.IntegerField(default=0, verbose_name="Deneme Sayısı")
    max_attempts = models.IntegerField(default=3, verbose_name="Max Deneme")
    priority = models.IntegerField(
        default=100,
        verbose_name="Öncelik",
        help_text="Düşük sayı = yüksek öncelik (Unix nice stili). Örn: acil=10, normal=100, düşük=1000",
    )

    # ---- Yaşam döngüsü ----
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturulma")
    scheduled_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Planlı Zaman",
        help_text="Null → hemen çalıştırılır; değer → bu zamandan önce çalıştırılmaz",
    )
    expires_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Geçerlilik Süresi",
        help_text="Bu zamandan sonra çalıştırılmamışsa 'expired' olarak işaretlenir",
    )
    executed_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Yürütme Zamanı",
        help_text="Yazma komutunun cihaza gönderildiği zaman",
    )
    completed_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Tamamlanma Zamanı",
        help_text="Başarı/başarısızlığın kesinleştiği zaman",
    )

    # ---- Denetim izi / ilişkilendirme ----
    source = models.CharField(
        max_length=15, choices=SOURCE_CHOICES, default="api",
        verbose_name="Kaynak",
    )
    request_type = models.ForeignKey(
        RequestType, on_delete=models.SET_NULL,
        blank=True, null=True, verbose_name="Talep Tipi",
        help_text="Alan-özel sınıflandırma (opsiyonel; örn. ministry_sample)",
    )
    requested_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        blank=True, null=True, related_name="commands",
        verbose_name="Talep Eden Kullanıcı",
    )
    correlation_id = models.CharField(
        max_length=100, blank=True, null=True,
        verbose_name="Correlation ID",
        help_text="Dış sistem trace / log ilişkilendirme kimliği",
    )
    idempotency_key = models.CharField(
        max_length=100, blank=True, null=True, unique=True,
        verbose_name="Idempotency Key",
        help_text="Aynı key ile gelen ikinci istek yeni komut oluşturmaz",
    )

    # ---- Sonuç ----
    error_message = models.CharField(
        max_length=500, blank=True, default="",
        verbose_name="Hata Mesajı",
    )
    response_data = models.JSONField(
        blank=True, null=True,
        verbose_name="Cihaz Yanıtı",
        help_text="Cihaz yazma sonrası döndürdüğü değer/metadata (varsa)",
    )

    class Meta:
        db_table = "command"
        verbose_name_plural = "Komutlar"
        ordering = ["-priority", "created_at"]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"], name="cmd_queue_idx"),
        ]

    def __str__(self):
        return f"{self.sensor} ← {self.value if self.value_type != 'string' else self.value_text} [{self.status}]"
