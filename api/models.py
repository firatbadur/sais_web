"""
SCADA çekirdek veri modelleri.

Bu uygulama (`api`) her türlü endüstriyel izleme / SCADA senaryosunda
kullanılmaya uygun, alan-özel kavramlardan arındırılmış temel modelleri
içerir. Atıksu (SAIS), Envisoft gibi alan-özel uzantılar ayrı bir
uygulamada (`sais_domain`) tanımlıdır.
"""
import datetime

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
        verbose_name="Ad", help_text="Tesis tipi adı",
    )
    description = models.CharField(
        max_length=250, blank=True, default="",
        verbose_name="Açıklama",
    )

    class Meta:
        db_table = "station_type"
        verbose_name_plural = "Tesis Tipleri"
        ordering = ["code"]

    def __str__(self):
        return self.name


class Station(models.Model):
    """İzleme istasyonu (saha)."""

    name = models.CharField(
        max_length=100, verbose_name="Tesis Adı", help_text="Tesis Adı",
    )
    station_type = models.ForeignKey(
        StationType, on_delete=models.SET_NULL,
        blank=False, null=True,
        verbose_name="Tesis Tipi",
        help_text="Tesisin ölçüm sistemi tipi (zorunlu)",
    )
    address = models.CharField(
        max_length=250, blank=True, default="",
        verbose_name="Tesis Adresi", help_text="Tesis Adresi",
    )
    company = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name="Kurum Adı", help_text="Kurum Adı",
    )
    active = models.BooleanField(
        default=True, verbose_name="Aktif mi?", help_text="Aktif mi?",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "station"
        verbose_name_plural = "Tesis Bilgileri"
        ordering = ["created_at"]

    def __str__(self):
        return self.name


class StationAuthority(models.Model):
    """İstasyon ↔ kullanıcı yetki eşlemesi.

    Bir istasyonun alarm/bildirimlerinden sorumlu kullanıcıları tanımlar.
    Alarm motoru (Faz 2) alıcıları buradan (`notify=True`) + kullanıcının
    `sms_enabled`/`email_enabled` tercihlerinden çözer.
    """

    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="authorities",
        verbose_name="Tesis",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="station_authorities",
        verbose_name="Kullanıcı",
    )
    notify = models.BooleanField(default=True, verbose_name="Bildirim Gönder")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "station_authority"
        unique_together = (("station", "user"),)
        ordering = ["station_id", "user_id"]
        verbose_name = "Tesis Yetkilisi"
        verbose_name_plural = "Tesis Yetkilileri"

    def __str__(self):
        return f"{self.station} ← {self.user}"


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
        verbose_name="Tesis",
    )
    name = models.CharField(
        max_length=100, default="",
        verbose_name="Bağlantı Adı",
        help_text="Bu tesisteki bağlantının özgün adı",
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

    @property
    def display_name(self):
        """Kullanıcıya (dashboard) gösterilecek ad: okunabilir Türkçe metin
        (`parameter_txt`) varsa onu, yoksa kod adını (`parameter_name`) döner.

        `parameter_name` Bakanlık/Envisoft kanal kodudur ("CozunmusOksijen");
        UI'da `parameter_txt` ("Çözünmüş Oksijen") tercih edilir.
        """
        return self.parameter_txt or self.parameter_name or f"Parameter {self.pk}"


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
    tag = models.CharField(
        max_length=80, blank=True, default="", db_index=True,
        verbose_name="SCADA Etiketi (Tag)",
        help_text="Mimik/HMI bağlama için benzersiz etiket. Boş bırakılırsa "
                  "kayıtta parametre kodundan otomatik üretilir (gerçek SCADA'daki gibi).",
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
        blank=True, null=True, default=2,
        verbose_name="Ondalık Hassasiyet",
        help_text="Kayıt öncesi kaç basamağa yuvarlanacak. Varsayılan 2. "
                  "Boş bırakılırsa yuvarlama yapılmaz. "
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
    display_order = models.IntegerField(
        default=0, db_index=True, verbose_name="Görüntüleme Sırası",
        help_text="Anasayfa canlı tablolarında artan sıra; eşitse alt kıstaslar uygulanır.",
    )
    dashboard_hidden = models.BooleanField(
        default=False, verbose_name="Dashboard'da Gizle",
        help_text="True ise bu sensör dashboard anlık tablolarında (analog/dijital) "
                  "gösterilmez. Polling, kayıt ve raporlar etkilenmez — yalnız görünürlük.",
    )
    is_simulated = models.BooleanField(
        default=False, verbose_name="Simülasyon Modu",
        help_text="True ise reader cihazdan değer okumak yerine rastgele üretir (test/demo için)",
    )
    sim_min = models.FloatField(
        blank=True, null=True, verbose_name="Simülasyon Min",
        help_text="Simülasyon modunda üretilecek rastgele değerin alt sınırı. "
                  "Boşsa parametrenin min_range değeri (o da yoksa 0) kullanılır.",
    )
    sim_max = models.FloatField(
        blank=True, null=True, verbose_name="Simülasyon Max",
        help_text="Simülasyon modunda üretilecek rastgele değerin üst sınırı. "
                  "Boşsa parametrenin max_range değeri (o da yoksa 100) kullanılır.",
    )
    report_status = models.BooleanField(
        default=False, verbose_name="Status Raporla",
        help_text="True ise bu sensörün status'ü dış sisteme (Bakanlık) gönderilir",
    )

    # ---- Değişimde kaydet (Change-of-Value / deadband) ----
    save_on_change = models.BooleanField(
        null=True, blank=True, default=None,
        verbose_name="Sadece Değişimde Kaydet",
        help_text=(
            "True ise Reading sadece değer değiştiğinde (analogda deadband'i "
            "aşan değişim, dijitalde herhangi bir değişim) ya da status "
            "değiştiğinde yazılır. SensorLatest snapshot'ı yine her okumada "
            "güncellenir (HMI taze kalır). Connection.save_interval_sec minimum "
            "aralık olarak yine uygulanır. Boş (None) bırakılırsa oluşturmada "
            "tipe göre varsayılan uygulanır: dijital (DI/DO) için açık, analog "
            "için kapalı. Açıkça True/False seçilirse o değer korunur."
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

        # Dijital I/O (DI=2 / DO=3) bit-tabanlı bir grupta (coils=1 / discrete=2)
        # ise data_type otomatik "bool" olur: okuma tarafı bit decode eder,
        # yazma tarafı (dijital output Start/Stop) write_coil yapar. Aksi halde
        # data_type=int16 kalıp write_register'a düşer ve coil'e yazmaz.
        # Register tabanlı gruplarda (holding=3 / input=4) kullanıcının
        # data_type'ına (bit/uint16 vb.) dokunulmaz.
        if self.sensor_type in (2, 3) and sg.function in (1, 2):
            self.data_type = "bool"

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

        save_on_change belirtilmemişse (None) oluşturmada tipe göre varsayılan
        uygulanır: yeni dijital sensör (DI=2 / DO=3) için açık (dijital sensörler
        doğası gereği COV/değişimde-kaydet mantığına uyar), analog için kapalı.
        Açıkça True/False verilmişse korunur — yalnız None çözülür.
        """
        self._inherit_from_scan_group()
        if self._state.adding and self.save_on_change is None:
            self.save_on_change = self.sensor_type in (2, 3)
        super().save(*args, **kwargs)
        # Otomatik SCADA etiketi — gerçek bir SCADA'da her tag benzersizdir.
        # Boşsa parametre kodundan üretilip yazılır (pk gerektiği için save sonrası).
        if not self.tag:
            self.tag = self._generate_tag()
            super().save(update_fields=["tag"])

    def _generate_tag(self) -> str:
        """Parametre kodundan benzersiz, SCADA-uyumlu bir tag türetir.

        İlk sensör için kod adının kendisi (ör. 'pH', 'Pompa1'); aynı kod
        başka bir sensörde kullanılıyorsa pk eklenir ('pH_42').
        """
        import re
        raw = ""
        if self.parameter_id:
            raw = self.parameter.parameter_name or self.parameter.parameter_txt or ""
        base = re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_") or f"TAG{self.pk}"
        clash = type(self).objects.filter(tag=base).exclude(pk=self.pk).exists()
        return base if not clash else f"{base}_{self.pk}"

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
    last_digital_state = models.BooleanField(
        blank=True, null=True,
        verbose_name="Son Dijital Durum",
        help_text="Dijital sensörün son GEÇERLİ okumadaki ham aktif/pasif durumu "
                  "(iletişim/decode hatası okumaları hariç). "
                  "last_digital_change_at karşılaştırma referansı.",
    )
    last_digital_change_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="Son Durum Değişim Zamanı",
        help_text="Dijital sensörün aktif/pasif durumu en son ne zaman değişti. "
                  "İletişim hatası kaynaklı değişimler (value None) sayılmaz — "
                  "anasayfa Dijital Kanallar 'Son Değişim' sütununu besler.",
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


class ReadingFiveMin(ReadingAggregateBase):
    """Sensör başına 5 dakikalık aggregate.

    Numune senaryosu motoru gibi kısa-pencere ortalama tüketicileri için —
    poll sıklığından bağımsız, oturmuş 5 dakikalık bucket avg/min/max.
    """

    class Meta(ReadingAggregateBase.Meta):
        db_table = "reading_five_min"
        verbose_name_plural = "5 Dakikalık Aggregate"
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "bucket_start"], name="reading_5m_unique_bucket",
            ),
        ]
        indexes = [
            models.Index(fields=["sensor", "-bucket_start"], name="r5m_sensor_bucket_idx"),
        ]


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
    """Sistem olay (event) kaydı — gerçek SCADA audit trail.

    Tüm kullanıcı hareketleri (giriş/çıkış, manuel komut, yapılandırma
    değişikliği), dijital giriş/çıkış tetiklemeleri ve sistem olayları tek bir
    merkezi giriş noktasından (`api.events.log_event`) buraya yazılır. Kim
    (`user`/`username`), nereden (`ip_address`), ne kadar önemli (`severity`)
    bilgileri olayla birlikte saklanır.
    """

    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_CRITICAL = "critical"
    SEVERITY_CHOICES = [
        (SEVERITY_INFO, "Bilgi"),
        (SEVERITY_WARNING, "Uyarı"),
        (SEVERITY_CRITICAL, "Kritik"),
    ]

    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, null=True, blank=True, related_name="system_logs",
    )
    type = models.ForeignKey(LogType, on_delete=models.CASCADE)
    severity = models.CharField(
        max_length=10, choices=SEVERITY_CHOICES, default=SEVERITY_INFO,
        db_index=True, verbose_name="Önem Derecesi",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="system_logs", verbose_name="Kullanıcı",
    )
    username = models.CharField(
        max_length=150, blank=True, null=True, verbose_name="Kullanıcı Adı",
        help_text="Olay anındaki kullanıcı adı (kullanıcı silinse/başarısız girişte de korunur).",
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, verbose_name="IP Adresi",
    )
    description = models.CharField(max_length=1000, blank=True, null=True, verbose_name="Açıklama")
    time_iso = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Kayıt Tarihi")

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


# ---------------------------------------------------------------------------
# Veritabanı yedekleme / geri yükleme
# ---------------------------------------------------------------------------

BACKUP_TIERS = (
    ("daily", "Günlük"),
    ("weekly", "Haftalık"),
    ("monthly", "Aylık"),
    ("yearly", "Yıllık"),
    ("manual", "Manuel"),
)

# Tier başına varsayılan saklama adedi (GFS). BackupPolicy ilk yaratılırken kullanılır.
BACKUP_DEFAULT_RETENTION = {
    "daily": 7,
    "weekly": 4,
    "monthly": 12,
    "yearly": 5,
    "manual": 10,
}

# Tier başına varsayılan aktiflik. Varsayılan ayarda yalnızca aylık yedek alınır
# (ayda 1 defa); kullanıcı dashboard'dan diğer periyotları açabilir.
BACKUP_DEFAULT_ENABLED = {
    "daily": False,
    "weekly": False,
    "monthly": True,
    "yearly": False,
    "manual": False,
}


class BackupPolicy(models.Model):
    """Tier başına yedekleme politikası — dashboard'dan yönetilir.

    Celery beat task'ları her tier için sabit cron'da tetiklenir ama yedek alıp
    almayacağına bu tablodaki `enabled` karar verir (tek doğruluk kaynağı). `retention`
    o tier'da kaç adet başarılı yedek dosyasının saklanacağını belirler.
    """

    tier = models.CharField(
        max_length=10, choices=BACKUP_TIERS, unique=True,
        verbose_name="Periyot",
    )
    enabled = models.BooleanField(
        default=True, verbose_name="Aktif",
        help_text="Kapalıysa bu periyotta otomatik yedek alınmaz.",
    )
    retention = models.PositiveIntegerField(
        default=7, verbose_name="Saklanacak Adet",
        help_text="Bu periyotta tutulacak yedek dosyası sayısı; fazlası silinir.",
    )

    class Meta:
        db_table = "backup_policy"
        verbose_name = "Yedekleme Politikası"
        verbose_name_plural = "Yedekleme Politikaları"
        ordering = ["tier"]

    def __str__(self):
        return f"{self.get_tier_display()} (retention={self.retention}, {'açık' if self.enabled else 'kapalı'})"

    @classmethod
    def ensure_defaults(cls):
        """Eksik tier'lar için varsayılan politika satırlarını idempotent yaratır."""
        for tier, _label in BACKUP_TIERS:
            cls.objects.get_or_create(
                tier=tier,
                defaults={
                    "enabled": BACKUP_DEFAULT_ENABLED.get(tier, False),
                    "retention": BACKUP_DEFAULT_RETENTION.get(tier, 7),
                },
            )


class DatabaseBackup(models.Model):
    """Alınmış bir veritabanı yedeğinin (.dump) kaydı + sürüm damgası.

    `app_version` ve `migration_state`, geri yüklemede şema uyumluluğunu
    (exact / forward / block) hesaplamak için kullanılır.
    """

    STATUS_CHOICES = (
        ("running", "Alınıyor"),
        ("success", "Başarılı"),
        ("failed", "Başarısız"),
    )
    TRIGGER_CHOICES = (
        ("auto", "Otomatik"),
        ("manual", "Manuel"),
    )

    tier = models.CharField(max_length=10, choices=BACKUP_TIERS, verbose_name="Periyot")
    filename = models.CharField(max_length=255, verbose_name="Dosya Adı")
    path = models.CharField(max_length=500, verbose_name="Tam Yol")
    size_bytes = models.BigIntegerField(default=0, verbose_name="Boyut (byte)")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="running", verbose_name="Durum",
    )

    db_name = models.CharField(max_length=128, verbose_name="Veritabanı")
    app_version = models.CharField(
        max_length=50, default="dev", verbose_name="Uygulama Sürümü",
        help_text="Yedek alındığı andaki APP_VERSION.",
    )
    migration_state = models.JSONField(
        blank=True, null=True, verbose_name="Migration Durumu",
        help_text="{app: son_uygulanan_migration} — geri yüklemede şema uyumu için.",
    )

    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Başlangıç")
    finished_at = models.DateTimeField(blank=True, null=True, verbose_name="Bitiş")
    error = models.TextField(blank=True, default="", verbose_name="Hata")

    trigger = models.CharField(
        max_length=10, choices=TRIGGER_CHOICES, default="auto", verbose_name="Tetikleyici",
    )
    triggered_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="database_backups", verbose_name="Tetikleyen Kullanıcı",
    )
    pruned = models.BooleanField(
        default=False, verbose_name="Dosya Silindi",
        help_text="Retention politikası gereği .dump dosyası silindi (kayıt audit için kalır).",
    )

    class Meta:
        db_table = "database_backup"
        verbose_name = "Veritabanı Yedeği"
        verbose_name_plural = "Veritabanı Yedekleri"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["tier", "status", "pruned", "started_at"], name="backup_tier_idx"),
        ]

    def __str__(self):
        return f"{self.filename} [{self.status}]"


class DatabaseRestore(models.Model):
    """Bir geri yükleme (restore) işleminin kaydı — audit + durum takibi."""

    STATUS_CHOICES = (
        ("running", "Yükleniyor"),
        ("success", "Başarılı"),
        ("failed", "Başarısız"),
    )
    COMPATIBILITY_CHOICES = (
        ("exact", "Birebir"),
        ("forward", "İleri Migrate"),
        ("block", "Engellendi"),
        ("forced", "Zorlandı"),
    )

    source_backup = models.ForeignKey(
        DatabaseBackup, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="restores", verbose_name="Kaynak Yedek",
    )
    source_filename = models.CharField(max_length=255, verbose_name="Kaynak Dosya")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="running", verbose_name="Durum",
    )
    compatibility = models.CharField(
        max_length=10, choices=COMPATIBILITY_CHOICES, verbose_name="Uyumluluk",
    )
    ran_migrate = models.BooleanField(default=False, verbose_name="Migrate Çalıştı")
    app_version_at_backup = models.CharField(
        max_length=50, blank=True, default="", verbose_name="Yedek Sürümü",
    )

    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Başlangıç")
    finished_at = models.DateTimeField(blank=True, null=True, verbose_name="Bitiş")
    error = models.TextField(blank=True, default="", verbose_name="Hata")
    triggered_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="database_restores", verbose_name="Tetikleyen Kullanıcı",
    )

    class Meta:
        db_table = "database_restore"
        verbose_name = "Veritabanı Geri Yükleme"
        verbose_name_plural = "Veritabanı Geri Yüklemeler"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.source_filename} → [{self.status}]"


# ---------------------------------------------------------------------------
# Lisanslama (kurulum/saha bazlı, imzalı)
# ---------------------------------------------------------------------------

class License(models.Model):
    """Bu kurulumun lisans durumu — singleton (pk=1), SystemSwitch deseni.

    İmzalı token uzaktan (LICENSE_URL) çekilir, Ed25519 ile doğrulanır ve buraya
    cache'lenir (`raw_token`). Enforcement `valid_until` tarihine dayanır; gate
    anında ağ gerekmez. Detay: api/licensing.py.
    """

    STATUS_CHOICES = (
        ("active", "Aktif"),
        ("expired", "Süresi Doldu"),
        ("invalid", "Geçersiz"),
        ("missing", "Lisans Yok"),
    )

    license_key = models.CharField(max_length=120, blank=True, default="", verbose_name="Lisans Anahtarı")
    customer = models.CharField(max_length=200, blank=True, default="", verbose_name="Müşteri")
    valid_until = models.DateTimeField(blank=True, null=True, verbose_name="Geçerlilik Bitişi")
    issued_at = models.DateTimeField(blank=True, null=True, verbose_name="Veriliş Tarihi")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="missing", verbose_name="Durum",
    )
    signature_valid = models.BooleanField(default=False, verbose_name="İmza Geçerli")
    machine_fingerprint = models.CharField(
        max_length=128, blank=True, default="", verbose_name="Bağlı Makine Parmak İzi",
        help_text="Token bu donanıma kilitliyse (node-lock) buraya yazılır; boşsa "
                  "lisans makineye bağlı değil. Runtime fingerprint ile karşılaştırılır.",
    )
    raw_token = models.JSONField(
        blank=True, null=True, verbose_name="İmzalı Token",
        help_text="Doğrulanmış son token (restart/offline'da yeniden doğrulanır).",
    )
    last_checked_at = models.DateTimeField(blank=True, null=True, verbose_name="Son Kontrol")
    last_check_ok = models.BooleanField(default=False, verbose_name="Son Kontrol Başarılı")
    last_error = models.TextField(blank=True, default="", verbose_name="Son Hata")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="İlk Kayıt")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "license"
        verbose_name = "Lisans"
        verbose_name_plural = "Lisans"

    def __str__(self):
        return f"{self.license_key or '—'} [{self.status}] → {self.valid_until}"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — silinmez

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def days_remaining(self):
        from django.utils import timezone
        if not self.valid_until:
            return None
        return (self.valid_until - timezone.now()).days


class SystemHeartbeat(models.Model):
    """Sistem canlılık damgası — singleton (pk=1), SystemSwitch deseni.

    Çalışan stack `api.tasks.heartbeat_task` ile her dakika `last_seen`'i tazeler.
    PC kapanınca (elektrik kesintisi/sert kapanma dahil) damga durur. Açılışta
    `detect_power_off` komutu son damga ile şimdiki zaman arasındaki boşluğu ölçer;
    `settings.POWEROFF_DETECT_THRESHOLD_MIN` eşiğini aşarsa o aralığı her istasyon
    için bir `PowerOff` kaydına yazar (start_date=son damga, end_date=açılış).
    Graceful sinyale (SIGTERM) bağlı olmadığı için elektrik kesintisini de yakalar.
    """

    last_seen = models.DateTimeField(verbose_name="Son Canlılık")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "system_heartbeat"
        verbose_name = "Sistem Canlılık"
        verbose_name_plural = "Sistem Canlılık"

    def __str__(self):
        return str(self.last_seen)

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — silinmez

    @classmethod
    def load(cls):
        from django.utils import timezone
        obj, _created = cls.objects.get_or_create(
            pk=1, defaults={"last_seen": timezone.now()},
        )
        return obj


class SetupState(models.Model):
    """İlk kurulum sihirbazı durumu — singleton (pk=1), SystemSwitch deseni.

    Yeni bir kurulumda dashboard kullanıcıyı bir kurulum sihirbazıyla karşılar
    (tesis bilgileri + SAIS kabin kaydı + web/sensör ayarlarına yönlendirme).
    Sihirbaz tamamlanınca `completed=True` damgalanır ve bir daha otomatik
    açılmaz. Yönetici header'daki "Kurulum Sihirbazı" butonuyla istediği zaman
    yeniden açabilir (önizleme/yeniden yapılandırma).
    """

    completed = models.BooleanField(
        default=False, verbose_name="Kurulum Tamamlandı",
        help_text="Kurulum sihirbazı en az bir kez tamamlandı mı?",
    )
    completed_at = models.DateTimeField(
        blank=True, null=True, verbose_name="Tamamlanma Zamanı",
    )
    completed_by = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL,
        blank=True, null=True, related_name="+",
        verbose_name="Tamamlayan",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "setup_state"
        verbose_name = "Kurulum Durumu"
        verbose_name_plural = "Kurulum Durumu"

    def __str__(self):
        return "Tamamlandı" if self.completed else "Bekliyor"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — silinmez

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


# ---------------------------------------------------------------------------
# Web erişim ayarları (domain + SSL) — Caddy reverse proxy ile yönetilir
# ---------------------------------------------------------------------------

class WebSettings(models.Model):
    """Bu kurulumun dış erişim ayarları — singleton (pk=1), License deseni.

    Dashboard'dan domain + TLS yönetilir; `api.web_proxy.apply()` bu kayıttan
    paylaşılan volume'deki Caddyfile'ı üretir, Caddy `--watch` ile reload eder.
    Caddyfile tek doğruluk kaynağı DEĞİL — bu kayıt kaynaktır; Caddyfile her
    `apply()` çağrısında yeniden üretilir (idempotent, restart'a dayanıklı).

    `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` burada DEĞİL; fleet-wide `.env`'de
    wildcard (`.envisoft.com.tr`) tutulur → subdomain değişince Django restart
    gerekmez. Detay: api/web_proxy.py.
    """

    TLS_LETSENCRYPT = "letsencrypt"
    TLS_MANUAL = "manual"
    TLS_INTERNAL = "internal"
    TLS_MODE_CHOICES = (
        (TLS_LETSENCRYPT, "Let's Encrypt (otomatik)"),
        (TLS_MANUAL, "Manuel Sertifika (PEM/PFX)"),
        (TLS_INTERNAL, "Self-Signed (LAN/test)"),
    )

    enabled = models.BooleanField(
        default=False, verbose_name="Etkin",
        help_text="Kapalıyken Caddy güvenli internal (self-signed) fallback'e düşer.",
    )
    domain = models.CharField(
        max_length=253, blank=True, default="", verbose_name="Domain",
        help_text="Örn. sais-tesis1.envisoft.com.tr (DNS A kaydı + 443 yönlendirme gerekir).",
    )
    tls_mode = models.CharField(
        max_length=12, choices=TLS_MODE_CHOICES, default=TLS_INTERNAL,
        verbose_name="TLS Modu",
    )
    http_redirect = models.BooleanField(
        default=True, verbose_name="HTTP→HTTPS Yönlendir",
    )
    letsencrypt_email = models.EmailField(
        blank=True, default="", verbose_name="Let's Encrypt E-postası",
        help_text="ACME bildirimleri için; letsencrypt modunda zorunlu.",
    )

    # Manuel sertifika (DB = audit + source; dosya kopyası Caddy için yazılır)
    manual_cert_pem = models.TextField(
        blank=True, default="", verbose_name="Sertifika PEM (zincir dahil)",
    )
    manual_key_pem = models.TextField(
        blank=True, default="", verbose_name="Özel Anahtar PEM (şifresiz)",
    )
    manual_cert_uploaded_at = models.DateTimeField(
        blank=True, null=True, verbose_name="Sertifika Yükleme Tarihi",
    )
    manual_cert_subject = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Sertifika Sahibi (CN)",
    )
    manual_cert_not_after = models.DateTimeField(
        blank=True, null=True, verbose_name="Sertifika Bitiş Tarihi",
    )

    # Render durumu
    last_rendered_at = models.DateTimeField(
        blank=True, null=True, verbose_name="Son Üretim",
    )
    last_render_error = models.TextField(
        blank=True, default="", verbose_name="Son Üretim Hatası",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")
    updated_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Güncelleyen Kullanıcı",
    )

    class Meta:
        db_table = "web_settings"
        verbose_name = "Web Erişim Ayarı"
        verbose_name_plural = "Web Erişim Ayarları"

    def __str__(self):
        return f"{self.domain or '—'} [{self.tls_mode}]"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — silinmez

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def clean(self):
        """tls_mode'a göre koşullu zorunluluk."""
        from django.core.exceptions import ValidationError
        errors = {}
        if self.enabled and not self.domain.strip():
            errors["domain"] = "Etkin web erişimi için domain zorunludur."
        if self.tls_mode == self.TLS_LETSENCRYPT:
            if not self.domain.strip():
                errors["domain"] = "Let's Encrypt için domain zorunludur."
            if not self.letsencrypt_email.strip():
                errors["letsencrypt_email"] = "Let's Encrypt için e-posta zorunludur."
        elif self.tls_mode == self.TLS_MANUAL:
            if not self.manual_cert_pem.strip() or not self.manual_key_pem.strip():
                errors["tls_mode"] = "Manuel mod için önce sertifika + anahtar yükleyin."
        if errors:
            raise ValidationError(errors)

    def cert_days_remaining(self):
        from django.utils import timezone
        if not self.manual_cert_not_after:
            return None
        return (self.manual_cert_not_after - timezone.now()).days


class PublicIpRecord(models.Model):
    """Sunucunun dış (public) IP adresi geçmişi.

    Saha çoğunlukla dinamik IP'li bir hatta bağlı; public IP değişince DNS A
    kaydı / port yönlendirmesi bozulur. Operatörün "şu an public IP neydi"
    sorusuna yanıt verebilmek ve değişimi izleyebilmek için her **farklı** IP
    yeni bir satır olarak kaydedilir; aynı IP tekrar görüldükçe yalnız
    `last_seen` tazelenir. `is_current` en son görülen IP'yi işaretler.
    """

    ip_address = models.GenericIPAddressField(verbose_name="Public IP")
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name="İlk görülme")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="Son görülme")
    is_current = models.BooleanField(default=True, verbose_name="Güncel")

    class Meta:
        db_table = "public_ip_record"
        verbose_name = "Public IP Kaydı"
        verbose_name_plural = "Public IP Kayıtları"
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.ip_address} ({'güncel' if self.is_current else 'eski'})"

    @classmethod
    def record(cls, ip):
        """Verilen public IP'yi kaydet. Aynıysa `last_seen` tazelenir, farklıysa
        eski 'güncel' kayıt(lar) pasifleştirilip yeni satır açılır. Boş/None ip
        görmezden gelinir. Kaydedilen (veya güncellenen) satırı döndürür."""
        ip = (ip or "").strip()
        if not ip:
            return None
        current = cls.objects.filter(is_current=True).order_by("-last_seen").first()
        if current and current.ip_address == ip:
            current.save(update_fields=["last_seen"])  # auto_now -> last_seen tazelenir
            return current
        cls.objects.filter(is_current=True).update(is_current=False)
        return cls.objects.create(ip_address=ip, is_current=True)


class NotificationSettings(models.Model):
    """SMS (NetGSM) + e-posta (SMTP) gönderim ayarları — singleton (pk=1).

    Admin panelden / dashboard'dan yönetilir. Default'lar eski yazılımın
    değerleridir; ilk `load()`'da bu değerlerle oluşturulur.
    """

    # ---- E-posta (dinamik SMTP) ----
    email_enabled = models.BooleanField(default=False, verbose_name="E-posta Etkin")
    smtp_host = models.CharField(max_length=255, default="smtp.gmail.com", verbose_name="SMTP Sunucu")
    smtp_port = models.IntegerField(default=587, verbose_name="SMTP Port")
    smtp_use_tls = models.BooleanField(default=True, verbose_name="STARTTLS")
    smtp_user = models.CharField(max_length=255, default="saisalarm57@gmail.com", verbose_name="SMTP Kullanıcı")
    smtp_password = models.CharField(max_length=255, default="kyaqokitoascdyhq", verbose_name="SMTP Şifre")
    mail_from = models.CharField(max_length=255, default="saisalarm57@gmail.com", verbose_name="Gönderen")
    mail_subject = models.CharField(max_length=255, default="SAİS Mail Bildirim Servisi", verbose_name="Konu")

    # ---- SMS (NetGSM GET/POST API) ----
    sms_enabled = models.BooleanField(default=False, verbose_name="SMS Etkin")
    netgsm_usercode = models.CharField(max_length=64, default="3129119605", verbose_name="NetGSM Kullanıcı Kodu")
    netgsm_password = models.CharField(max_length=64, default="k1-c33mX", verbose_name="NetGSM Şifre")
    netgsm_header = models.CharField(max_length=32, default="ONLNE CEVRE", verbose_name="NetGSM Başlık")
    netgsm_api_url = models.CharField(
        max_length=255, default="https://api.netgsm.com.tr/sms/send/get/", verbose_name="NetGSM API URL",
    )

    # ---- Audit ----
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")
    updated_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Güncelleyen",
    )

    class Meta:
        db_table = "notification_settings"
        verbose_name = "Bildirim Ayarı"
        verbose_name_plural = "Bildirim Ayarları"

    def __str__(self):
        return "Bildirim Ayarları"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — silinmez

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class MessageTemplate(models.Model):
    """Hazır (kayıtlı) bildirim mesajı — test panelinde kart olarak gösterilir."""

    CHANNEL_CHOICES = (("sms", "SMS"), ("email", "E-posta"), ("both", "Her ikisi"))

    title = models.CharField(max_length=120, verbose_name="Başlık")
    body = models.TextField(verbose_name="Mesaj")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default="both", verbose_name="Kanal")
    category = models.CharField(max_length=60, blank=True, default="", verbose_name="Kategori")
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")

    class Meta:
        db_table = "message_template"
        verbose_name = "Hazır Mesaj"
        verbose_name_plural = "Hazır Mesajlar"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class NotificationLog(models.Model):
    """Gönderilen her SMS/e-posta kaydı (test şimdi; alarm Faz 2'de)."""

    CHANNEL_CHOICES = (("sms", "SMS"), ("email", "E-posta"))
    STATUS_CHOICES = (("ok", "Başarılı"), ("fail", "Hatalı"))

    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, verbose_name="Kanal")
    recipient = models.CharField(max_length=255, verbose_name="Alıcı")
    message = models.CharField(max_length=500, blank=True, default="", verbose_name="Mesaj")
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, verbose_name="Durum")
    error = models.CharField(max_length=500, blank=True, default="", verbose_name="Hata")
    kind = models.CharField(max_length=20, default="test", verbose_name="Tür")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Tarih")
    sent_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Gönderen",
    )

    class Meta:
        db_table = "notification_log"
        verbose_name = "Bildirim Kaydı"
        verbose_name_plural = "Bildirim Kayıtları"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.channel} → {self.recipient} ({self.status})"


class AlarmRule(models.Model):
    """Bir istasyon için alarm tanımı (ölçüm / diagnostik / offline).

    Jenerik SCADA alarmı — SAIS'e özel değildir. Periyodik task
    (`api.tasks.run_alarms`) her kuralı `SensorLatest` anlık değerlerine karşı
    değerlendirir; koşul sağlanır ve `period_minutes` süresi dolmuşsa aktif
    kullanıcılara SMS/e-posta gönderir (`api.notifications`), opsiyonel olarak
    bir dijital output'u tetikler (`trigger_output`).
    """

    RULE_ANALOG = "analog"
    RULE_DIGITAL = "digital"
    RULE_OFFLINE = "offline"
    RULE_CHOICES = (
        (RULE_ANALOG, "Ölçüm (Analog Limit)"),
        (RULE_DIGITAL, "Diagnostik (Dijital Kanal)"),
        (RULE_OFFLINE, "Tesis Offline"),
    )

    COND_MIN = "min"
    COND_MAX = "max"
    COND_MINMAX = "minmax"
    COND_CHOICES = (
        (COND_MIN, "Limit Altı"),
        (COND_MAX, "Limit Üstü"),
        (COND_MINMAX, "Limit Altı ve Üstü"),
    )

    PERIOD_CHOICES = (
        (15, "15 Dakika"),
        (30, "30 Dakika"),
        (60, "Saat Başı"),
        (180, "3 Saat"),
        (360, "6 Saat"),
        (720, "12 Saat"),
        (1440, "Günlük"),
    )

    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="alarm_rules",
        verbose_name="Tesis",
    )
    rule_type = models.CharField(
        max_length=10, choices=RULE_CHOICES, default=RULE_ANALOG, verbose_name="Alarm Türü",
    )

    # --- Ölçüm (analog) ---
    parameter = models.ForeignKey(
        Parameter, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Kanal (Parametre)",
    )
    condition = models.CharField(
        max_length=10, choices=COND_CHOICES, blank=True, default="", verbose_name="Limit Tipi",
    )
    min_value = models.FloatField(null=True, blank=True, verbose_name="Min Değer")
    max_value = models.FloatField(null=True, blank=True, verbose_name="Max Değer")

    # --- Dijital (durum değişimi) ---
    sensor = models.ForeignKey(
        Sensor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Dijital Sensör",
        help_text="Durumu seçilen değere geçtiğinde alarm üreten dijital sensör (DI/DO).",
    )
    trigger_state = models.BooleanField(
        default=True, verbose_name="Tetikleme Durumu",
        help_text="True = sensör Aktif (ON) olunca; False = Pasif (OFF) olunca alarm.",
    )

    # --- İstasyon offline ---
    offline_seconds = models.IntegerField(
        default=900, verbose_name="Offline Eşiği (sn)",
        help_text="Son veriden bu kadar saniye geçtiyse tesis offline sayılır.",
    )

    # --- Ortak ---
    period_minutes = models.IntegerField(
        default=60, choices=PERIOD_CHOICES, verbose_name="Alarm Periyodu",
        help_text="Aynı alarm için iki bildirim arası minimum süre.",
    )
    message = models.TextField(verbose_name="Mesaj")
    notify_all = models.BooleanField(
        default=True, verbose_name="Tüm Yetkililer",
        help_text="True ise tüm aktif kullanıcılara; False ise yalnız oluşturana gönderilir.",
    )
    send_sms = models.BooleanField(default=True, verbose_name="SMS Gönder")
    send_email = models.BooleanField(default=True, verbose_name="E-posta Gönder")
    enabled = models.BooleanField(default=True, verbose_name="Aktif")

    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    last_triggered_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Son Tetiklenme",
    )

    class Meta:
        db_table = "alarm_rule"
        verbose_name = "Alarm Tanımı"
        verbose_name_plural = "Alarm Tanımları"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.station} · {self.get_rule_type_display()}"

    @property
    def sensor_label(self):
        if self.sensor and self.sensor.parameter and self.sensor.parameter.parameter_name:
            return self.sensor.parameter.parameter_name
        return str(self.sensor) if self.sensor else "—"

    @property
    def state_label(self):
        return "Aktif (ON)" if self.trigger_state else "Pasif (OFF)"

    @property
    def type_label(self):
        """Tabloda gösterilecek okunur alarm tipi."""
        if self.rule_type == self.RULE_ANALOG:
            return dict(self.COND_CHOICES).get(self.condition, "Ölçüm")
        if self.rule_type == self.RULE_DIGITAL:
            return f"{self.sensor_label}: {self.state_label}"
        return "Tesis Offline"


class Reminder(models.Model):
    """Takvim hatırlatıcısı — paylaşımlı (tüm dashboard kullanıcıları görür).

    Jenerik bir özellik (SAIS'e özel değil) → `api/`. Operatör takvimden bir
    gün/saat seçer, başlık + not girer; `remind_at` geldiğinde **yalnızca
    dashboard içi** bildirim üretilir: header'daki çan ikonunda badge + açılır
    liste ve anasayfada "Yaklaşan Hatırlatmalar" widget'ı. Harici SMS/e-posta
    göndermez (bunun için Alarm Tanımı / Bildirim Merkezi kullanılır).

    `remind_at <= now()` ve `is_done=False` → **vadesi gelmiş** (çan badge'inde
    sayılır). Paylaşımlı olduğu için herhangi bir kullanıcı tamamlandı işaretler
    veya siler; `created_by` / `done_by` yalnız denetim içindir.
    """

    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_CHOICES = (
        (PRIORITY_LOW, "Düşük"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "Yüksek"),
    )

    title = models.CharField(max_length=160, verbose_name="Başlık")
    note = models.TextField(blank=True, default="", verbose_name="Not")
    remind_at = models.DateTimeField(verbose_name="Hatırlatma Zamanı")
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL,
        verbose_name="Öncelik",
    )
    station = models.ForeignKey(
        Station, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="reminders", verbose_name="Tesis",
        help_text="İsteğe bağlı — hatırlatıcıyı bir tesise bağla.",
    )

    is_done = models.BooleanField(default=False, verbose_name="Tamamlandı")
    done_at = models.DateTimeField(blank=True, null=True, verbose_name="Tamamlanma")
    done_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Tamamlayan",
    )

    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "reminder"
        verbose_name = "Hatırlatıcı"
        verbose_name_plural = "Hatırlatıcılar"
        ordering = ["remind_at"]
        indexes = [models.Index(fields=["remind_at", "is_done"])]

    def __str__(self):
        return f"{self.title} @ {self.remind_at:%d.%m.%Y %H:%M}"


# ---------------------------------------------------------------------------
# Rapor Stüdyosu — blok tabanlı rapor şablonları + zamanlama + üretim geçmişi
# ---------------------------------------------------------------------------

class ReportTemplate(models.Model):
    """Blok tabanlı rapor şablonu (Rapor Stüdyosu).

    `blocks` sıralı bir blok listesi tutar; her blok bir dict:
    ``{"id", "type", ...}``. Blok tipleri: heading / text / kpi_cards /
    table / chart / page_break / spacer. Veri blokları (kpi_cards, table,
    chart) kendi veri bağlamasını taşır: istasyon + parametre(ler) +
    zaman penceresi (`window`: relative anahtar veya sabit tarih) + bucket
    (raw/15min/hourly/daily) + kolon seçimi. Göreli pencereler üretim
    anındaki `reference_time`'a göre çözülür — zamanlanmış raporlar bu
    sayede her çalıştırmada güncel dönemi kapsar. Üretim: api/reporting.py.
    """

    PAGE_SIZE_CHOICES = (("A4", "A4"), ("A3", "A3"))
    ORIENTATION_CHOICES = (("portrait", "Dikey"), ("landscape", "Yatay"))

    name = models.CharField(max_length=150, verbose_name="Rapor Adı")
    description = models.TextField(blank=True, default="", verbose_name="Açıklama")
    blocks = models.JSONField(
        default=list, blank=True, verbose_name="Blok Tanımları",
        help_text="Sıralı blok listesi — editör tarafından yazılır (JSON).",
    )

    page_size = models.CharField(
        max_length=8, choices=PAGE_SIZE_CHOICES, default="A4", verbose_name="Sayfa Boyutu",
    )
    orientation = models.CharField(
        max_length=12, choices=ORIENTATION_CHOICES, default="portrait",
        verbose_name="Yönlendirme",
    )
    header_text = models.CharField(
        max_length=200, blank=True, default="", verbose_name="Üst Bilgi",
    )
    footer_text = models.CharField(
        max_length=200, blank=True, default="", verbose_name="Alt Bilgi",
    )
    show_logo = models.BooleanField(default=True, verbose_name="Logo Göster")

    is_template = models.BooleanField(
        default=False, verbose_name="Yerleşik Şablon",
        help_text="Yerleşik (seed) şablonlar silinemez.",
    )
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="report_templates", verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    updated_at = models.DateTimeField(auto_now=True, db_index=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "report_template"
        verbose_name = "Rapor Şablonu"
        verbose_name_plural = "Rapor Şablonları"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class ReportSchedule(models.Model):
    """Bir rapor şablonunun zamanlanmış üretim + e-posta dağıtım ayarı.

    Beat'te schedule başına PeriodicTask AÇILMAZ; tek dispatcher task
    (`api.tasks.dispatch_report_schedules`, her 60 sn) `next_run_at <= now`
    olan etkin kayıtları kuyruğa atar ve `next_run_at`'ı bir sonraki periyoda
    ilerletir (dispatch_polls deseni — kullanıcı zamanlama düzenledikçe
    django_celery_beat kaydı üretilmez).
    """

    PERIOD_CHOICES = (
        ("daily", "Günlük"),
        ("weekly", "Haftalık"),
        ("monthly", "Aylık"),
    )
    WEEKDAY_CHOICES = (
        (0, "Pazartesi"), (1, "Salı"), (2, "Çarşamba"), (3, "Perşembe"),
        (4, "Cuma"), (5, "Cumartesi"), (6, "Pazar"),
    )

    template = models.ForeignKey(
        ReportTemplate, on_delete=models.CASCADE, related_name="schedules",
        verbose_name="Rapor Şablonu",
    )
    enabled = models.BooleanField(default=True, verbose_name="Etkin")
    period = models.CharField(
        max_length=10, choices=PERIOD_CHOICES, default="daily", verbose_name="Periyot",
    )
    time_of_day = models.TimeField(default=datetime.time(7, 0), verbose_name="Çalışma Saati")
    weekday = models.PositiveSmallIntegerField(
        choices=WEEKDAY_CHOICES, blank=True, null=True, verbose_name="Haftanın Günü",
        help_text="Haftalık periyot için (0=Pazartesi).",
    )
    day_of_month = models.PositiveSmallIntegerField(
        blank=True, null=True, verbose_name="Ayın Günü",
        help_text="Aylık periyot için (1-28).",
    )

    output_pdf = models.BooleanField(default=True, verbose_name="PDF Üret")
    output_excel = models.BooleanField(default=False, verbose_name="Excel Üret")

    email_enabled = models.BooleanField(default=False, verbose_name="E-posta Gönder")
    recipients = models.TextField(
        blank=True, default="", verbose_name="Alıcılar",
        help_text="Virgül veya satır ile ayrılmış e-posta adresleri.",
    )
    email_subject = models.CharField(
        max_length=200, default="{report_name} - {date}", verbose_name="E-posta Konusu",
        help_text="Placeholder: {report_name}, {date}",
    )
    email_body = models.TextField(
        blank=True, default="", verbose_name="E-posta Gövdesi",
        help_text="Placeholder: {report_name}, {date}. Boşsa varsayılan gövde kullanılır.",
    )

    next_run_at = models.DateTimeField(
        blank=True, null=True, db_index=True, verbose_name="Sonraki Çalışma",
    )
    last_run_at = models.DateTimeField(blank=True, null=True, verbose_name="Son Çalışma")
    last_status = models.CharField(
        max_length=16, blank=True, default="", verbose_name="Son Durum",
    )

    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "report_schedule"
        verbose_name = "Rapor Zamanlaması"
        verbose_name_plural = "Rapor Zamanlamaları"
        ordering = ["template__name", "id"]

    def __str__(self):
        return f"{self.template} · {self.period_summary}"

    @property
    def period_summary(self):
        """UI'da gösterilecek okunur periyot özeti."""
        saat = self.time_of_day.strftime("%H:%M") if self.time_of_day else "--:--"
        if self.period == "weekly":
            gun = dict(self.WEEKDAY_CHOICES).get(self.weekday, "?")
            return f"Her {gun} {saat}"
        if self.period == "monthly":
            return f"Her ayın {self.day_of_month or 1}. günü {saat}"
        return f"Her gün {saat}"

    def compute_next_run(self, from_dt=None):
        """`from_dt`'den (default: şimdi) SONRAKİ çalışma zamanını döndürür.

        Yerel saat diliminde hesaplanır (kullanıcı 07:00 dediyse yerel 07:00);
        dönen değer TZ-aware'dır.
        """
        from django.utils import timezone as dj_tz

        now = from_dt or dj_tz.now()
        local_now = dj_tz.localtime(now)
        run_time = self.time_of_day or datetime.time(7, 0)

        def _candidate(day):
            naive = datetime.datetime.combine(day, run_time)
            return dj_tz.make_aware(naive, dj_tz.get_current_timezone())

        if self.period == "daily":
            cand = _candidate(local_now.date())
            if cand <= now:
                cand = _candidate(local_now.date() + datetime.timedelta(days=1))
            return cand

        if self.period == "weekly":
            target_wd = self.weekday if self.weekday is not None else 0
            days_ahead = (target_wd - local_now.weekday()) % 7
            cand = _candidate(local_now.date() + datetime.timedelta(days=days_ahead))
            if cand <= now:
                cand = _candidate(local_now.date() + datetime.timedelta(days=days_ahead + 7))
            return cand

        # monthly
        target_dom = min(max(self.day_of_month or 1, 1), 28)
        year, month = local_now.year, local_now.month
        cand = _candidate(datetime.date(year, month, target_dom))
        if cand <= now:
            month += 1
            if month > 12:
                month, year = 1, year + 1
            cand = _candidate(datetime.date(year, month, target_dom))
        return cand

    def save(self, *args, **kwargs):
        # Etkinleştirilirken next_run_at boşsa hesapla; devre dışı bırakılınca
        # temizle (dispatcher hiç görmesin). Zamanlama alanları değiştiğinde
        # yeniden hesap, kaydeden endpoint'in sorumluluğundadır
        # (next_run_at=None ver → burada tazelenir).
        if self.enabled and not self.next_run_at:
            self.next_run_at = self.compute_next_run()
        elif not self.enabled:
            self.next_run_at = None
        super().save(*args, **kwargs)


class GeneratedReport(models.Model):
    """Üretilmiş bir raporun kaydı — iş geçmişi + dosya + e-posta durumu.

    DatabaseBackup deseni: status running→success/failed, tetikleyici audit,
    dosyalar `settings.REPORTS_DIR` altında çıplak dosya adıyla tutulur
    (path-traversal'a kapalı indirme için).
    """

    STATUS_CHOICES = (
        ("running", "Üretiliyor"),
        ("success", "Başarılı"),
        ("failed", "Başarısız"),
    )
    TRIGGER_CHOICES = (
        ("auto", "Otomatik"),
        ("manual", "Manuel"),
    )
    EMAIL_STATUS_CHOICES = (
        ("", "—"),
        ("sent", "Gönderildi"),
        ("partial", "Kısmen Gönderildi"),
        ("failed", "Gönderilemedi"),
    )

    template = models.ForeignKey(
        ReportTemplate, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="generated_reports", verbose_name="Rapor Şablonu",
    )
    template_name = models.CharField(
        max_length=150, verbose_name="Şablon Adı",
        help_text="Üretim anındaki ad (şablon silinse de geçmişte kalır).",
    )
    schedule = models.ForeignKey(
        ReportSchedule, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="generated_reports", verbose_name="Zamanlama",
    )

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="running",
        db_index=True, verbose_name="Durum",
    )
    trigger = models.CharField(
        max_length=10, choices=TRIGGER_CHOICES, default="manual", verbose_name="Tetikleyici",
    )
    triggered_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Tetikleyen Kullanıcı",
    )
    reference_time = models.DateTimeField(
        verbose_name="Referans Zamanı",
        help_text="Göreli zaman pencerelerinin çözüldüğü 'şimdi'.",
    )

    pdf_file = models.CharField(
        max_length=255, blank=True, default="", verbose_name="PDF Dosyası",
        help_text="REPORTS_DIR altındaki çıplak dosya adı.",
    )
    xlsx_file = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Excel Dosyası",
    )
    pdf_size = models.BigIntegerField(default=0, verbose_name="PDF Boyutu (byte)")
    xlsx_size = models.BigIntegerField(default=0, verbose_name="Excel Boyutu (byte)")

    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Başlangıç")
    finished_at = models.DateTimeField(blank=True, null=True, verbose_name="Bitiş")
    error = models.TextField(blank=True, default="", verbose_name="Hata / Uyarı")

    email_status = models.CharField(
        max_length=10, choices=EMAIL_STATUS_CHOICES, blank=True, default="",
        verbose_name="E-posta Durumu",
    )
    email_info = models.TextField(blank=True, default="", verbose_name="E-posta Detayı")

    class Meta:
        db_table = "generated_report"
        verbose_name = "Üretilen Rapor"
        verbose_name_plural = "Üretilen Raporlar"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.template_name} [{self.status}]"

    def file_paths(self):
        """Diskteki mevcut dosya yollarını döndürür (REPORTS_DIR altı)."""
        import os
        from django.conf import settings as dj_settings

        paths = []
        for fname in (self.pdf_file, self.xlsx_file):
            if fname:
                paths.append(os.path.join(dj_settings.REPORTS_DIR, fname))
        return paths

    def delete(self, *args, **kwargs):
        # Kayıt silinince üretilen dosyalar da diskten kaldırılır.
        import os

        for path in self.file_paths():
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        super().delete(*args, **kwargs)
