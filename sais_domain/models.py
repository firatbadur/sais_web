"""
SAIS'e (atıksu sürekli izleme) özgü, alan-özel uzantı modelleri.

Çekirdek `api` uygulaması jenerik SCADA kavramları barındırır; Envisoft
entegrasyonu, Bakanlık talep tipi, atıksu istasyon tipleri, Bakanlık
SAIS kabin kayıtları gibi SAIS platformuna özel bilgiler buraya
izole edilir.
"""
from django.db import models
from django.utils import timezone

from sais_domain.crypto import EncryptedCharField
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
        verbose_name="Tesis",
    )
    device_id = models.CharField(
        max_length=100,
        verbose_name="Bakanlık SIM ID",
        help_text="Bakanlık'ın SAIS kabini için atadığı benzersiz kimlik",
    )
    code = models.CharField(
        max_length=50,
        verbose_name="Tesis Kodu",
        help_text="Bakanlık tesis kodu (örn. 30060001)",
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
    # Bakanlık şifresi DB'de + pg_dump yedeklerinde Fernet ile ŞİFRELİ tutulur
    # (at-rest). Python tarafında düz metin sunulur → kullanım kodu değişmez.
    # Bkz. sais_domain/crypto.py. max_length token'ı barındıracak kadar geniş (512).
    auth_secret = EncryptedCharField(
        max_length=512,
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


def _default_data_error_codes():
    """Bakanlık veri hatası alarmı için varsayılan izlenecek hata kodları.

    200–206: Eksik/Geçersiz Yıkama, Eksik/Geçersiz Haftalık Yıkama,
    Geçersiz/Eksik Aylık Kalibrasyon, Geçersiz Akış Hızı, Geçersiz Debi,
    Tekrar Veri, Geçersiz Birim. Kullanıcı Sistem Kontrol → Sistem
    Alarmları'ndan değiştirebilir."""
    return [200, 201, 202, 203, 204, 205, 206]


# Sistem alarmı bildirimi alabilecek roller (Bakanlık rol=4 her zaman hariç —
# o kullanıcılar yalnız API erişimi içindir, bildirim almazlar).
ALARM_ROLE_CHOICES = tuple(
    (code, label) for code, label in CustomUser.rol_choices if code != 4
)
ALARM_ROLE_CODES = {code for code, _ in ALARM_ROLE_CHOICES}


def _default_alarm_roles():
    """Varsayılan bildirim hedefi: yalnız **Operatör** (rol=2).

    Her alarm kategorisi kendi rol listesini tutar; operatör Sistem Kontrol →
    Sistem Alarmları sekmesinden kategori bazında değiştirir (ör. lisans/SSL
    uyarısı yalnız Sistem Yöneticisi'ne gitsin)."""
    return [2]


class SystemAlarmSettings(models.Model):
    """Sistem uyarı mekanizmaları ayarı — singleton (pk=1).

    Sistem Kontrol → **Sistem Alarmları** sekmesinden yönetilir. Beş kategori,
    her biri toggle + kendi ayarlarıyla:

    1. **Bakanlık veri hatası** — son 10 dk `GetDataByBetweenTwoDate`; seçili hata
       status'ları `persist` dakikadan uzun tekrarlıysa operatöre bildir (anlık
       tek-sefer hatalar boğmasın). 10 dakikada bir çalışır.
    2. **SSL** — sertifika bitişine `ssl_warn_days` kala (günlük kontrol).
    3. **Kalibrasyon** — aylık zorunlu; son kalibrasyondan `interval-warn` gün
       geçince (1 gün kala) bildir (günlük).
    4. **Lisans** — bitişe `license_warn_days` kala (günlük).
    5. **Açılma/kapanma (PowerOff)** — `poweroff_min_minutes`'tan uzun
       enerji/PC kesintisi olunca bildir (her kayıt için bir kez).
    6. **Bakanlık veri kuyruğu** — gönderim kuyruğu (`SimOutboxEntry`) baştaki
       kayıtta tıkandıysa, dakika birikmesi eşiği aştıysa veya Bakanlık dakika
       reddettiyse bildir. 10 dakikada bir çalışır.
    """

    # --- Bakanlık veri hatası ---
    data_error_enabled = models.BooleanField(default=False, verbose_name="Bakanlık Veri Hatası Alarmı")
    data_error_codes = models.JSONField(
        default=_default_data_error_codes, blank=True,
        verbose_name="İzlenen Hata Kodları",
        help_text="Bu Bakanlık status kodları tekrarlı görülürse bildir.",
    )
    data_error_persist_minutes = models.IntegerField(
        default=5, verbose_name="Tekrar Eşiği (dk)",
        help_text="Hata bu kadar dakikadan uzun tekrarlıysa bildir (anlık tek-sefer atlanır).",
    )
    data_error_cooldown_minutes = models.IntegerField(
        default=60, verbose_name="Tekrar Bildirim Aralığı (dk)",
        help_text="Aynı hata için iki bildirim arası asgari süre.",
    )
    data_error_roles = models.JSONField(
        default=_default_alarm_roles, blank=True,
        verbose_name="Bakanlık Veri Hatası — Bildirim Rolleri",
        help_text="Bu alarmın bildirileceği kullanıcı rolleri (varsayılan: Operatör).",
    )

    # --- SSL ---
    ssl_enabled = models.BooleanField(default=True, verbose_name="SSL Bitiş Uyarısı")
    ssl_warn_days = models.IntegerField(default=3, verbose_name="SSL Uyarı (gün kala)")
    ssl_roles = models.JSONField(
        default=_default_alarm_roles, blank=True,
        verbose_name="SSL — Bildirim Rolleri",
        help_text="Bu alarmın bildirileceği kullanıcı rolleri (varsayılan: Operatör).",
    )

    # --- Kalibrasyon ---
    calibration_enabled = models.BooleanField(default=True, verbose_name="Kalibrasyon Hatırlatma")
    calibration_interval_days = models.IntegerField(
        default=30, verbose_name="Kalibrasyon Aralığı (gün)",
        help_text="Ayda 1 zorunlu → 30 gün.",
    )
    calibration_warn_days = models.IntegerField(
        default=1, verbose_name="Kalibrasyon Uyarı (gün kala)",
    )
    calibration_roles = models.JSONField(
        default=_default_alarm_roles, blank=True,
        verbose_name="Kalibrasyon — Bildirim Rolleri",
        help_text="Bu alarmın bildirileceği kullanıcı rolleri (varsayılan: Operatör).",
    )

    # --- Lisans ---
    license_enabled = models.BooleanField(default=True, verbose_name="Lisans Bitiş Uyarısı")
    license_warn_days = models.IntegerField(default=7, verbose_name="Lisans Uyarı (gün kala)")
    license_roles = models.JSONField(
        default=_default_alarm_roles, blank=True,
        verbose_name="Lisans — Bildirim Rolleri",
        help_text="Bu alarmın bildirileceği kullanıcı rolleri (varsayılan: Operatör).",
    )

    # --- SİM gönderim kuyruğu (tıkanma / birikme) ---
    sim_queue_enabled = models.BooleanField(
        default=True, verbose_name="Bakanlık Veri Kuyruğu Alarmı",
        help_text="Kuyruk tıkandığında veya birikmeye başladığında bildir.",
    )
    sim_queue_blocked_minutes = models.IntegerField(
        default=15, verbose_name="Tıkanma Süresi Eşiği (dk)",
        help_text="Baştaki veri bu kadar dakikadır iletilemiyorsa bildir.",
    )
    sim_queue_backlog_threshold = models.IntegerField(
        default=30, verbose_name="Birikim Eşiği (dakika sayısı)",
        help_text="Kuyrukta bu kadar dakikalık veri birikirse bildir.",
    )
    sim_queue_cooldown_minutes = models.IntegerField(
        default=60, verbose_name="Tekrar Bildirim Aralığı (dk)",
        help_text="Aynı kabin için iki bildirim arası asgari süre.",
    )
    sim_queue_roles = models.JSONField(
        default=_default_alarm_roles, blank=True,
        verbose_name="Bakanlık Veri Kuyruğu — Bildirim Rolleri",
        help_text="Bu alarmın bildirileceği kullanıcı rolleri (varsayılan: Operatör).",
    )

    # --- PowerOff (enerji/internet kesintisi) ---
    poweroff_enabled = models.BooleanField(default=True, verbose_name="Kesinti (Açılma/Kapanma) Uyarısı")
    poweroff_min_minutes = models.IntegerField(
        default=5, verbose_name="Asgari Kesinti Süresi (dk)",
        help_text="Bu süreden kısa kesintiler bildirilmez.",
    )
    poweroff_roles = models.JSONField(
        default=_default_alarm_roles, blank=True,
        verbose_name="Kesinti — Bildirim Rolleri",
        help_text="Bu alarmın bildirileceği kullanıcı rolleri (varsayılan: Operatör).",
    )

    # --- Kanallar ---
    notify_sms = models.BooleanField(default=True, verbose_name="SMS ile Bildir")
    notify_email = models.BooleanField(default=True, verbose_name="E-posta ile Bildir")

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")
    updated_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Güncelleyen",
    )

    class Meta:
        db_table = "sais_system_alarm_settings"
        verbose_name = "Sistem Alarm Ayarı"
        verbose_name_plural = "Sistem Alarm Ayarları"

    def __str__(self):
        return "Sistem Alarm Ayarları"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def channels(self):
        """Aktif bildirim kanalları (send_bulk için)."""
        ch = []
        if self.notify_sms:
            ch.append("sms")
        if self.notify_email:
            ch.append("email")
        return ch

    def roles_for(self, alarm_type: str) -> list[int]:
        """``alarm_type`` için bildirim yapılacak rol kodları.

        Tanımsız/boş/bozuk değerde varsayılana (yalnız Operatör) düşer —
        alan boş bırakılıp alarmın sessizce kimseye gitmemesi istenmez;
        bildirimi tamamen kapatmak için kategori toggle'ı kullanılır.
        """
        raw = getattr(self, f"{alarm_type}_roles", None)
        if not isinstance(raw, (list, tuple)):
            return _default_alarm_roles()
        roles = []
        for r in raw:
            try:
                code = int(r)
            except (TypeError, ValueError):
                continue
            if code in ALARM_ROLE_CODES and code not in roles:
                roles.append(code)
        return roles or _default_alarm_roles()


class SystemAlarmState(models.Model):
    """Sistem alarmı tekrar-bildirim throttle state'i.

    Her ``(alarm_type, ref_key)`` için son bildirim zamanını tutar; tekrar
    bildirimi cooldown ile sınırlar (kullanıcıyı boğmamak için). PowerOff gibi
    tek-sefer alarmlar için kayıt varlığı yeterlidir (yeniden bildirilmez).
    """

    alarm_type = models.CharField(max_length=30, verbose_name="Alarm Tipi")
    ref_key = models.CharField(max_length=120, verbose_name="Referans")
    last_notified_at = models.DateTimeField(verbose_name="Son Bildirim")
    detail = models.CharField(max_length=300, blank=True, default="", verbose_name="Detay")

    class Meta:
        db_table = "sais_system_alarm_state"
        verbose_name = "Sistem Alarm Durumu"
        verbose_name_plural = "Sistem Alarm Durumları"
        ordering = ["-last_notified_at"]
        constraints = [
            models.UniqueConstraint(fields=["alarm_type", "ref_key"], name="sais_alarm_state_unique"),
        ]

    def __str__(self):
        return f"{self.alarm_type}/{self.ref_key} @ {self.last_notified_at:%Y-%m-%d %H:%M}"


class SimValidDay(models.Model):
    """Bir kabin için bir günün **geçerli veri** istatistiği (job ile doldurulur).

    Aylık geçerli veri oranı bu günlük kayıtların toplamından hesaplanır; böylece
    dashboard Bakanlık'a **canlı ay-sorgusu atmaz**. Günde 1 kez çalışan job
    (``sais_domain.tasks.compute_sim_valid_stats``) ayın her gününü **tek**
    ``GetDataByBetweenTwoDate`` (period=1) ile **gün gün (kısım kısım)** çekip
    burada damgalar. Geçerlilik **yalnız doğrulanmış (``_N``) status** üzerinden
    hesaplanır (bkz. ``sim_report.valid_counts``).

    ``finalized=True`` → gün kapandı (geçmiş), bir daha hesaplanmaz; bugünün
    kaydı her job run'ında güncellenir (kısmi).
    """

    cabinet = models.ForeignKey(
        SaisCabinet, on_delete=models.CASCADE, related_name="valid_days",
        verbose_name="Kabin",
    )
    day = models.DateField(db_index=True, verbose_name="Gün")
    expected = models.IntegerField(
        default=0, verbose_name="Beklenen Dakika",
        help_text="O güne ait beklenen kayıt sayısı (geçmiş gün=1440, bugün=o ana kadar).",
    )
    received = models.IntegerField(default=0, verbose_name="Gelen Kayıt")
    param_valid = models.JSONField(
        default=dict, blank=True, verbose_name="Parametre Geçerli Sayıları",
        help_text="{parametre: doğrulanmış geçerli kayıt sayısı}",
    )
    param_count = models.IntegerField(default=0, verbose_name="Parametre Sayısı")
    finalized = models.BooleanField(
        default=False, verbose_name="Kesinleşti",
        help_text="Geçmiş gün; yeniden hesaplanmaz.",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Son Güncelleme")

    class Meta:
        db_table = "sais_valid_day"
        verbose_name = "Günlük Geçerli Veri"
        verbose_name_plural = "Günlük Geçerli Veriler"
        ordering = ["-day"]
        constraints = [
            models.UniqueConstraint(fields=["cabinet", "day"], name="sais_valid_day_unique"),
        ]

    def __str__(self):
        return f"{self.cabinet_id} / {self.day} — %{self.valid_pct()}"

    def valid_cells(self):
        """Toplam doğrulanmış geçerli hücre (parametre×dakika)."""
        return sum((self.param_valid or {}).values())

    def total_cells(self):
        """Beklenen toplam hücre (beklenen dakika × parametre sayısı)."""
        return self.expected * self.param_count

    def valid_pct(self):
        """Günün geçerli veri oranı (%, doğrulanmış status'a göre)."""
        total = self.total_cells()
        if not total:
            return 0.0
        return round(min(100.0, self.valid_cells() / total * 100), 1)



class SimOutboxEntry(models.Model):
    """Bakanlık SİM'e iletilecek dakikalık veri kuyruğu (store-and-forward).

    **Neden var:** eskiden dakikalık ``SendData`` doğrudan ``publish_cabinet_data``
    içinden atılıyordu; Bakanlık servisi yanıt vermezse o dakikanın payload'ı
    hiçbir yere yazılmadan kaybolurdu (tek onarım yolu 6 saatte bir çalışan
    ``resend_missing_data`` idi). Artık her dakika önce buraya yazılır,
    gönderimi ayrı bir drenaj task'ı yapar.

    **Sıkı FIFO / baş tıkanması (asıl gereksinim):** drenaj bir kabinin
    şeridinde en eski bekleyen kayıttan başlar. Baştaki kayıt **geçici** bir
    hata alırsa (timeout / bağlantı / 5xx) kuyruk **BLOKE** olur — sonraki
    dakikalar Bakanlık o veriyi kabul edene kadar GÖNDERİLMEZ. Böylece
    Bakanlık tarafında dakika sırası korunur.

    Kilitlenmeye karşı üç kaçış valfi vardır:

    1. **Kalıcı ret** (HTTP 4xx / zarf ``result:false``) sınırlı sayıda denenir
       (``SAIS_SIM_OUTBOX_MAX_ATTEMPTS``), sonra ``failed`` damgalanır, kuyruk
       ilerler ve operatöre alarm gider.
    2. **Zehirli kuyruk** — peş peşe ``SAIS_SIM_OUTBOX_POISON_STREAK`` kayıt
       aynı nedenle reddedilirse (sistematik hata, ör. bozuk parametre adı)
       sonraki retler ilk denemede ``failed`` olur; kuyruk saatlerce tıkanmaz.
    3. **Süre aşımı** — ``readtime``'ı ``SAIS_SIM_OUTBOX_MAX_AGE_HOURS``
       (48 saat) geçen kayıt ``expired`` olur; Bakanlık zaten 48 saatten
       eskisini kabul etmiyor (``GetMissingDates`` penceresi de 48 saat).

    **Payload dondurulur.** ``payload`` alanı üretim anındaki
    ``build_sim_payload`` çıktısıdır. Kritik: dondurulmasaydı, biriken bir
    kuyruğu boşaltan drenaj *güncel* sensör değerlerini *eski* bir dakika
    damgasıyla gönderirdi.

    Şeritler (``priority``): canlı dakikalar (0) geçmiş/eksik veri
    backfill'inden (10) önce gider — böylece 720 dakikalık bir backfill yığını
    canlı akışı bekletmez. Kuyruk **kabin bazında izoledir**: bir kabinin
    tıkanması diğerini durdurmaz (drenaj task'ı ve Redis kilidi kabin başınadır).

    Retention: ``prune_sim_outbox`` (``SIM_OUTBOX_RETENTION_DAYS`` = 7 gün
    gönderilmiş, ``SIM_OUTBOX_DEAD_RETENTION_DAYS`` = 30 gün ölü kayıt).
    """

    STATUS_PENDING = "pending"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Kuyrukta"),
        (STATUS_SENDING, "Gönderiliyor"),
        (STATUS_SENT, "İletildi"),
        (STATUS_FAILED, "Bakanlık Reddetti"),
        (STATUS_EXPIRED, "Süresi Doldu"),
        (STATUS_SKIPPED, "Operatör Atladı"),
    )
    #: Henüz sonuçlanmamış (kuyrukta sayılan) durumlar.
    ACTIVE_STATUSES = (STATUS_PENDING, STATUS_SENDING)
    #: Sonuçlanmış ama iletilememiş durumlar.
    DEAD_STATUSES = (STATUS_FAILED, STATUS_EXPIRED, STATUS_SKIPPED)

    PRIORITY_LIVE = 0
    PRIORITY_BACKFILL = 10
    PRIORITY_CHOICES = (
        (PRIORITY_LIVE, "Canlı"),
        (PRIORITY_BACKFILL, "Geçmiş (eksik veri)"),
    )
    #: Drenajın ziyaret sırası — canlı şerit önce.
    LANES = (PRIORITY_LIVE, PRIORITY_BACKFILL)

    SOURCE_LIVE = "live"
    SOURCE_MISSING = "missing"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = (
        (SOURCE_LIVE, "Dakikalık Yayın"),
        (SOURCE_MISSING, "Eksik Veri Servisi"),
        (SOURCE_MANUAL, "Elle / Komut"),
    )

    cabinet = models.ForeignKey(
        SaisCabinet, on_delete=models.CASCADE, related_name="outbox_entries",
        verbose_name="Kabin",
    )
    readtime = models.DateTimeField(
        verbose_name="Veri Zamanı",
        help_text="Verinin ait olduğu dakika (saniye sıfırlanmış). Bakanlık'a "
                  "gönderilen Readtime bu değerden üretilir.",
    )
    period = models.SmallIntegerField(
        default=1, verbose_name="Periyot",
        help_text="Kabinin veri periyodu (SendData Period alanı).",
    )
    priority = models.SmallIntegerField(
        default=PRIORITY_LIVE, choices=PRIORITY_CHOICES, verbose_name="Öncelik",
        help_text="Canlı dakikalar geçmiş veri backfill'inden önce gönderilir.",
    )
    source = models.CharField(
        max_length=10, default=SOURCE_LIVE, choices=SOURCE_CHOICES,
        verbose_name="Kaynak",
    )
    payload = models.JSONField(
        default=dict, blank=True, verbose_name="Veri (payload)",
        help_text="Üretim anında dondurulmuş {parametre: değer, parametre_Status: "
                  "kod} sözlüğü. Kayıt sonuçlanınca yer kaplamasın diye boşaltılır.",
    )

    status = models.CharField(
        max_length=16, default=STATUS_PENDING, choices=STATUS_CHOICES,
        db_index=True, verbose_name="Durum",
    )
    attempts = models.PositiveIntegerField(
        default=0, verbose_name="Deneme Sayısı",
    )
    next_attempt_at = models.DateTimeField(
        default=timezone.now, verbose_name="Sonraki Deneme",
        help_text="Backoff sonrası bu andan önce tekrar denenmez.",
    )
    last_attempt_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Son Deneme",
    )
    claimed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Kilitlenme Anı",
        help_text="`sending` durumuna geçiş anı; worker çökerse bu damgadan "
                  "hareketle kayıt kuyruğa geri alınır.",
    )
    first_error_at = models.DateTimeField(
        null=True, blank=True, verbose_name="İlk Hata",
        help_text="Bu kaydın ilk başarısız denemesi — 'kuyruk N dakikadır "
                  "tıkalı' ölçümü buradan hesaplanır.",
    )
    sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name="İletim Zamanı",
    )

    last_error_kind = models.CharField(
        max_length=20, blank=True, default="", verbose_name="Hata Tipi",
        help_text="sais_domain.sim_errors kategorisi (timeout/conn_error/...).",
    )
    last_error = models.CharField(
        max_length=500, blank=True, default="", verbose_name="Son Hata",
    )
    last_http_status = models.IntegerField(
        null=True, blank=True, verbose_name="Son HTTP Kodu",
    )
    ministry_message = models.CharField(
        max_length=500, blank=True, default="", verbose_name="Bakanlık Mesajı",
        help_text="Ret hâlinde Bakanlık zarfındaki `message` alanı.",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Oluşturma")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme")

    class Meta:
        db_table = "sais_sim_outbox"
        verbose_name = "SİM Veri Kuyruğu Kaydı"
        verbose_name_plural = "SİM Veri Kuyruğu"
        ordering = ["-readtime", "-id"]
        constraints = [
            # Aynı dakika için en fazla BİR açık kayıt: mükerrer beat
            # tetiklemesi veya Celery ACKS_LATE yeniden teslimi kaydı
            # çiftlemesin. Kısmi koşul sayesinde, Bakanlık daha önce iletilmiş
            # bir dakikayı "eksik" bildirirse o dakika yeniden kuyruğa alınabilir.
            models.UniqueConstraint(
                fields=["cabinet", "readtime"],
                condition=models.Q(status__in=["pending", "sending"]),
                name="sais_outbox_active_uniq",
            ),
        ]
        indexes = [
            # Baş kayıt seçimi — KISMİ index: yalnız açık satırları kapsar, bu
            # yüzden birikmiş `sent` satırları sıcak index'i şişirmez.
            models.Index(
                fields=["cabinet", "priority", "readtime"],
                condition=models.Q(status__in=["pending", "sending"]),
                name="sais_outbox_head_idx",
            ),
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(status="pending"),
                name="sais_outbox_due_idx",
            ),
            models.Index(fields=["status", "-readtime"], name="sais_outbox_st_rt_idx"),
            models.Index(fields=["cabinet", "-readtime"], name="sais_outbox_cab_rt_idx"),
        ]

    def __str__(self):
        return f"[{self.status}] kabin={self.cabinet_id} {self.readtime:%Y-%m-%d %H:%M}"

    # ---- Yardımcılar ----

    @property
    def readtime_str(self):
        """Bakanlık'ın beklediği ``YYYY-AA-GGTSS:DD:00`` yerel saat string'i.

        String saklanmaz, gönderim anında üretilir — böylece damga ile sıralama
        anahtarı asla birbirinden ayrışamaz.
        """
        return timezone.localtime(self.readtime).strftime("%Y-%m-%dT%H:%M:00")

    @property
    def is_open(self):
        return self.status in self.ACTIVE_STATUSES

    def age_seconds(self, now=None):
        """Verinin ait olduğu dakikadan bu yana geçen saniye."""
        now = now or timezone.now()
        return int((now - self.readtime).total_seconds())

    def is_expired(self, max_age_hours, now=None):
        """``readtime`` 48 saatten eskiyse Bakanlık zaten kabul etmez."""
        return self.age_seconds(now) > int(max_age_hours) * 3600

    def blocked_seconds(self, now=None):
        """Bu kaydın kaç saniyedir iletilemediği (ilk hatadan beri)."""
        if not self.first_error_at:
            return 0
        now = now or timezone.now()
        return int((now - self.first_error_at).total_seconds())

    # ---- Kuyruk işlemleri ----

    @classmethod
    def enqueue(cls, cabinet, *, readtime, payload, period=1,
                priority=PRIORITY_LIVE, source=SOURCE_LIVE):
        """Bir dakikayı kuyruğa ekler; zaten açık kayıt varsa eklemez.

        ``(cabinet, readtime)`` üzerindeki **kısmi** unique kısıt yüzünden
        ``get_or_create`` güvenilir değildir (kısıt yalnız açık satırları
        kapsar) → varlık kontrolü + ``IntegrityError`` yakalama ile yapılır.

        Dönüş: ``(entry | None, created: bool)``.
        """
        from django.db import IntegrityError, transaction

        readtime = readtime.replace(second=0, microsecond=0)
        try:
            with transaction.atomic():
                existing = cls.objects.filter(
                    cabinet=cabinet, readtime=readtime,
                    status__in=cls.ACTIVE_STATUSES,
                ).first()
                if existing is not None:
                    return existing, False
                entry = cls.objects.create(
                    cabinet=cabinet, readtime=readtime, payload=payload,
                    period=period or 1, priority=priority, source=source,
                )
                return entry, True
        except IntegrityError:
            # Yarış: başka bir worker aynı dakikayı bizden önce ekledi.
            return None, False

    @classmethod
    def head(cls, cabinet_id, priority):
        """Bir kabin + şeritteki en eski bekleyen kayıt (FIFO başı)."""
        return (
            cls.objects
            .filter(cabinet_id=cabinet_id, priority=priority,
                    status=cls.STATUS_PENDING)
            .order_by("readtime", "id")
            .first()
        )

    @classmethod
    def summary(cls, now=None):
        """Kabin başına kuyruk özeti — dashboard kartı + alarm motoru için.

        Tek yerde toplanır ki panel rozeti, kuyruk sayfası ve alarm eşiği aynı
        sayıları görsün.
        """
        from datetime import timedelta

        from django.db.models import Count, Min, Q

        now = now or timezone.now()
        day_ago = now - timedelta(hours=24)
        hour_ago = now - timedelta(hours=1)

        rows = (
            cls.objects
            .values("cabinet_id", "cabinet__name", "cabinet__device_id",
                    "cabinet__station__name")
            .annotate(
                pending=Count("id", filter=Q(status__in=cls.ACTIVE_STATUSES)),
                oldest=Min("readtime", filter=Q(status__in=cls.ACTIVE_STATUSES)),
                failed_24h=Count("id", filter=Q(status=cls.STATUS_FAILED,
                                                updated_at__gte=day_ago)),
                failed_1h=Count("id", filter=Q(status=cls.STATUS_FAILED,
                                               updated_at__gte=hour_ago)),
                expired_24h=Count("id", filter=Q(status=cls.STATUS_EXPIRED,
                                                 updated_at__gte=day_ago)),
                sent_24h=Count("id", filter=Q(status=cls.STATUS_SENT,
                                              sent_at__gte=day_ago)),
            )
            .order_by("cabinet_id")
        )

        out = []
        for r in rows:
            head = (
                cls.head(r["cabinet_id"], cls.PRIORITY_LIVE)
                or cls.head(r["cabinet_id"], cls.PRIORITY_BACKFILL)
            )
            out.append({
                "cabinet_id": r["cabinet_id"],
                "cabinet": r["cabinet__name"] or r["cabinet__device_id"],
                "station": r["cabinet__station__name"] or "",
                "pending": r["pending"],
                "oldest": r["oldest"],
                "failed_24h": r["failed_24h"],
                "failed_1h": r["failed_1h"],
                "expired_24h": r["expired_24h"],
                "sent_24h": r["sent_24h"],
                "head_readtime": head.readtime if head else None,
                "head_blocked_seconds": head.blocked_seconds(now) if head else 0,
                "head_error_kind": head.last_error_kind if head else "",
                "head_message": (
                    (head.ministry_message or head.last_error) if head else ""
                ),
                "blocked": bool(head and head.first_error_at),
            })
        return out


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

    # --- Bakanlık veri kabul penceresi (kuyruk süre aşımı) ---
    # Bakanlık SIM varsayılan olarak son 48 saatlik veriyi kabul eder, daha
    # eskisini reddeder (GetMissingDates penceresi de 48 saattir). ANCAK bakım /
    # özel durumlarda Bakanlık bu pencereyi geçici olarak genişletebiliyor.
    # Kuyruk (SimOutboxEntry) bu değerden eski bekleyen kayıtları `expired`
    # yapıp ilerlediği için, pencere sabit kodlu OLMAMALI: saha operatörü
    # Sistem Kontrol sayfasından güncelleyebilir. `.env`'deki
    # SAIS_SIM_OUTBOX_MAX_AGE_HOURS yalnız ilk varsayılanı belirler.
    sim_accept_window_hours = models.IntegerField(
        default=48,
        verbose_name="Bakanlık Veri Kabul Penceresi (saat)",
        help_text="Bakanlık bu kadar saat öncesine kadarki veriyi kabul eder "
                  "(varsayılan 48). Kuyrukta bu süreyi aşan kayıtlar 'süresi "
                  "doldu' işaretlenip atlanır. Bakanlık bakım vb. nedenle "
                  "pencereyi geçici genişletirse burayı artırın; eksik veri "
                  "servisi de aynı pencereyi kullanır.",
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

    def accept_window_hours(self):
        """Bakanlık veri kabul penceresi (saat) — güvenli sınırlar içinde.

        Kuyruk süre aşımı ve eksik veri servisi bu tek kaynağı kullanır.
        Anlamsız değerlere karşı 1 saat ile 30 gün arasına sıkıştırılır.
        """
        from django.conf import settings
        raw = self.sim_accept_window_hours
        if not raw or raw <= 0:
            raw = int(getattr(settings, "SAIS_SIM_OUTBOX_MAX_AGE_HOURS", 48))
        return max(1, min(int(raw), 24 * 30))

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
        help_text="Aynı tesis + tür için yalnız bir senaryo aktif olabilir.",
    )
    enabled = models.BooleanField(default=True, verbose_name="Etkin")
    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE,
        blank=True, null=True,
        related_name="sample_scenarios", verbose_name="Tesis",
        help_text="Boş senaryolar şablondur; aktif edilmeden önce tesis atanır.",
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
        related_name="+", verbose_name="Numune Alıcı Sensör",
        help_text="StartSample/sampler_on/off aksiyonlarının yazacağı dijital-out "
                  "sensörü. sampler_on/off aksiyonu kullanan senaryolarda zorunlu; "
                  "tanımlı değilse senaryo çalışmaz.",
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
        """Senaryonun numune alıcı sensörü (tanımlı değilse None)."""
        return self.sampler_sensor if self.sampler_sensor_id else None

    def requires_sampler(self):
        """Adımlarında sampler_on/sampler_off aksiyonu olan senaryo numune
        çıkışına (sampler_sensor) ihtiyaç duyar."""
        for step in self.steps.all():
            for action in (step.actions or []):
                if action.get("type") in ("sampler_on", "sampler_off"):
                    return True
        return False

    def sampler_missing(self):
        """Numune çıkışı gereken ama tanımlı olmayan senaryo. True ise motorda
        çalışmaz ve kütüphanede uyarı gösterilir."""
        return self.requires_sampler() and self.effective_sampler_sensor() is None

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
        "api.Station", on_delete=models.CASCADE, related_name="+", verbose_name="Tesis",
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
        "api.Station", on_delete=models.CASCADE, related_name="+", verbose_name="Tesis",
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
        related_name="+", verbose_name="Tesis (bağlam)",
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
