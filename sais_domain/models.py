"""
SAIS'e (atıksu sürekli izleme) özgü, alan-özel uzantı modelleri.

Çekirdek `api` uygulaması jenerik SCADA kavramları barındırır; Envisoft
entegrasyonu, Bakanlık talep tipi, atıksu istasyon tipleri, Bakanlık
SAIS kabin kayıtları gibi SAIS platformuna özel bilgiler buraya
izole edilir.
"""
from django.db import models

from users.models import CustomUser


class SaisCabinet(models.Model):
    """Çevre ve Şehircilik Bakanlığı SAIS kabin kaydı.

    Bakanlık her SAIS (Sürekli Atıksu İzleme Sistemi) kabini için bir
    kayıt ID'si (SIM ID), bir kurum kodu ve Bakanlık sistemine veri
    gönderiminde kullanılacak kullanıcı adı/şifre tanımlar. Bu model
    o kayıtları tutar; `api.Station` ile 1:N bağlanır (tek fiziksel
    istasyon için birden fazla Bakanlık kaydı nadiren oluşabilir).
    """

    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE, related_name="sais_cabinets",
        verbose_name="İstasyon",
    )
    device_id = models.CharField(
        max_length=100,
        verbose_name="Bakanlık SIM ID",
        help_text="Bakanlık'ın SAIS kabini için atadığı benzersiz kimlik",
    )
    code = models.CharField(
        max_length=50,
        verbose_name="İstasyon Kodu",
        help_text="Bakanlık istasyon kodu (örn. 30060001)",
    )
    name = models.CharField(
        max_length=200,
        verbose_name="Kabin Adı",
    )
    data_period = models.IntegerField(
        blank=True, null=True, default=1,
        verbose_name="Veri Periyodu (dk)",
        help_text="Bakanlık'a veri gönderim periyodu",
    )
    auth_username = models.CharField(
        max_length=50,
        verbose_name="Bakanlık Kullanıcı Adı",
    )
    # TODO: auth_secret şu an plaintext tutuluyor; ileride django-fernet-fields
    # veya bir KMS katmanı ile şifrelenmeli.
    auth_secret = models.CharField(
        max_length=255,
        verbose_name="Bakanlık Şifresi",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sais_cabinet"
        verbose_name_plural = "SAIS Kabin Kayıtları"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.station} / {self.device_id}"


class EnvisoftChannel(models.Model):
    """`api.Parameter` ↔ Envisoft kanal ID eşlemesi."""

    parameter = models.OneToOneField(
        "api.Parameter",
        on_delete=models.CASCADE,
        related_name="envisoft",
        verbose_name="Parametre",
    )
    envi_channel_id = models.IntegerField(
        verbose_name="Envisoft Kanal ID",
        help_text="Envisoft sistemindeki kanal numarası",
    )

    class Meta:
        db_table = "sais_envisoft_channel"
        verbose_name_plural = "Envisoft Kanal Eşlemeleri"
        ordering = ["envi_channel_id"]

    def __str__(self):
        return f"{self.parameter} → envi#{self.envi_channel_id}"


class SystemSwitch(models.Model):
    """Sistem geneli aç/kapa bayrakları — singleton (pk=1).

    Yönetici dashboard sayfasından (`/dashboard/admin-pages/system-control/`)
    toggle edilir; ilgili Celery task'ları (`publish_cabinet_data`,
    `dispatch_polls`) her tetiklenmede `SystemSwitch.load()` ile bayrakları
    okur ve kapalıysa no-op yapar. Worker/beat process'i çalışmaya devam
    eder — yalnız iş akışı sessizce askıya alınır.
    """

    sim_enabled = models.BooleanField(
        default=True,
        verbose_name="SIM Veri İletimi",
        help_text="Kapalıysa Bakanlık SIM'e SendData çağrıları atlanır.",
    )
    envisoft_enabled = models.BooleanField(
        default=True,
        verbose_name="Envisoft Veri İletimi",
        help_text="Kapalıysa Envisoft SendData çağrıları atlanır.",
    )
    polling_enabled = models.BooleanField(
        default=True,
        verbose_name="Sensör Okuması (Polling)",
        help_text="Kapalıysa dispatch_polls bağlantı enqueue etmez.",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")
    updated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Güncelleyen",
    )

    class Meta:
        db_table = "sais_system_switch"
        verbose_name = "Sistem Anahtarı"
        verbose_name_plural = "Sistem Anahtarları"

    def __str__(self):
        return "Sistem Anahtarları"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Singleton — silinmez
        pass

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
