"""3B (Three.js) mimik desteği geri alındı — `kind` alanı kaldırılır.

`RemoveField`'dan ÖNCE bir veri adımı çalışır: `kind="3d"` kayıtlar SİLİNİR.
Sebep: kolon kalkınca o satırlar sıradan (2B) mimik gibi görünür, ama `data`
alanları `mimic3d/1` sahne belgesi olduğu için Fabric editörü/görüntüleyicisi
onları açamaz — galeride tıklandığında boş/bozuk ekran veren kayıtlar kalırdı.

Geri alınabilirlik: `kind` alanı geri eklenebilir (varsayılan "2d"), ancak
silinen 3B sahneler geri gelmez (bilinçli; 3B özelliği kaldırıldı).
"""
from django.db import migrations, models


def delete_3d_screens(apps, schema_editor):
    MimicScreen = apps.get_model("dashboard", "MimicScreen")
    qs = MimicScreen.objects.filter(kind="3d")
    n = qs.count()
    if n:
        qs.delete()
        print("  3B mimik kaydı silindi: %s" % n)


def noop(apps, schema_editor):
    """Geri alma: silinen kayıtlar geri getirilemez (veri kaybı bilinçli)."""


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0003_mimicscreen_kind"),
    ]

    operations = [
        migrations.RunPython(delete_3d_screens, noop),
        migrations.RemoveField(
            model_name="mimicscreen",
            name="kind",
        ),
        migrations.AlterField(
            model_name="mimicscreen",
            name="data",
            field=models.JSONField(
                blank=True, default=dict,
                help_text="Fabric.js canvas.toJSON() çıktısı (objeler + bağlama meta verisi).",
                verbose_name="Tuval Verisi",
            ),
        ),
    ]
