from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone


class CustomUser(AbstractUser):

    rol_choices = (
        (1, 'Sistem Yöneticisi'),
        (2, 'Operatör'),
        (3, 'Normal Kullanıcı'),
        (4, 'Bakanlık Kullanıcısı'),
    )

    # Oturum (session) otomatik düşme süresi — kullanıcı tercihi. Login'de ve
    # Tercihler sayfasında `request.session.set_expiry()` ile uygulanır.
    # 0 = tarayıcı kapanınca; diğerleri dakika cinsinden. Varsayılan 8 saat.
    session_timeout_choices = (
        (0, 'Tarayıcı kapanınca'),
        (60, '1 saat'),
        (240, '4 saat'),
        (480, '8 saat'),
        (720, '12 saat'),
        (1440, '1 gün'),
        (10080, '7 gün'),
        (43200, '30 gün'),
    )

    rol = models.IntegerField(verbose_name="Rol", help_text="Rol", blank=False,
                              null=True,choices=rol_choices,default=3)
    isDark = models.BooleanField(verbose_name="Tema", help_text="Durum", blank=False, default=False)

    session_timeout_minutes = models.IntegerField(
        verbose_name="Oturum Süresi",
        help_text="Oturumun otomatik düşeceği süre (0 = tarayıcı kapanınca)",
        choices=session_timeout_choices,
        default=480,
    )

    device_id = models.CharField(max_length=250, blank=True, null=True)
    added_by = models.IntegerField(verbose_name="Ekleyen Kullanıcı", help_text="Ekleyen Kullanıcı", blank=False,
                                     null=True,default=1)

    # Bildirim (alarm) tercih alanları — SMS/e-posta alıcı çözümü bunları kullanır.
    phone_number = models.CharField(
        max_length=20, blank=True, default="", verbose_name="Telefon",
        help_text="NetGSM formatı: 5XXXXXXXXX",
    )
    sms_enabled = models.BooleanField(default=False, verbose_name="SMS Bildirimi")
    email_enabled = models.BooleanField(default=False, verbose_name="E-posta Bildirimi")

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Sadece "Sistem Yöneticisi" rolü verildiğinde staff/superuser bayraklarını
        # otomatik aç. Admin'den farklı değerler atansın diye diğer rollerde
        # bayrakları ellemiyoruz (aksi halde createsuperuser sonrası demote oluyordu).
        if self.rol == 1:
            self.is_staff = True
            self.is_superuser = True
        super().save(*args, **kwargs)


