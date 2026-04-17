"""
SAIS'e (atıksu sürekli izleme) özgü seed kayıtlarını oluşturur.

Kullanım:
    python manage.py seed_sais_data

İdempotenttir; birden çok kez çalıştırmak güvenlidir.
"""
from django.core.management.base import BaseCommand

from api.models import Parameter, RequestType, StationType
from sais_domain.models import EnvisoftChannel


SAIS_STATION_TYPES = [
    ("wastewater_domestic", "Evsel Atıksu", "Yerleşimden kaynaklanan atıksu"),
    ("wastewater_industrial", "Endüstriyel Atıksu", "Sanayi ve ticari faaliyetlerden gelen atıksu"),
    ("wastewater_municipal", "Kentsel Atıksu", "Evsel, endüstriyel ve/veya yağmur suyu karışımı"),
]


SAIS_REQUEST_TYPES = [
    ("ministry_sample", "Bakanlık Numune Talebi"),
]


# (parameter_name, envi_channel_id) — core seed'de oluşturulan Parameter'lar için
# Envisoft kanal eşlemeleri. parameter_name değeri `api.seed_initial_data` ile
# senkron tutulmalı.
SAIS_ENVI_CHANNEL_MAP = [
    ("pH", 0),
    ("Iletkenlik", 1),
    ("CozunmusOksijen", 2),
    ("Debi", 3),
    ("Sicaklik", 4),
    ("AkisHizi", 5),
    ("KOi", 6),
    ("AKM", 7),
    ("KabinSicaklik", 7),
    ("KabinNem", 8),
    ("GunlukDebi", 9),
    ("GirisDebi", 10),
    ("CikisDebi", 11),
    ("Pompa1", 50),
    ("Pompa2", 51),
    ("Yikama", 52),
    ("HaftalikYikama", 53),
    ("Bakim", 54),
    ("Enerji", 55),
    ("SuBasti", 56),
    ("Duman", 57),
    ("SuYok", 58),
    ("NumuneAlma", 59),
    ("Surucu1", 60),
    ("Surucu2", 61),
    ("Surucu3", 62),
    ("İstasyonBakimda", 63),
    ("Kapi", 64),
    ("AcilStop", 65),
    ("TesisBakimda", 66),
    ("SistemKapali", 67),
    ("Kalibrasyon", 68),
    ("Ups", 69),
    ("ManuelYikama", 70),
    ("SistemDurdurma", 71),
    ("BakimModu", 72),
    ("AcilStopOut", 73),
    ("Numune1Dolu", 74),
    ("Numune2Dolu", 75),
    ("Numune3Dolu", 76),
    ("Numune4Dolu", 77),
    ("Sicaklik", 78),
    ("NumuneReset", 79),
    ("NumuneAnlik", 80),
    ("Surucu1Hata", 81),
    ("Surucu2Hata", 82),
    ("SenaryoSifirla", 83),
    ("Desarj", 84),
]


class Command(BaseCommand):
    help = "SAIS alan-özel seed kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
        st_created = 0
        for code, name, description in SAIS_STATION_TYPES:
            _, created = StationType.objects.get_or_create(
                code=code, defaults={"name": name, "description": description},
            )
            st_created += int(created)

        rt_created = 0
        for code, name in SAIS_REQUEST_TYPES:
            _, created = RequestType.objects.get_or_create(
                code=code, defaults={"name": name},
            )
            rt_created += int(created)

        env_created = 0
        for parameter_name, envi_id in SAIS_ENVI_CHANNEL_MAP:
            param = Parameter.objects.filter(parameter_name=parameter_name).first()
            if not param:
                continue
            _, created = EnvisoftChannel.objects.get_or_create(
                parameter=param, defaults={"envi_channel_id": envi_id},
            )
            env_created += int(created)

        self.stdout.write(self.style.SUCCESS(
            f"İstasyon Tipleri: {st_created} yeni, "
            f"Talep Tipleri: {rt_created} yeni, "
            f"Envisoft Eşleme: {env_created} yeni."
        ))
