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

    Manuel durum override state'i de burada tutulur (singleton — aynı anda
    yalnız bir override aktif olabilir): manuel yıkama / haftalık yıkama veya
    manuel bakım modu (istasyon/tesis bakımda) tetiklendiğinde belirli süre
    boyunca SIM + Envisoft payload'larındaki tüm status kodları ilgili kod ile
    override edilir: 23 (manuel yıkama), 24 (haftalık yıkama), 25 (istasyon
    bakımda), 26 (tesis bakımda).
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
    station_maint_duration_minutes = models.IntegerField(
        default=60,
        verbose_name="İstasyon Bakım Süresi (dk)",
        help_text="İstasyon bakım modu başlatıldığında varsayılan süre.",
    )
    facility_maint_duration_minutes = models.IntegerField(
        default=120,
        verbose_name="Tesis Bakım Süresi (dk)",
        help_text="Tesis bakım modu başlatıldığında varsayılan süre.",
    )

    # --- Aktif manuel durum override state'i (yıkama / bakım) ---
    WASH_KIND_CHOICES = (
        ("manual", "Manuel Yıkama"),
        ("weekly", "Haftalık Yıkama"),
        ("station_maint", "İstasyon Bakımda"),
        ("facility_maint", "Tesis Bakımda"),
    )
    # StatusCode.code eşlemesi — seed_initial_data.py ile birebir.
    WASH_STATUS_CODE = {
        "manual": 23,
        "weekly": 24,
        "station_maint": 25,
        "facility_maint": 26,
    }

    wash_active_kind = models.CharField(
        max_length=20, choices=WASH_KIND_CHOICES, null=True, blank=True,
        verbose_name="Aktif Override Tipi",
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

    # --- Eksik veri yeniden gönderim servisi (resend_missing_data) durumu ---
    last_missing_check_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Eksik Veri Son Kontrol",
        help_text="GetMissingDates servisinin en son çalıştığı an (6 saatte bir).",
    )
    last_missing_found_count = models.IntegerField(
        default=0,
        verbose_name="Son Bulunan Eksik Dakika",
        help_text="Son kontrolde Bakanlık'ın eksik bildirdiği dakika sayısı.",
    )
    last_missing_resent_count = models.IntegerField(
        default=0,
        verbose_name="Son Yeniden Gönderilen",
        help_text="Son kontrolde başarıyla yeniden gönderilen eksik dakika sayısı.",
    )
    last_missing_error = models.CharField(
        max_length=300, null=True, blank=True,
        verbose_name="Eksik Veri Son Hata",
        help_text="Son kontrol hata ile bittiyse mesajı; başarılıysa boş.",
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

    @classmethod
    def mark_missing_run(cls, *, found, resent, error=""):
        """Eksik veri yeniden gönderim run'ı bitince çağrılır — durum damgasını
        yazar (Sistem Kontrol sayfasında görüntülenir). Tüm kabinler toplanmış
        sayılarla bir kez çağrılır."""
        from django.utils import timezone
        cls.objects.filter(pk=1).update(
            last_missing_check_at=timezone.now(),
            last_missing_found_count=int(found or 0),
            last_missing_resent_count=int(resent or 0),
            last_missing_error=(error or "")[:300] or None,
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


# ---------------------------------------------------------------------------
# Numune alma senaryosu — no-code kurucu + yürütme motoru
# ---------------------------------------------------------------------------
#
# Eski yazılımdaki sabit-kodlu `controlSampleValues` akışı (3 kademeli alarm +
# 24s tamamlama + Bakanlık talepli yol) jenerik bir "senaryo yorumlayıcı" haline
# getirildi: kullanıcı tetik kombinasyonunu, adımların sürelerini ve aksiyonlarını
# dashboard'dan kurar; `sais_domain.scenario_engine` her dakika değerlendirir.


class Scenario(models.Model):
    """Numune alma senaryosu — kütüphane satırı.

    Bir senaryo izlenecek parametreleri/eşikleri (`ScenarioParameter`) ve
    zamanlanmış aksiyon adımlarını (`ScenarioStep`) bir araya getirir. Aynı
    `(station, kind)` için en çok bir senaryo `is_active` olur; motor yalnız
    aktif senaryoyu yürütür. `is_builtin` senaryolar (varsayılan otomatik +
    Bakanlık talepli şablonlar) `seed_sais_data` ile gelir ve silinemez.
    """

    KIND_AUTO = "auto"
    KIND_MINISTRY = "ministry"
    KIND_CHOICES = (
        (KIND_AUTO, "Otomatik (eşik tetikli)"),
        (KIND_MINISTRY, "Bakanlık Talepli"),
    )

    TRIGGER_ANY = "any"
    TRIGGER_ALL = "all"
    TRIGGER_N = "n_of_m"
    TRIGGER_CHOICES = (
        (TRIGGER_ANY, "Herhangi biri aşınca"),
        (TRIGGER_ALL, "Hepsi birlikte aşınca"),
        (TRIGGER_N, "En az N tanesi aşınca"),
    )

    WINDOW_5M = "5m"
    WINDOW_15M = "15m"
    WINDOW_CHOICES = (
        (WINDOW_5M, "Son 5 dakika ortalaması"),
        (WINDOW_15M, "Son 15 dakika ortalaması"),
    )

    name = models.CharField(max_length=150, verbose_name="Senaryo Adı")
    description = models.TextField(blank=True, default="", verbose_name="Açıklama")
    kind = models.CharField(
        max_length=10, choices=KIND_CHOICES, default=KIND_AUTO,
        verbose_name="Senaryo Türü",
    )
    is_builtin = models.BooleanField(
        default=False, verbose_name="Yerleşik Şablon",
        help_text="Yerleşik şablonlar silinemez (çoğaltılıp düzenlenebilir).",
    )
    is_active = models.BooleanField(
        default=False, verbose_name="Aktif",
        help_text="Aynı istasyon + tür için yalnız bir senaryo aktif olabilir.",
    )
    enabled = models.BooleanField(default=True, verbose_name="Etkin")
    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE,
        blank=True, null=True,
        related_name="sample_scenarios", verbose_name="İstasyon",
        help_text="Boş senaryolar şablondur; aktif edilmeden önce istasyon atanır.",
    )
    avg_window = models.CharField(
        max_length=4, choices=WINDOW_CHOICES, default=WINDOW_15M,
        verbose_name="Ortalama Penceresi",
    )
    trigger_mode = models.CharField(
        max_length=10, choices=TRIGGER_CHOICES, default=TRIGGER_ANY,
        verbose_name="Tetik Kombinasyonu",
    )
    trigger_n = models.IntegerField(
        default=1, verbose_name="N (en az kaç parametre)",
        help_text="trigger_mode='n_of_m' ise kaç parametrenin aşması gerektiği.",
    )
    sampler_sensor = models.ForeignKey(
        "api.Sensor", on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="Numune Alıcı Sensör (override)",
        help_text="Boşsa istasyonun sample_request_sensor'ı kullanılır.",
    )
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True, related_name="+",
        verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")

    class Meta:
        db_table = "sais_scenario"
        verbose_name = "Numune Senaryosu"
        verbose_name_plural = "Numune Senaryoları"
        ordering = ["station", "kind", "-is_active", "name"]

    def __str__(self):
        return self.name

    def effective_sampler_sensor(self):
        """Override sensör veya istasyonun sample_request_sensor'ı."""
        if self.sampler_sensor_id:
            return self.sampler_sensor
        return self.station.sample_request_sensor if self.station_id else None

    def cabinet(self):
        """SIM bildirimleri için bağlı Bakanlık kabini (tek-kabin varsayımı)."""
        if not self.station_id:
            return None
        return self.station.sais_cabinets.first()

    def aggregate_model(self):
        """avg_window'a karşılık gelen aggregate modeli."""
        from api.models import ReadingFifteenMin, ReadingFiveMin
        return ReadingFiveMin if self.avg_window == self.WINDOW_5M else ReadingFifteenMin

    def activate(self):
        """Bu senaryoyu aktif yap, aynı (istasyon, tür) içindeki diğerlerini pasifle."""
        Scenario.objects.filter(
            station_id=self.station_id, kind=self.kind,
        ).exclude(pk=self.pk).update(is_active=False)
        if not self.is_active:
            self.is_active = True
            self.save(update_fields=["is_active", "updated_at"])


class ScenarioParameter(models.Model):
    """Senaryoda izlenen parametre + eşik değerleri."""

    scenario = models.ForeignKey(
        Scenario, on_delete=models.CASCADE, related_name="parameters",
        verbose_name="Senaryo",
    )
    parameter = models.ForeignKey(
        "api.Parameter", on_delete=models.CASCADE, related_name="+",
        verbose_name="Parametre",
    )
    min_value = models.FloatField(blank=True, null=True, verbose_name="Alt Sınır")
    max_value = models.FloatField(blank=True, null=True, verbose_name="Üst Sınır")
    ministry_param_code = models.CharField(
        max_length=50, blank=True, default="",
        verbose_name="Bakanlık Parametre Kodu",
        help_text="GetSampleCode için; boşsa parametre adı kullanılır.",
    )
    enabled = models.BooleanField(default=True, verbose_name="Etkin")

    class Meta:
        db_table = "sais_scenario_parameter"
        verbose_name = "Senaryo Parametresi"
        verbose_name_plural = "Senaryo Parametreleri"
        ordering = ["scenario", "parameter"]
        constraints = [
            models.UniqueConstraint(
                fields=["scenario", "parameter"], name="sais_scenario_param_unique",
            ),
        ]

    def __str__(self):
        return f"{self.scenario} / {self.parameter}"

    def ministry_code(self):
        return self.ministry_param_code or (self.parameter.parameter_name or "")


class ScenarioStep(models.Model):
    """Senaryonun sıralı bir adımı: tetikten N saniye sonra çalışan aksiyonlar.

    `after_seconds` tetik anından (run.trigger_at) itibaren mutlak offset'tir;
    motor `after_seconds <= geçen_süre` olunca adımı işler (monoton — kaçan bir
    dakikalık tick adımı atlamaz). `actions` JSON listesidir; her eleman
    ``{"type": "...", ...}`` biçiminde (bkz. scenario_engine._execute_step).
    """

    scenario = models.ForeignKey(
        Scenario, on_delete=models.CASCADE, related_name="steps",
        verbose_name="Senaryo",
    )
    order = models.IntegerField(default=0, verbose_name="Sıra")
    label = models.CharField(max_length=150, blank=True, default="", verbose_name="Adım Adı")
    after_seconds = models.IntegerField(
        default=0, verbose_name="Tetikten Sonra (sn)",
        help_text="Tetik anından bu kadar saniye sonra adım işlenir.",
    )
    require_still_exceeded = models.BooleanField(
        default=False, verbose_name="Koşul Hâlâ Sağlanmalı",
        help_text="Açıksa, vade geldiğinde tetik koşulu hâlâ geçerliyse çalışır.",
    )
    actions = models.JSONField(
        default=list, blank=True, verbose_name="Aksiyonlar",
        help_text="Aksiyon listesi: notify / sampler_on / sampler_off / "
                  "ministry_get_code / sim_sample_start / sim_sample_complete / "
                  "sim_sample_error / send_diagnostic.",
    )

    class Meta:
        db_table = "sais_scenario_step"
        verbose_name = "Senaryo Adımı"
        verbose_name_plural = "Senaryo Adımları"
        ordering = ["scenario", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["scenario", "order"], name="sais_scenario_step_unique",
            ),
        ]

    def __str__(self):
        return f"{self.scenario} #{self.order} {self.label}".strip()


class ScenarioRun(models.Model):
    """Bir senaryonun çalışan/yürütülen örneği (eski `samplealarm` karşılığı).

    Tetik gerçekleşince oluşturulur; `last_step_order` cursor'ı ilerledikçe
    adımlar işlenir. Tüm ilerleme burada kalıcıdır → motor tick'ler arası
    stateless'tir, restart güvenlidir.
    """

    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_IN_PROGRESS, "Devam Ediyor"),
        (STATUS_COMPLETED, "Tamamlandı"),
        (STATUS_FAILED, "Başarısız"),
        (STATUS_CANCELLED, "İptal Edildi"),
    )

    scenario = models.ForeignKey(
        Scenario, on_delete=models.CASCADE, related_name="runs", verbose_name="Senaryo",
    )
    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE, related_name="+", verbose_name="İstasyon",
    )
    run_date = models.DateField(db_index=True, verbose_name="Tarih")
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS,
        verbose_name="Durum",
    )
    is_ministry = models.BooleanField(default=False, verbose_name="Bakanlık Talepli")
    trigger_at = models.DateTimeField(verbose_name="Tetik Zamanı")
    last_step_order = models.IntegerField(default=-1, verbose_name="İşlenen Son Adım")
    triggered_parameters = models.JSONField(
        default=list, blank=True, verbose_name="Tetikleyen Parametreler",
    )
    sample_code = models.CharField(max_length=100, blank=True, default="", verbose_name="Numune Kodu")
    completed_at = models.DateTimeField(blank=True, null=True, verbose_name="Tamamlanma")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")

    class Meta:
        db_table = "sais_scenario_run"
        verbose_name = "Senaryo Çalışması"
        verbose_name_plural = "Senaryo Çalışmaları"
        ordering = ["-trigger_at"]
        indexes = [
            models.Index(fields=["station", "-run_date"], name="sais_run_station_date_idx"),
        ]

    def __str__(self):
        return f"{self.scenario} @ {self.trigger_at:%Y-%m-%d %H:%M}"


class ScenarioRunLog(models.Model):
    """Senaryo değerlendirme/adım denetim kaydı (eski `sample_dynamic` karşılığı)."""

    KIND_EVAL = "eval"
    KIND_STEP = "step"
    KIND_SKIP = "skip"
    KIND_CHOICES = (
        (KIND_EVAL, "Değerlendirme"),
        (KIND_STEP, "Adım"),
        (KIND_SKIP, "Atlandı"),
    )

    run = models.ForeignKey(
        ScenarioRun, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="logs", verbose_name="Çalışma",
    )
    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE, related_name="+", verbose_name="İstasyon",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Zaman")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES, default=KIND_EVAL, verbose_name="Tür")
    step_order = models.IntegerField(blank=True, null=True, verbose_name="Adım Sırası")
    averages = models.JSONField(default=dict, blank=True, verbose_name="Ortalamalar")
    message = models.CharField(max_length=500, blank=True, default="", verbose_name="Mesaj")
    skipped_reason = models.CharField(max_length=200, blank=True, default="", verbose_name="Atlanma Nedeni")

    class Meta:
        db_table = "sais_scenario_run_log"
        verbose_name = "Senaryo Log"
        verbose_name_plural = "Senaryo Logları"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["station", "-created_at"], name="sais_runlog_station_idx"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


class ScenarioGraph(models.Model):
    """Görsel node-graph senaryo tasarımı (Drawflow export JSON).

    Genel otomasyon senaryosu tasarımcısının (PLC-benzeri node-graph) sakladığı
    ham graph tanımı. Demo aşamasında yalnız tasarlanıp saklanır — yürütülmez;
    motor + per-senaryo beat sonraki fazda gelir. `is_template` yerleşik
    "Numune Alma" başlangıç tasarımıdır (silinemez).

    Not: graph jenerik bir kavram; ileride `api/`'a taşınabilir. Demo'da mevcut
    senaryo koduna yakın olsun diye `sais_domain`'de tutuluyor.
    """

    name = models.CharField(max_length=150, verbose_name="Tasarım Adı")
    description = models.TextField(blank=True, default="", verbose_name="Açıklama")
    graph = models.JSONField(
        default=dict, blank=True, verbose_name="Graph (Drawflow)",
        help_text="Drawflow editor.export() çıktısı (node + bağlantı tanımı).",
    )
    is_template = models.BooleanField(
        default=False, verbose_name="Yerleşik Şablon",
        help_text="Yerleşik şablonlar silinemez (kopyalanıp düzenlenebilir).",
    )
    station = models.ForeignKey(
        "api.Station", on_delete=models.SET_NULL, blank=True, null=True,
        related_name="+", verbose_name="İstasyon (bağlam)",
    )
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, blank=True, null=True, related_name="+",
        verbose_name="Oluşturan",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")

    class Meta:
        db_table = "sais_scenario_graph"
        verbose_name = "Senaryo Tasarımı"
        verbose_name_plural = "Senaryo Tasarımları"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name
