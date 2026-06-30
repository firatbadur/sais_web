"""Bakanlık SAIS veri durum kodlarının tamamını StatusCode tablosuna ekler.

Eski kurulumlarda yalnız 11 kod seed'lenmişti; sistem alarmları + SIM status
filtresi için tüm Bakanlık kodları (GetDataStatusDescription ile birebir)
gerekiyor. ``migrate`` ile sahalara otomatik gelir (idempotent — mevcut kodlara
dokunmaz). Yeni kurulumlar zaten ``seed_initial_data`` ile alır.
"""
from django.db import migrations


STATUS_CODES = [
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


def seed_status_codes(apps, schema_editor):
    StatusCode = apps.get_model("api", "StatusCode")
    for code, name in STATUS_CODES:
        StatusCode.objects.get_or_create(code=code, defaults={"name": name})


def noop_reverse(apps, schema_editor):
    # Geri alma kod silmez (başka kayıtlar referans veriyor olabilir).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0026_backfill_sensor_tags"),
    ]

    operations = [
        migrations.RunPython(seed_status_codes, noop_reverse),
    ]
