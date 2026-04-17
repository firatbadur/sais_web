from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone


class CustomUser(AbstractUser):

    rol_choices = (
        (1, 'Sistem Yöneticisi'),
        (2, 'Operatör'),
        (3, 'Normal Kullanıcı'),
    )

    rol = models.IntegerField(verbose_name="Rol", help_text="Rol", blank=False,
                              null=True,choices=rol_choices,default=3)
    isDark = models.BooleanField(verbose_name="Tema", help_text="Durum", blank=False, default=False)

    device_id = models.CharField(max_length=250, blank=True, null=True)
    added_by = models.IntegerField(verbose_name="Ekleyen Kullanıcı", help_text="Ekleyen Kullanıcı", blank=False,
                                     null=True,default=1)
    created_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.rol == 1:  # Check if rol is Sistem Yöneticisi
            self.is_staff = True
            self.is_superuser = True
        else:
            self.is_staff = False
            self.is_superuser = False

        super().save(*args, **kwargs)


