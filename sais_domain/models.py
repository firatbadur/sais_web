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

    Manuel/haftalık yıkama state'i de burada tutulur (singleton — aynı anda
    yalnız bir yıkama aktif olabilir): yıkama tetiklendiğinde belirli süre
    boyunca SIM + Envisoft payload'larındaki tüm status kodları 23 (manuel)
    veya 24 (haftalık) ile override edilir.
    """

    # --- Veri akışı bayrakları ---
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

    # --- Yıkama süreleri (kullanıcı ayarı, kalıcı) ---
    manual_wash_duration_minutes = models.IntegerField(
        default=5,
        verbose_name="Manuel Yıkama Süresi (dk)",
        help_text="Manuel yıkama başlatıldığında varsayılan süre.",
    )
    weekly_wash_duration_minutes = models.IntegerField(
        default=20,
        verbose_name="Haftalık Yıkama Süresi (dk)",
        help_text="Haftalık yıkama başlatıldığında varsayılan süre.",
    )

    # --- Aktif yıkama state'i ---
    WASH_KIND_CHOICES = (
        ("manual", "Manuel Yıkama"),
        ("weekly", "Haftalık Yıkama"),
    )
    # StatusCode.code eşlemesi — seed_initial_data.py ile birebir.
    WASH_STATUS_CODE = {"manual": 23, "weekly": 24}

    wash_active_kind = models.CharField(
        max_length=10, choices=WASH_KIND_CHOICES, null=True, blank=True,
        verbose_name="Aktif Yıkama Tipi",
    )
    wash_started_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Yıkama Başlangıcı",
    )
    wash_ends_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Yıkama Bitişi",
        help_text="Bu zaman geçtikten sonra yıkama otomatik sonlanır.",
    )
    wash_started_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
        verbose_name="Yıkamayı Başlatan",
    )

    # --- Bakanlık SIM'e başarıyla iletilen son veri (HTTP 200) ---
    last_sim_success_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="SIM'e Son İletim Zamanı",
        help_text="Bakanlık SendData çağrısının en son 200 (kabul) döndüğü an.",
    )
    last_sim_success_readtime = models.CharField(
        max_length=25, null=True, blank=True,
        verbose_name="SIM'e İletilen Son Veri Tarihi",
        help_text="Bakanlık'ın 200 ile kabul ettiği verinin dakika damgası.",
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

    def active_wash_status_code(self):
        """Aktif yıkama varsa override edilecek status kodunu (23/24) döner.

        Süresi dolmuş veya hiç başlatılmamışsa None döner. DB'yi temizlemez —
        okuyucular kısa-circuit'lar; clear işlemi `publish_cabinet_data`
        içinde lazy yapılır (her dakika çalıştığı için pratik).
        """
        from django.utils import timezone
        if not self.wash_active_kind or not self.wash_ends_at:
            return None
        if timezone.now() >= self.wash_ends_at:
            return None
        return self.WASH_STATUS_CODE.get(self.wash_active_kind)

    def wash_remaining_seconds(self):
        """Aktif yıkama kalan saniye sayısı (None döner aktif değilse)."""
        from django.utils import timezone
        if not self.wash_active_kind or not self.wash_ends_at:
            return None
        remaining = (self.wash_ends_at - timezone.now()).total_seconds()
        return max(0, int(remaining))

    @classmethod
    def mark_sim_success(cls, readtime):
        """Bakanlık SendData'sı 200 ile kabul edince çağrılır — singleton'a
        son başarılı iletim zamanını + veri dakikasını yazar (hafif update)."""
        from django.utils import timezone
        cls.objects.filter(pk=1).update(
            last_sim_success_at=timezone.now(),
            last_sim_success_readtime=(readtime or "")[:25] or None,
        )


class SimStatusPolicy(models.Model):
    """Bakanlık SIM'e hangi status kodlarının iletileceğini belirleyen tekil
    politika (singleton, pk=1).

    Saha pratiği: Bakanlık'a yalnız **operasyonel** statuslar (Yıkama 23,
    Haftalık Yıkama 24, İstasyon Bakımda 25, Tesis Bakımda 26) raporlanmak
    istenir; diğer (alarm, iletişim hatası, ölçüm aralığı dışı vb.) statuslar
    Bakanlık tarafında gereksiz ihlal/uyarı doğurmasın diye **bastırılır** —
    SendData payload'unda `fallback_status_code` (varsayılan 1 = Veri Geçerli)
    ile değiştirilir.

    `blocked_statuses` boş + `configured=False` iken davranış değişmez (tüm
    statuslar olduğu gibi gider) — yönetici sayfadan kaydedene dek mevcut
    raporlama korunur. Yıkama override'ı (force_status) bu politikadan
    bağımsızdır; aktif yıkamada zaten tüm statuslar 23/24 ile zorlanır.
    """

    # Bakanlık'a her zaman raporlanması beklenen operasyonel status kodları.
    OPERATIONAL_CODES = (23, 24, 25, 26)

    blocked_statuses = models.ManyToManyField(
        "api.StatusCode",
        blank=True,
        related_name="+",
        verbose_name="SIM'e Gönderilmeyen Statuslar",
        help_text="İşaretli statuslar Bakanlık'a iletilmez; yerine fallback kod gönderilir.",
    )
    fallback_status_code = models.IntegerField(
        default=1,
        verbose_name="Yerine Gönderilecek Kod",
        help_text="Engellenen statusların yerine yazılacak StatusCode.code (varsayılan 1 = Veri Geçerli).",
    )
    configured = models.BooleanField(
        default=False,
        verbose_name="Yapılandırıldı",
        help_text="Yönetici en az bir kez kaydetti mi? False iken filtre uygulanmaz.",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")
    updated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
        verbose_name="Güncelleyen",
    )

    class Meta:
        db_table = "sais_sim_status_policy"
        verbose_name = "SIM Status Politikası"
        verbose_name_plural = "SIM Status Politikaları"

    def __str__(self):
        return "SIM Status Politikası"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — silinmez

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def blocked_code_set(self):
        """Engellenen status kodlarının kümesi. `configured=False` ise boş küme
        (filtre uygulanmaz — mevcut davranış korunur)."""
        if not self.configured:
            return set()
        return set(self.blocked_statuses.values_list("code", flat=True))
