"""
Varsayılan Parametre ve Status kodu kayıtlarını oluşturur.

Kullanım:
    python manage.py seed_initial_data
"""
from django.core.management.base import BaseCommand

from api.models import Parameters, Status_Codes


DEFAULT_PARAMETERS = [
    # (parameter_name, parameter_txt, unit, unit_txt, sim_channel, envi_channel,
    #  gec_min, gec_max, olcum_min, olcum_max, min_range, max_range)
    ("pH", "pH", "7e84ef31-518b-495f-ad2d-ccc8824dbeb3", "--",
     "600101fd-b533-4b2a-951f-37a6c13aab64", 0, 6, 9, 0, 14, 0, 14),
    ("Iletkenlik", "İletkenlik", "cbbf854a-5f7a-4552-a26b-77342217e4e4", "uS/cm",
     "1650b204-0801-4ba3-a435-345e8374323f", 1, 0, 100000, 0, 100000, 0, 100000),
    ("CozunmusOksijen", "Çözünmüs Oksijen", "dc0f15a2-b2eb-4760-9c28-ff255c6d16d5", "mg/l",
     "67dc7a29-b7ec-4d3f-b886-ed43a1847af3", 2, 0, 100, 0, 100, 0, 100),
    ("Debi", "Debi", "6e78e970-89d2-4479-8448-03017edf2319", "m3/dakika",
     "faefdd30-61d6-4f02-9fe3-066c03139e66", 3, 0, 100000, 0, 100000, 0, 100000),
    ("Sicaklik", "Sıcaklık", "53ea78cb-9e85-44b9-b376-317e7844f056", "°C",
     "f22fa333-ecbd-4538-b4c3-3b4e56917a46", 4, 0, 100, 0, 100, -40, 100),
    ("AkisHizi", "Akış Hızı", "185480e6-da5e-43ba-9b25-1d7f8cb8eebb", "m/sn",
     "c292b027-36eb-4f33-aff2-1ee94df949e9", 5, 0, 1, 0, 1, 0, 1),
    ("KOi", "KOi", "dc0f15a2-b2eb-4760-9c28-ff255c6d16d5", "mg/l",
     "a189a5c8-cf82-4d44-825c-0b9a40e5f7c2", 6, 0, 125, 0, 10000, 0, 10000),
    ("AKM", "AKM", "dc0f15a2-b2eb-4760-9c28-ff255c6d16d5", "mg/l",
     "4c699eff-7a09-46c0-b4ef-a208d2866f0b", 7, 0, 35, 0, 10000, 0, 10000),
]

# Kanal bazlı dijital/statü parametreleri
CHANNEL_ONLY = [
    (7, "KabinSicaklik", "Kabin Sıcaklık", "°C"),
    (8, "KabinNem", "Kabin Nem", "%"),
    (9, "GunlukDebi", "Günlük Debi", "m3/gun"),
    (10, "GirisDebi", "Giriş Debi", "m3/dk"),
    (11, "CikisDebi", "Çıkış Debi", "m3/dk"),
    (50, "Pompa1", "Pompa-1", ""),
    (51, "Pompa2", "Pompa-2", ""),
    (52, "Yikama", "Yıkama", ""),
    (53, "HaftalikYikama", "Haftalık Yıkama", ""),
    (54, "Bakim", "Bakım", ""),
    (55, "Enerji", "Enerji", ""),
    (56, "SuBasti", "SuBasti", ""),
    (57, "Duman", "Duman", ""),
    (58, "SuYok", "Su Yok", ""),
    (59, "NumuneAlma", "Numune Kompozit", ""),
    (60, "Surucu1", "Sürücü-1", ""),
    (61, "Surucu2", "Sürücü-2", ""),
    (62, "Surucu3", "Sürücü-3", ""),
    (63, "İstasyonBakimda", "İstasyon Bakımda", ""),
    (64, "Kapi", "Kapı", ""),
    (65, "AcilStop", "Acil Stop", ""),
    (66, "TesisBakimda", "Tesis Bakimda", ""),
    (67, "SistemKapali", "Sistem Kapalı", ""),
    (68, "Kalibrasyon", "Kalibrasyon", ""),
    (69, "Ups", "Ups", ""),
    (70, "ManuelYikama", "Manuel Yıkama", ""),
    (71, "SistemDurdurma", "Sistem Durdurma", ""),
    (72, "BakimModu", "Bakım Modu", ""),
    (73, "AcilStopOut", "Acil Stop Out", ""),
    (74, "Numune1Dolu", "Numune-1 Dolu", ""),
    (75, "Numune2Dolu", "Numune-2 Dolu", ""),
    (76, "Numune3Dolu", "Numune-3 Dolu", ""),
    (77, "Numune4Dolu", "Numune-4 Dolu", ""),
    (78, "Sicaklik", "Sıcaklık", ""),
    (79, "NumuneReset", "NumuneReset", ""),
    (80, "NumuneAnlik", "Numune Anlık", ""),
    (81, "Surucu1Hata", "Sürücü-1-Hata", ""),
    (82, "Surucu2Hata", "Sürücü-2-Hata", ""),
    (83, "SenaryoSifirla", "Senaryo Sıfırla", ""),
    (84, "Desarj", "Desarj", ""),
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


class Command(BaseCommand):
    help = "Varsayılan parametre ve status kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
        created_params = 0
        for row in DEFAULT_PARAMETERS:
            (
                parameter_name, parameter_txt, unit, unit_txt, sim_channel,
                envi_channel, gec_min, gec_max, olcum_min, olcum_max, min_range, max_range,
            ) = row
            _, created = Parameters.objects.get_or_create(
                parameter_name=parameter_name,
                defaults=dict(
                    parameter_txt=parameter_txt,
                    unit=unit,
                    unit_txt=unit_txt,
                    sim_channel=sim_channel,
                    envi_channel=envi_channel,
                    gec_min=gec_min,
                    gec_max=gec_max,
                    olcum_min=olcum_min,
                    olcum_max=olcum_max,
                    min_range=min_range,
                    max_range=max_range,
                ),
            )
            created_params += int(created)

        for envi_channel, parameter_name, parameter_txt, unit_txt in CHANNEL_ONLY:
            _, created = Parameters.objects.get_or_create(
                parameter_name=parameter_name,
                defaults=dict(
                    parameter_txt=parameter_txt,
                    unit_txt=unit_txt,
                    envi_channel=envi_channel,
                ),
            )
            created_params += int(created)

        created_status = 0
        for code, name in DEFAULT_STATUS_CODES:
            _, created = Status_Codes.objects.get_or_create(
                code=code,
                defaults={"name": name},
            )
            created_status += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Parametreler: {created_params} yeni kayıt, "
                f"Status kodlar: {created_status} yeni kayıt."
            )
        )
