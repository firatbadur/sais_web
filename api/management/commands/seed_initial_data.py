"""
Çekirdek SCADA seed verisini oluşturur: varsayılan istasyon, parametreler,
status kodları ve jenerik talep tipleri.

Kullanım:
    python manage.py seed_initial_data

Alan-özel (SAIS / Envisoft / Bakanlık) veriler için ayrı komut:
    python manage.py seed_sais_data
"""
from django.core.management.base import BaseCommand

from api.models import (
    LogType, MessageTemplate, Parameter, RequestType, Station, StationType, StatusCode,
)


# Jenerik istasyon (ölçüm sistemi) tipleri. Atıksu deşarj alt-tipleri
# (evsel/endüstriyel/kentsel) SAIS-özel olduğu için sais_domain seed'inde kalır.
# (code, name, description)
DEFAULT_STATION_TYPES = [
    ("wastewater_monitoring", "Sürekli Atıksu İzleme Sistemi", "Atıksu deşarjı sürekli izleme (SAIS)"),
    ("emission_monitoring", "Sürekli Emisyon Ölçüm Sistemi", "Baca gazı sürekli emisyon ölçümü (SEÖS)"),
    ("flow_measurement", "Debi Ölçüm Sistemi", "Atıksu/proses debisi ölçümü"),
    ("air_quality", "Hava Kalitesi İzleme Tesisi", "Ortam hava kalitesi sürekli izleme"),
    ("meteorology", "Meteorolojik Ölçüm Tesisi", "Rüzgar, sıcaklık, nem, basınç vb. meteoroloji"),
    ("water_quality", "Su Kalitesi İzleme Tesisi", "Yüzey/yeraltı/alıcı ortam su kalitesi izleme"),
    ("noise_monitoring", "Gürültü İzleme Tesisi", "Çevresel gürültü ölçümü"),
    ("solar_plant", "GES (Güneş Enerji Santrali)", "Güneş enerji santrali izleme"),
    ("energy_monitoring", "Enerji İzleme Sistemi", "Elektrik/enerji tüketim izleme"),
    ("scada_general", "Genel SCADA İzleme", "Genel amaçlı endüstriyel izleme sistemi"),
    ("other", "Diğer Sistemler", "Yukarıdaki kategorilerin dışındaki sistemler"),
]


# (parameter_name, parameter_txt, unit, unit_txt, device_channel_id,
#  gec_min, gec_max, olcum_min, olcum_max, min_range, max_range)
DEFAULT_PARAMETERS = [
    ("pH", "pH", "7e84ef31-518b-495f-ad2d-ccc8824dbeb3", "--",
     "600101fd-b533-4b2a-951f-37a6c13aab64", 6, 9, 0, 14, 0, 14),
    ("Iletkenlik", "İletkenlik", "cbbf854a-5f7a-4552-a26b-77342217e4e4", "uS/cm",
     "1650b204-0801-4ba3-a435-345e8374323f", 0, 100000, 0, 100000, 0, 100000),
    ("CozunmusOksijen", "Çözünmüs Oksijen", "dc0f15a2-b2eb-4760-9c28-ff255c6d16d5", "mg/l",
     "67dc7a29-b7ec-4d3f-b886-ed43a1847af3", 0, 100, 0, 100, 0, 100),
    ("Debi", "Debi", "6e78e970-89d2-4479-8448-03017edf2319", "m3/dakika",
     "faefdd30-61d6-4f02-9fe3-066c03139e66", 0, 100000, 0, 100000, 0, 100000),
    ("Sicaklik", "Sıcaklık", "53ea78cb-9e85-44b9-b376-317e7844f056", "°C",
     "f22fa333-ecbd-4538-b4c3-3b4e56917a46", 0, 100, 0, 100, -40, 100),
    ("AkisHizi", "Akış Hızı", "185480e6-da5e-43ba-9b25-1d7f8cb8eebb", "m/sn",
     "c292b027-36eb-4f33-aff2-1ee94df949e9", 0, 1, 0, 1, 0, 1),
    ("KOi", "KOi", "dc0f15a2-b2eb-4760-9c28-ff255c6d16d5", "mg/l",
     "a189a5c8-cf82-4d44-825c-0b9a40e5f7c2", 0, 125, 0, 10000, 0, 10000),
    ("AKM", "AKM", "dc0f15a2-b2eb-4760-9c28-ff255c6d16d5", "mg/l",
     "4c699eff-7a09-46c0-b4ef-a208d2866f0b", 0, 35, 0, 10000, 0, 10000),
]


# (parameter_name, parameter_txt, unit_txt)
CHANNEL_ONLY = [
    ("KabinSicaklik", "Kabin Sıcaklık", "°C"),
    ("KabinNem", "Kabin Nem", "%"),
    ("GunlukDebi", "Günlük Debi", "m3/gun"),
    ("GirisDebi", "Giriş Debi", "m3/dk"),
    ("CikisDebi", "Çıkış Debi", "m3/dk"),
    ("Pompa1", "Pompa-1", ""),
    ("Pompa2", "Pompa-2", ""),
    ("Yikama", "Yıkama", ""),
    ("HaftalikYikama", "Haftalık Yıkama", ""),
    ("Bakim", "Bakım", ""),
    ("Enerji", "Enerji", ""),
    ("SuBasti", "SuBasti", ""),
    ("Duman", "Duman", ""),
    ("SuYok", "Su Yok", ""),
    ("NumuneAlma", "Numune Kompozit", ""),
    ("Surucu1", "Sürücü-1", ""),
    ("Surucu2", "Sürücü-2", ""),
    ("Surucu3", "Sürücü-3", ""),
    ("İstasyonBakimda", "İstasyon Bakımda", ""),
    ("Kapi", "Kapı", ""),
    ("AcilStop", "Acil Stop", ""),
    ("TesisBakimda", "Tesis Bakimda", ""),
    ("SistemKapali", "Sistem Kapalı", ""),
    ("Kalibrasyon", "Kalibrasyon", ""),
    ("Ups", "Ups", ""),
    ("ManuelYikama", "Manuel Yıkama", ""),
    ("SistemDurdurma", "Sistem Durdurma", ""),
    ("BakimModu", "Bakım Modu", ""),
    ("AcilStopOut", "Acil Stop Out", ""),
    ("Numune1Dolu", "Numune-1 Dolu", ""),
    ("Numune2Dolu", "Numune-2 Dolu", ""),
    ("Numune3Dolu", "Numune-3 Dolu", ""),
    ("Numune4Dolu", "Numune-4 Dolu", ""),
    ("NumuneReset", "NumuneReset", ""),
    ("NumuneAnlik", "Numune Anlık", ""),
    ("Surucu1Hata", "Sürücü-1-Hata", ""),
    ("Surucu2Hata", "Sürücü-2-Hata", ""),
    ("SenaryoSifirla", "Senaryo Sıfırla", ""),
    ("Desarj", "Desarj", ""),
]


# Bakanlık SAIS veri durum kodlarının tamamı (GetDataStatusDescription ile birebir).
DEFAULT_STATUS_CODES = [
    (0, "Veri Yok"),
    (1, "Veri Geçerli"),
    (4, "Geçersiz Veri"),
    (7, "Kalibrasyon Limit Dışı"),
    (8, "İletişim Hatası"),
    (9, "Sistem Kalibrasyon"),
    (12, "Alarm"),
    (15, "Purge"),
    (19, "Kalibrasyon Hatası"),
    (21, "Akış Yok"),
    (22, "Deşarj Yok"),
    (23, "Yıkama"),
    (24, "Haftalık Yıkama"),
    (25, "İstasyon Bakımda"),
    (26, "Tesis Bakımda"),
    (30, "Cihaz Bakımda"),
    (31, "Debi Arızası"),
    (35, "1. Nokta Kalibrasyonu"),
    (36, "2. Nokta Kalibrasyonu"),
    (39, "Ölçüm Aralığı Dışında"),
    (200, "Eksik veya Geçersiz Yıkama"),
    (201, "Eksik veya Geçersiz Haftalık Yıkama"),
    (202, "Geçersiz veya Eksik Aylık Kalibrasyon"),
    (203, "Geçersiz Akış Hızı Değeri"),
    (204, "Geçersiz Debi Değeri"),
    (205, "Tekrar Veri"),
    (206, "Geçersiz Birim"),
]


# Jenerik SCADA talep tipleri (SAIS-özel olanlar sais_domain seed'inde).
DEFAULT_REQUEST_TYPES = [
    ("operator", "Operatör Talebi"),
    ("auto_scenario", "Otomatik Numune Senaryosu"),
    ("manual_output", "Manuel Çıkış Kontrolü"),
]


# Hazır bildirim mesajları (alarm + SMS/mail test panelinde kullanılır).
# Kurumsal, parametre/olay-bazlı şablonlar — kullanıcı ayrıca kendi mesajını ekleyebilir.
DEFAULT_MESSAGE_TEMPLATES = [
    # --- Analog ölçüm (limit aşımı) ---
    ("pH Limit Aşımı", "pH değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen tesisi kontrol ediniz."),
    ("İletkenlik Limit Aşımı", "İletkenlik değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    ("Çözünmüş Oksijen Limit Aşımı", "Çözünmüş oksijen değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    ("Debi Limit Aşımı", "Debi değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    ("Sıcaklık Limit Aşımı", "Sıcaklık değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    ("Akış Hızı Limit Aşımı", "Akış hızı belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    ("KOİ Limit Aşımı", "KOİ değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    ("AKM Limit Aşımı", "AKM değeri belirlenen limit değerlerinin dışına çıkmıştır. Lütfen kontrol ediniz."),
    # --- Dijital / olay ---
    ("Enerji Kesintisi", "Tesiste enerji kesintisi tespit edilmiştir. UPS devrede; lütfen kontrol ediniz."),
    ("Su Baskını", "Su baskını sensörü aktif olmuştur. Lütfen acil müdahale ediniz."),
    ("Duman Algılandı", "Duman sensörü aktif olmuştur. Lütfen acil kontrol ediniz."),
    ("Acil Stop", "Acil stop devreye girmiştir. Lütfen kontrol ediniz."),
    ("Sürücü Hatası", "Sürücü hatası tespit edilmiştir. Lütfen kontrol ediniz."),
    ("Pompa Arızası", "Pompa arızası tespit edilmiştir. Lütfen kontrol ediniz."),
    ("Kapı Açık", "Tesis kapısı açık konuma geçmiştir. Lütfen kontrol ediniz."),
    ("Tesis Offline", "Tesis ile iletişim kesilmiştir (offline). Lütfen kontrol ediniz."),
    ("Cihaz İletişim Hatası", "Cihaz iletişim hatası tespit edilmiştir. Lütfen kontrol ediniz."),
    ("Numune Alma", "Numune alma işlemi algılanmıştır. Lütfen kontrol ediniz."),
    ("Bakım Modu Aktif", "Tesis bakım moduna alınmıştır. Lütfen kontrol ediniz."),
]


class Command(BaseCommand):
    help = "Çekirdek SCADA seed kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
        created_station_types = 0
        station_type_by_code = {}
        for code, name, description in DEFAULT_STATION_TYPES:
            obj, created = StationType.objects.get_or_create(
                code=code,
                defaults={"name": name, "description": description},
            )
            station_type_by_code[code] = obj
            created_station_types += int(created)

        default_station, station_created = Station.objects.get_or_create(
            id=1,
            defaults={
                "name": "Varsayılan Tesis",
                "active": True,
                "station_type": station_type_by_code.get("wastewater_monitoring"),
            },
        )
        if station_created:
            self.stdout.write(self.style.SUCCESS("Varsayılan istasyon (id=1) oluşturuldu."))

        # Tip artık zorunlu; eski kurulumda tipsiz kalmış varsayılan istasyonu doldur.
        if default_station.station_type_id is None:
            default_station.station_type = station_type_by_code.get("wastewater_monitoring")
            default_station.save(update_fields=["station_type"])

        created_params = 0
        for row in DEFAULT_PARAMETERS:
            (
                parameter_name, parameter_txt, unit, unit_txt, device_channel_id,
                gec_min, gec_max, olcum_min, olcum_max, min_range, max_range,
            ) = row
            _, created = Parameter.objects.get_or_create(
                parameter_name=parameter_name,
                station=default_station,
                defaults=dict(
                    parameter_txt=parameter_txt,
                    unit=unit,
                    unit_txt=unit_txt,
                    device_channel_id=device_channel_id,
                    gec_min=gec_min,
                    gec_max=gec_max,
                    olcum_min=olcum_min,
                    olcum_max=olcum_max,
                    min_range=min_range,
                    max_range=max_range,
                ),
            )
            created_params += int(created)

        for parameter_name, parameter_txt, unit_txt in CHANNEL_ONLY:
            _, created = Parameter.objects.get_or_create(
                parameter_name=parameter_name,
                station=default_station,
                defaults=dict(
                    parameter_txt=parameter_txt,
                    unit_txt=unit_txt,
                ),
            )
            created_params += int(created)

        created_status = 0
        for code, name in DEFAULT_STATUS_CODES:
            _, created = StatusCode.objects.get_or_create(
                code=code,
                defaults={"name": name},
            )
            created_status += int(created)

        created_request = 0
        for code, name in DEFAULT_REQUEST_TYPES:
            _, created = RequestType.objects.get_or_create(
                code=code,
                defaults={"name": name},
            )
            created_request += int(created)

        created_msg = 0
        for title, body in DEFAULT_MESSAGE_TEMPLATES:
            _, created = MessageTemplate.objects.get_or_create(
                title=title,
                defaults={"body": body, "channel": "both"},
            )
            created_msg += int(created)

        # Olay (event) kategorileri — merkezi log_event motifinin kullandığı
        # LogType kayıtları. Olay yazımı zaten get_or_create yapar; burada
        # önceden tohumlamak rapor filtresinde tüm tiplerin görünmesini sağlar.
        from api.events import EventType

        created_logtype = 0
        for name in (
            EventType.LOGIN, EventType.LOGOUT, EventType.LOGIN_FAILED,
            EventType.COMMAND, EventType.DIGITAL_IO, EventType.CONFIG,
            EventType.USER_MGMT, EventType.BACKUP, EventType.LICENSE,
            EventType.SCENARIO, EventType.CALIBRATION, EventType.SYSTEM,
        ):
            _, created = LogType.objects.get_or_create(name=name)
            created_logtype += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"İstasyon tipleri: {created_station_types} yeni, "
                f"Parametreler: {created_params} yeni, "
                f"Status kodları: {created_status} yeni, "
                f"Talep tipleri: {created_request} yeni, "
                f"Hazır mesajlar: {created_msg} yeni, "
                f"Olay tipleri: {created_logtype} yeni."
            )
        )
