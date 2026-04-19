"""
SCADA çekirdek veri modelleri.

Bu uygulama (`api`) her türlü endüstriyel izleme / SCADA senaryosunda
kullanılmaya uygun, alan-özel kavramlardan arındırılmış temel modelleri
içerir. Atıksu (SAIS), Envisoft gibi alan-özel uzantılar ayrı bir
uygulamada (`sais_domain`) tanımlıdır.
"""
from django.db import models

from users.models import CustomUser


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
        max_length=20, choices=PROTOCOL_CHOICES, default="modbus_tcp",
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
        default=10, verbose_name="Varsayılan Okuma Periyodu (sn)",
        help_text="Bu bağlantıdaki sensörlerin varsayılan okuma aralığı",
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

    # ---- Runtime durumu (reader tarafından güncellenir) ----
    last_connected_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Son Bağlantı Zamanı",
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
        ("int16", "Int16 (signed)"),
        ("uint16", "UInt16 (unsigned)"),
        ("int32", "Int32 (signed)"),
        ("uint32", "UInt32 (unsigned)"),
        ("int64", "Int64 (signed)"),
        ("uint64", "UInt64 (unsigned)"),
        ("float32", "Float32 (IEEE-754)"),
        ("float64", "Float64 (IEEE-754)"),
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

    class Meta:
        db_table = "sensor"
        verbose_name_plural = "Sensörler"
        ordering = ["id"]

    def __str__(self):
        if self.parameter_id and self.parameter.parameter_name:
            return self.parameter.parameter_name
        return f"Sensor-{self.pk}"


class SensorLatest(models.Model):
    """Sensörün en son anlık değeri (per-sensor 1 kayıt)."""

    sensor = models.OneToOneField(
        Sensor, on_delete=models.CASCADE, related_name="latest",
    )
    instant = models.FloatField(default=0, blank=True, null=True, verbose_name="Anlık Değer")
    status = models.ForeignKey(StatusCode, on_delete=models.SET_NULL, blank=True, null=True)
    readtime = models.DateTimeField(blank=True, null=True, verbose_name="Okuma Zamanı")
    factorA = models.FloatField(
        default=1, blank=True, null=True,
        verbose_name="Kalibrasyon Faktörü (A)", help_text="result = ax+b",
    )
    factorB = models.FloatField(
        default=0, blank=True, null=True,
        verbose_name="Kalibrasyon Faktörü (B)", help_text="result = ax+b",
    )
    send_status = models.BooleanField(default=False, verbose_name="Status Gönderilsin mi ?")
    is_random = models.BooleanField(default=False, verbose_name="Rastgele üret")

    class Meta:
        db_table = "sensor_latest"
        verbose_name_plural = "Sensör Anlık Veriler"
        ordering = ["sensor"]

    def __str__(self):
        return str(self.sensor) if self.sensor_id else f"Latest-{self.pk}"


class Reading(models.Model):
    """Sensör ölçüm kaydı (tarihsel veri)."""

    sensor = models.ForeignKey(
        Sensor, on_delete=models.CASCADE, related_name="readings", verbose_name="Sensör",
    )
    value = models.FloatField(blank=True, null=True, verbose_name="Değer")
    status = models.ForeignKey(StatusCode, on_delete=models.SET_NULL, blank=True, null=True)
    time_iso = models.DateTimeField(blank=True, null=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "reading"
        verbose_name_plural = "Okumalar"
        ordering = ["-time_iso"]

    def __str__(self):
        return f"{self.sensor} @ {self.time_iso}"


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
    """Dış API çağrıları için istek/yanıt kaydı."""

    type = models.ForeignKey(LogType, on_delete=models.CASCADE)
    url = models.CharField(max_length=250, blank=True, null=True, verbose_name="Request Url")
    data = models.CharField(max_length=2000, blank=True, null=True, verbose_name="Request Data")
    header = models.CharField(max_length=2000, blank=True, null=True, verbose_name="Request Header")
    param = models.CharField(max_length=2000, blank=True, null=True, verbose_name="Request Param")
    token = models.CharField(max_length=250, blank=True, null=True, verbose_name="Token")
    response = models.CharField(max_length=2000, blank=True, null=True, verbose_name="Response")
    status = models.IntegerField(blank=True, null=True, verbose_name="Status Kod")
    time_iso = models.DateTimeField(auto_now_add=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "api_log"
        verbose_name_plural = "Api Log Kayıtları"
        ordering = ["-time_iso"]

    def __str__(self):
        return str(self.type)


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


class OutputRequest(models.Model):
    """Sensöre yönelik dijital-out / aksiyon tetikleme talebi."""

    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name="output_requests")
    value = models.IntegerField(verbose_name="Değer")
    request_type = models.ForeignKey(
        RequestType, on_delete=models.SET_NULL,
        blank=True, null=True, verbose_name="Talep Tipi",
    )
    is_completed = models.BooleanField(default=False)
    request_code = models.CharField(max_length=250, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kayıt Tarihi")

    class Meta:
        db_table = "output_request"
        verbose_name_plural = "Dijital Out Talepleri"
        ordering = ["-created_at"]

    def __str__(self):
        return str(self.created_at)
