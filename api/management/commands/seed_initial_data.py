"""
Çekirdek SCADA seed verisini oluşturur: varsayılan istasyon, parametreler,
status kodları ve jenerik talep tipleri.

Kullanım:
    python manage.py seed_initial_data

Alan-özel (SAIS / Envisoft / Bakanlık) veriler için ayrı komut:
    python manage.py seed_sais_data
"""
from django.core.management.base import BaseCommand

from api.models import Parameter, RequestType, Station, StatusCode


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


DEFAULT_STATUS_CODES = [
    (0, "Veri Yok"),
    (1, "Veri Geçerli"),
    (4, "Geçersiz Veri"),
    (8, "İletişim Hatası"),
    (12, "Alarm"),
    (15, "Purge"),
    (23, "Yıkama"),
    (24, "Haftalık Yıkama"),
    (25, "İstasyon Bakımda"),
    (26, "Tesis Bakımda"),
    (39, "Ölçüm Aralığı Dışında"),
]


# Jenerik SCADA talep tipleri (SAIS-özel olanlar sais_domain seed'inde).
DEFAULT_REQUEST_TYPES = [
    ("operator", "Operatör Talebi"),
    ("auto_scenario", "Otomatik Numune Senaryosu"),
    ("manual_output", "Manuel Çıkış Kontrolü"),
]


class Command(BaseCommand):
    help = "Çekirdek SCADA seed kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
        default_station, station_created = Station.objects.get_or_create(
            id=1,
            defaults={
                "name": "Varsayılan İstasyon",
                "active": True,
            },
        )
        if station_created:
            self.stdout.write(self.style.SUCCESS("Varsayılan istasyon (id=1) oluşturuldu."))

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

        self.stdout.write(
            self.style.SUCCESS(
                f"Parametreler: {created_params} yeni, "
                f"Status kodları: {created_status} yeni, "
                f"Talep tipleri: {created_request} yeni."
            )
        )
