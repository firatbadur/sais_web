from django.db import models
from users.models import *
class StationInfo(models.Model):

    STATION_TYPE_CHOICES = [
        (1, 'Evsel Atıksu'),  # Yerleşimden kaynaklanan atıksu
        (2, 'Endüstriyel Atıksu'),  # Sanayi ve ticari faaliyetlerden gelen atıksu
        (3, 'Kentsel Atıksu'),  # Evsel, endüstriyel ve/veya yağmur suyunun karışımı
    ]

    name = models.CharField(max_length=100, verbose_name="İstasyon Adı", help_text="İstasyon Adı", blank=False,null=False)
    station_type = models.IntegerField(choices=STATION_TYPE_CHOICES, default=1,blank=True,null=True)
    address = models.CharField(max_length=250, verbose_name="İstasyon Adresi", help_text="İstasyon Adresi", blank=True,null=False)
    domain = models.URLField(max_length=100, verbose_name="Domain", help_text="İstasyon Domain", blank=True,null=False,default="sais.onlinecevre.com.tr")
    port = models.IntegerField(verbose_name="Port No", help_text="Port No", blank=True, null=True,default=443)
    company = models.CharField(max_length=100, verbose_name="Kurum Adı", help_text="Kurum Adı", blank=True,null=False,default="Envisoft")


    active = models.BooleanField(verbose_name="Aktif mi?", help_text="Aktif mi?", blank=False, null=True,default=True)
    created_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(CustomUser,on_delete=models.SET_NULL,blank=False,null=True,default=1)
    class Meta:

        db_table = 'station'
        verbose_name_plural = "İstasyon Bilgileri" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["created_at"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.name

class SimInformation(models.Model):

    station = models.ForeignKey(StationInfo,on_delete=models.CASCADE,blank=False,null=False)
    sim_id = models.CharField(max_length=100, verbose_name="İstasyon ID", help_text="İstasyon ID", blank=False,null=False)
    code = models.CharField(max_length=50, verbose_name="İstasyon Kodu", help_text="İstasyon Kodu", blank=False,null=False,default='30060001')
    name = models.CharField(max_length=200, verbose_name="İstasyon Adı", help_text="İstasyon Adı", blank=False,null=False)
    data_period = models.IntegerField(verbose_name="Veri Periyodu (dk)", help_text="Veri Periyodu (dk)", blank=True, null=True,default=1)
    username = models.CharField(max_length=50, verbose_name="Sim Kullanıcı Adı", help_text="Sim Kullanıcı Adı", blank=False,null=False)
    password = models.CharField(max_length=50, verbose_name="Sim Şifre", help_text="Sim Şifre",blank=False, null=False)
    created_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(CustomUser,on_delete=models.SET_NULL,blank=False,null=True,default=1)

    class Meta:

        db_table = 'sim_info'
        verbose_name_plural = "Sim Bilgileri" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["created_at"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.station

class Connections(models.Model):

    CON_NAME_CHOICES = (("con_1", "Bağlantı-1"),
        ("con_2", "Bağlantı-2"),
        ("con_3", "Bağlantı-3"),)

    COM_TYPE_CHOICES = (("modbus", "Modbus"),
                        ("ascii", "Ascii"))

    CON_TYPE_CHOICES = (("tcp", "TCP/IP"),
                        ("serial", "SERIAL"))

    CON_MODE_CHOICES = (("rtu", "RTU"),
                        ("ascii", "ASCII"))

    BAUDRATES = ((300,300),(600,600),(1200,1200),(2400,2400),(4800,4800),(9600,9600),(14400,14400),(19200,19200))

    PARITY = ((0, "None Parity"),
                        (1, "Odd Parity"),(2, "Even Parity"))

    STOP_BITS = ((0, "1 Stop Bit"),
              (1, "2 Stop Bit"))

    BYTE_SIZE = ((8, "8 Data Bits"),
                 (7, "7 Data Bits"))


    con_name = models.CharField(max_length=10,verbose_name="Bağlantı Adı",help_text='Bağlantı Adı',blank=False,null=True,choices=CON_NAME_CHOICES,default='con_1')
    communication_type = models.CharField(max_length=10,verbose_name='Haberleşme Tipi',help_text='Haberleşme Tipi',blank=False,null=True,choices=COM_TYPE_CHOICES,default='modbus')
    con_type = models.CharField(max_length=10,verbose_name='Bağlantı Tipi',help_text='Bağlantı Tipi',blank=False,null=True,choices=CON_TYPE_CHOICES,default='tcp')
    con_mode = models.CharField(max_length=10,verbose_name='Bağlantı Modu',help_text='Bağlantı Modu',blank=False,null=True,choices=CON_MODE_CHOICES,default='rtu')
    con_address = models.CharField(max_length=15,verbose_name='Bağlantı Adresi',help_text='COM4 or 192.168.1.1',blank=False,null=True)
    port = models.IntegerField(verbose_name='Port', help_text='Port Numarası',default=502,blank=False,null=True)
    baudrate = models.IntegerField(verbose_name='Bant Genişliği',help_text='Bant Genişliği',choices=BAUDRATES,default=9600)
    parity = models.IntegerField(verbose_name='Parity', help_text='Parity', choices=PARITY,default=0)
    stop_bits = models.IntegerField(verbose_name='Stop Bits', help_text='Stop Bits', choices=STOP_BITS,default=0)
    byte_size = models.IntegerField(verbose_name='Byte Size', help_text='Byte Size', choices=BYTE_SIZE,default=8)
    xonxoff = models.BooleanField(verbose_name='Xonxoff',default=False)
    rtscts = models.BooleanField(verbose_name='Rstcts', default=False)
    dsrdtr = models.BooleanField(verbose_name='Dsrdtr', default=False)
    created_date = models.DateTimeField(auto_now=True)
    status = models.BooleanField(verbose_name="Aktif", help_text="Aktif", default=True)


    class Meta:
        verbose_name_plural = "Connections" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["id"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.con_name # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Status_Codes(models.Model):

    code = models.IntegerField(verbose_name='Kod Numarası',blank=False,null=True)
    name = models.CharField(max_length=200,verbose_name='Status Kod Adı',blank=False,null=True)

    class Meta:
        db_table = 'status_codes'
        verbose_name_plural = "Status Kodları" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["id"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.name # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Parameters(models.Model):
    station = models.ForeignKey(
        StationInfo,
        on_delete=models.CASCADE,
        blank=False,
        null=False,
        default=1,
        related_name="parameters"
    )
    parameter_name = models.CharField(max_length=50,verbose_name='Parametre Adı',blank=False,null=True)
    parameter_txt = models.CharField(max_length=50, verbose_name='Parametre Txt', blank=True, null=True)
    unit = models.CharField(max_length=150, verbose_name='Parametre Birim', blank=True, null=True)
    unit_txt = models.CharField(max_length=50, verbose_name='Parametre Txt', blank=True, null=True)
    channel_number = models.IntegerField(verbose_name='Kanal No',blank=True,null=True)
    sim_channel = models.CharField(max_length=250,verbose_name='Kanal ID',blank=False,null=True)
    envi_channel = models.IntegerField(verbose_name='Envisoft Kanal ID', blank=False, null=True)
    gec_min = models.IntegerField(verbose_name='Geçerli Veri Min',blank=True,null=True)
    gec_max = models.IntegerField(verbose_name='Geçerli Veri Max', blank=True, null=True)
    olcum_min = models.IntegerField(verbose_name='Ölçüm Altı', blank=True, null=True)
    olcum_max = models.IntegerField(verbose_name='Ölçüm Üstü', blank=True, null=True)
    min_range = models.IntegerField(verbose_name='Range Aralığı Min', blank=True, null=True)
    max_range = models.IntegerField(verbose_name='Range Aralığı Min', blank=True, null=True)

    class Meta:
        db_table = 'parameters'
        verbose_name_plural = "Parametreler" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["id"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.parameter_name # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Sensors(models.Model):

    SENSOR_TYPE = ((0, "Analog Input"),(1, "Analog Output"),(2, "Dijital Input"),(3, "Dijital Output"))

    BYTE_ORDER = (("little", "Endian.Little"),
                        ("big", "Endian.Big"))

    SIGNAL_TYPE = ((0, "4-20mA"),(1, "0-20mA"),(2, "0-10mV"))

    FUNCTION = ((1, "Read Coils"),(2, "Read Discrete Inputs"),(3, "Read Holding Registers"),(4, "Read Input Registers"),
                (5, "Write Single Coil"),(6, "Write Single Register"),(15, "Write Multiple Coils"),(16, "Write Multiple Registers"))

    DECODE_TYPES = (("float32", "32 Bit Float"),("float64", "64 Bit Float"),("hex", "Hex"),(None,"Raw Data"))

    parameters = models.ForeignKey(Parameters,on_delete=models.CASCADE,blank=False,null=True)
    sensor_type = models.IntegerField(verbose_name='Sensör Tipi',blank=False,null=True,choices=SENSOR_TYPE,default=0)
    brand = models.CharField(max_length=100,verbose_name='Sensör Marka',blank=True,null=True)
    model = models.CharField(max_length=100, verbose_name='Sensör Model', blank=True, null=True)
    serial_number = models.CharField(max_length=150, verbose_name='Seri No', blank=True, null=True)
    con = models.ForeignKey(Connections,on_delete=models.CASCADE,verbose_name='Bağlantı')
    signal_type = models.IntegerField(verbose_name='Sinyal Tipi',blank=True,null=True,choices=SIGNAL_TYPE,default=0)
    slave_id = models.IntegerField(verbose_name='Slave ID',default=1,blank=False,null=True)
    byte_order = models.CharField(max_length=20,verbose_name='Byte Order',blank=False,null=True,choices=BYTE_ORDER,default='big')
    word_order = models.CharField(max_length=20, verbose_name='Word Order', blank=False, null=True, choices=BYTE_ORDER,default='little')
    ascii_code = models.CharField(max_length=20,verbose_name='Ascii Kod',blank=True,null=True)
    address = models.IntegerField(verbose_name='Haberleşme Adresi',help_text='Modbus/Ascii Adresi/Sırası',blank=True,null=True)
    quantity = models.IntegerField(verbose_name='Adres Aralığı', blank=True,null=True,default=2)
    function = models.IntegerField(verbose_name='Fonksiyon', blank=True, null=True, choices=FUNCTION,default=3)
    decode = models.CharField(max_length=20,verbose_name='Decode',blank=True,null=True,choices=DECODE_TYPES,default="float32")
    digital_inverse = models.BooleanField(verbose_name='Dijital Ters mi ?',default=False)
    is_active = models.BooleanField(verbose_name='Aktif', default=True)
    class Meta:
        db_table = 'sensors'
        verbose_name_plural = "Sensors" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["id"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.sensor # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class SensorInstants(models.Model):


    channel = models.OneToOneField(Sensors, on_delete=models.CASCADE, blank=False, null=True)
    instant = models.FloatField(verbose_name='Anlık Değer',blank=True,null=True,default=0)
    status = models.ForeignKey(Status_Codes,on_delete=models.CASCADE,blank=True,null=True)
    readtime = models.DateTimeField(verbose_name='Okuma Zamanı', blank=True, null=True)
    factorA = models.FloatField(verbose_name='Kalibrasyon Faktörü (A)', help_text='result = ax+b', blank=True,
                                null=True, default=1)
    factorB = models.FloatField(verbose_name='Kalibrasyon Faktörü (B)', help_text='result = ax+b', blank=True,
                                null=True, default=0)
    send_status = models.BooleanField(verbose_name='Status Gönderilsin mi ?', default=False)
    is_random = models.BooleanField(verbose_name='Aktif', default=False)
    class Meta:
        db_table = 'sensor_instants'
        verbose_name_plural = "Sensör Anlık Veriler" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["channel"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.channel # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Reads(models.Model):

    channel = models.ForeignKey(Sensors,on_delete=models.CASCADE,verbose_name='Kanal',blank=False,null=True)
    value = models.FloatField(verbose_name='Değer',blank=False,null=True)
    status = models.ForeignKey(Status_Codes,on_delete=models.CASCADE,blank=False,null=True)
    time_iso = models.DateTimeField(verbose_name='Kayıt Tarihi',blank=False,null=True)

    class Meta:
        db_table = 'reads'
        verbose_name_plural = "Reads" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.channel # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Poweroff(models.Model):

    station = models.ForeignKey(StationInfo,on_delete=models.CASCADE)
    start_date = models.DateTimeField(verbose_name='Başlangıç Tarihi',blank=False,null=True)
    end_date = models.DateTimeField(verbose_name='Bitiş Tarihi', blank=False, null=True)
    time_iso = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi')

    class Meta:
        db_table = 'poweroff'
        verbose_name_plural = "Poweroff Kayıtları" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.time_iso # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Calibration(models.Model):

    CAL_TYPE = ((0, "Zero"), (1, "Span"), (2, "Multi"))

    channel = models.ForeignKey(Sensors,on_delete=models.CASCADE,blank=False,null=True)
    type = models.IntegerField(verbose_name='Kalibrasyon Tipi',blank=False,null=True,choices=CAL_TYPE)
    period = models.IntegerField(verbose_name='Kalibrasyon Periyodu',blank=False,null=True,default=60)
    cal_ref = models.FloatField(verbose_name='Referans Değer',blank=False,null=True)
    cal_average = models.FloatField(verbose_name='Ortalama Değer', blank=False, null=True)
    cal_std = models.FloatField(verbose_name='Standart Sapma', blank=False, null=True)
    user = models.ForeignKey(CustomUser,on_delete=models.CASCADE,blank=True,null=True)
    is_valid = models.BooleanField(verbose_name='Geçerli mi ?',blank=False,null=True)
    time_iso = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi')

    class Meta:
        db_table = 'calibration'
        verbose_name_plural = "Kalibrasyon Kayıtları" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.time_iso # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Log_Types(models.Model):

    name = models.CharField(max_length=200, verbose_name='Status Kod Adı', blank=False, null=True)

    class Meta:
        db_table = 'log_types'
        verbose_name_plural = "Log Tipleri" # admin sayfasında görünen tablo ismini gösterir.
        # ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.name # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Sys_Log(models.Model):

    station = models.ForeignKey(StationInfo, on_delete=models.CASCADE, null=True, blank=True,default=1)
    type = models.ForeignKey(Log_Types,on_delete=models.CASCADE)
    description = models.CharField(max_length=1000,verbose_name='Açıklama',blank=False,null=True)
    time_iso = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi')

    class Meta:
        db_table = 'sys_log'
        verbose_name_plural = "Sistem Log Kayıtları" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.time_iso # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Api_Log(models.Model):

    type = models.ForeignKey(Log_Types,on_delete=models.CASCADE)
    url = models.CharField(max_length=250, verbose_name='Request Url', blank=True, null=True)
    data = models.CharField(max_length=2000,verbose_name='Request Data',blank=True,null=True)
    header = models.CharField(max_length=2000, verbose_name='Request Header', blank=True, null=True)
    param = models.CharField(max_length=2000, verbose_name='Request Param', blank=True, null=True)
    token = models.CharField(max_length=250, verbose_name='Token', blank=True, null=True)
    response = models.CharField(max_length=2000, verbose_name='Response', blank=True, null=True)
    status = models.IntegerField(verbose_name='Status Kod',blank=True,null=True)
    time_iso = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi',blank=False,null=True)

    class Meta:
        db_table = 'api_log'
        verbose_name_plural = "Api Log Kayıtları" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.type # eklenen kayıtların ne ile görüntüeneceğini gösterir.

# class Sim_Data(models.Model):
#
#     ph_v = models.FloatField(verbose_name='Ph Anlık',blank=True,null=True)
#     ph_s = models.IntegerField(verbose_name='Ph Status', blank=True, null=True)
#     il_v = models.FloatField(verbose_name='İletkenlik Anlık', blank=True, null=True)
#     il_s = models.IntegerField(verbose_name='İletkenlik Status', blank=True, null=True)
#     coz_v = models.FloatField(verbose_name='Çöz. Oks. Anlık', blank=True, null=True)
#     coz_s = models.IntegerField(verbose_name='Çöz. Oks. Status', blank=True, null=True)
#     debi_v = models.FloatField(verbose_name='Debi Anlık', blank=True, null=True)
#     debi_s = models.IntegerField(verbose_name='Debi Status', blank=True, null=True)
#     sic_v = models.FloatField(verbose_name='Sıcaklık Anlık', blank=True, null=True)
#     sic_s = models.IntegerField(verbose_name='Sıcaklık Status', blank=True, null=True)
#     akis_v = models.FloatField(verbose_name='Akış Hızı Anlık', blank=True, null=True)
#     akis_s = models.IntegerField(verbose_name='Akış Hızı Status', blank=True, null=True)
#     koi_v = models.FloatField(verbose_name='Koi Anlık', blank=True, null=True)
#     koi_s = models.IntegerField(verbose_name='Koi Status', blank=True, null=True)
#     akm_v = models.FloatField(verbose_name='Akm Anlık', blank=True, null=True)
#     akm_s = models.IntegerField(verbose_name='Akm Status', blank=True, null=True)
#     time_iso = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi',blank=False,null=True)
#
#     class Meta:
#         db_table = 'sim_data'
#         verbose_name_plural = "Sim Verileri" # admin sayfasında görünen tablo ismini gösterir.
#         ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.
#
#     def __str__(self):
#
#         return "%s" % self.time_iso # eklenen kayıtların ne ile görüntüeneceğini gösterir.

# class Sample_Senario(models.Model):
#
#     ph_ort = models.FloatField(verbose_name='Ph Ort.',blank=False,null=True)
#     akm_ort = models.FloatField(verbose_name='Akm Ort.', blank=False, null=True)
#     koi_ort = models.FloatField(verbose_name='Koi Ort.', blank=False, null=True)
#     alarm_adet = models.IntegerField(verbose_name='Alarm Adet', blank=True, null=True)
#     alarm_durum = models.BooleanField(verbose_name='Alarm Durumu', blank=True, null=True,default=False)
#     alarm_param = models.CharField(max_length=100,blank=True,null=True,verbose_name="Alarm Parametre")
#     alarm_msg = models.CharField(max_length=250, blank=True, null=True, verbose_name="Alarm Mesajı")
#     time_iso = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi',blank=False,null=True)
#
#     class Meta:
#         db_table = 'sample_senaris'
#         verbose_name_plural = "Numune Alma Canlı" # admin sayfasında görünen tablo ismini gösterir.
#         ordering = ["-time_iso"] #admin panelinde id ye göre listeleme yapar.
#
#     def __str__(self):
#
#         return "%s" % self.time_iso # eklenen kayıtların ne ile görüntüeneceğini gösterir.

class Out_Requests(models.Model):

    ALARM_LEVEL = ((1, "Bakanlık Numune Talebi"), (2, "Operatör Talebi"), (2, "Otomatik Numune Senaryosu"))

    sensor = models.ForeignKey(Sensors,on_delete=models.CASCADE,blank=False,null=False)
    value = models.IntegerField(verbose_name='Değer',blank=False,null=False)
    alarm_level = models.IntegerField(choices=ALARM_LEVEL,verbose_name='Alarm Level',blank=False,null=False)
    is_completed = models.BooleanField(blank=False,null=False,default=False)
    request_code = models.CharField(max_length=250, blank=True, null=True)
    created_at = models.DateTimeField(auto_now=True,verbose_name='Kayıt Tarihi',blank=False,null=True)

    class Meta:
        db_table = 'out_requests'
        verbose_name_plural = "Dijital Out Talepleri" # admin sayfasında görünen tablo ismini gösterir.
        ordering = ["-created_at"] #admin panelinde id ye göre listeleme yapar.

    def __str__(self):

        return "%s" % self.created_at # eklenen kayıtların ne ile görüntüeneceğini gösterir.