from django.db import migrations
from django.utils import timezone


def backfill_setup_state(apps, schema_editor):
    """Zaten yapılandırılmış kurulumları 'tamamlandı' say.

    Yeni `SetupState` varsayılan olarak `completed=False` gelir; bu da mevcut
    (üretim/dev) kurulumlarda yöneticiyi yanlışlıkla kurulum sihirbazına
    yönlendirir. Burada sistemde gerçek yapılandırma izi varsa (en az bir
    Connection ya da varsayılan placeholder dışında/ek bir Station) sihirbazı
    tamamlanmış kabul ederiz. Yalnızca tertemiz (yeni seed'lenmiş) kurulumlarda
    `completed=False` kalır ve sihirbaz ilk açılışta gösterilir.
    """
    SetupState = apps.get_model("api", "SetupState")
    Connection = apps.get_model("api", "Connection")
    Station = apps.get_model("api", "Station")

    already_configured = (
        Connection.objects.exists()
        or Station.objects.exclude(name="Varsayılan İstasyon").exists()
        or Station.objects.count() > 1
    )
    if already_configured:
        SetupState.objects.update_or_create(
            pk=1,
            defaults={"completed": True, "completed_at": timezone.now()},
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0023_setupstate"),
    ]

    operations = [
        migrations.RunPython(backfill_setup_state, noop_reverse),
    ]
