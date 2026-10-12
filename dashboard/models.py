"""Dashboard'a özgü modeller.

`Document` — operatör/yönetici tarafından yüklenen belgelerin (teknik
dokümantasyon, bakım formları, sensör katalogları vb.) yönetimi. Tamamen
dashboard arayüzüne özgü bir özellik; SCADA çekirdeği (`api/`) veya SAIS
(`sais_domain/`) ile ilişkisi yoktur.
"""
from __future__ import annotations

import os

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


def document_upload_path(instance, filename):
    """Yüklenen belgeler `media/documents/` altında saklanır.

    Aynı isimli dosyalarda Django FileField otomatik benzersizleştirir.
    """
    return os.path.join("documents", filename)


class Document(models.Model):
    """Yüklenen bir belge kaydı.

    Dosyanın kendisi `MEDIA_ROOT/documents/` altında tutulur; indirme her
    zaman yetki kontrollü `DocumentDownloadView` üzerinden akar (MEDIA_URL'den
    doğrudan servis edilmez).
    """

    class DocType(models.TextChoices):
        TECHNICAL_DOC = "technical_doc", _("Teknik Dokümantasyon")
        COMPARISON_TEST = "comparison_test", _("Bütünleşik Karşılaştırma Testleri")
        MAINTENANCE_FORM = "maintenance_form", _("Periyodik Bakım Formu")
        MINUTES = "minutes", _("Tutanaklar")
        SENSOR_CATALOG = "sensor_catalog", _("Sensör Katalogları")
        SENSOR_BROCHURE = "sensor_brochure", _("Sensör Broşürleri")
        OTHER = "other", _("Diğer")

    title = models.CharField(_("Başlık"), max_length=255)
    doc_type = models.CharField(
        _("Belge Türü"),
        max_length=32,
        choices=DocType.choices,
        default=DocType.OTHER,
        db_index=True,
    )
    description = models.TextField(_("Açıklama"), blank=True)

    file = models.FileField(_("Dosya"), upload_to=document_upload_path)
    original_name = models.CharField(_("Orijinal Dosya Adı"), max_length=255, blank=True)
    file_size = models.BigIntegerField(_("Dosya Boyutu (bayt)"), default=0)
    content_type = models.CharField(_("İçerik Tipi"), max_length=100, blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
        verbose_name=_("Yükleyen"),
    )
    created_at = models.DateTimeField(_("Yüklenme Tarihi"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("Doküman")
        verbose_name_plural = _("Dokümanlar")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def extension(self):
        """Uzantı (nokta olmadan, küçük harf) — ör. 'pdf'."""
        name = self.original_name or (self.file.name if self.file else "")
        return os.path.splitext(name)[1].lstrip(".").lower()

    def delete(self, *args, **kwargs):
        """Kayıt silinince diskteki dosyayı da temizle."""
        file = self.file
        super().delete(*args, **kwargs)
        if file:
            file.storage.delete(file.name)


class MimicScreen(models.Model):
    """Kullanıcı tasarımı SCADA/HMI mimik ekranı.

    Tamamen dashboard arayüzüne özgü bir tasarım editörü özelliğidir. Editör
    (Fabric.js tabanlı) tuvali bir JSON belge olarak `data` alanında saklar;
    `thumbnail` küçük bir base64 PNG önizlemesidir (galeri kartlarında gösterilir).

    Bu fazda mimikler SCADA çekirdeğine **bağlı değildir** — yalnız tasarlanıp
    saklanır, görüntülenir, simüle edilir. Animasyon/etiket bağlama meta verisi
    her objenin kendi `scada` özelliğinde `data` JSON içinde tutulur; ileride
    gerçek `Sensor`/`SensorLatest` değerlerine bağlanabilir.
    """

    name = models.CharField(_("Ekran Adı"), max_length=150)
    description = models.TextField(_("Açıklama"), blank=True, default="")

    data = models.JSONField(
        _("Tuval Verisi"), default=dict, blank=True,
        help_text=_("Fabric.js canvas.toJSON() çıktısı (objeler + bağlama meta verisi)."),
    )
    thumbnail = models.TextField(
        _("Önizleme (base64 PNG)"), blank=True, default="",
        help_text=_("Galeri kartı için küçük base64 data-URL önizleme."),
    )

    width = models.PositiveIntegerField(_("Genişlik (px)"), default=1280)
    height = models.PositiveIntegerField(_("Yükseklik (px)"), default=720)
    background = models.CharField(_("Arka Plan Rengi"), max_length=32, default="#f5f8fa")
    is_template = models.BooleanField(
        _("Yerleşik Şablon"), default=False,
        help_text=_("Yerleşik şablonlar silinemez (kopyalanıp düzenlenebilir)."),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mimic_screens",
        verbose_name=_("Oluşturan"),
    )
    created_at = models.DateTimeField(_("Oluşturma"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Son Güncelleme"), auto_now=True, db_index=True)

    class Meta:
        verbose_name = _("Mimik Ekranı")
        verbose_name_plural = _("Mimik Ekranları")
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


# --------------------------------------------------------------------------- #
# Tesis Sanal Turu — 360° panorama sahneleri + hotspot'lar
# --------------------------------------------------------------------------- #
# Görüntüler bilinçli olarak MEDIA'da değil DB'de (BinaryField) tutulur: prod
# compose'da `media_data` yalnız `web`'e mount'lu, panoramayı birleştiren
# `celery_worker`'a değil; mevcut sahalarda compose dosyasını Watchtower
# yenilemediği için mount eklemek filoya ulaşmazdı. DB'de tutmak ayrıca
# turların `pg_dump` yedeğine otomatik girmesini sağlar. Liste sorgularında
# binary kolonlar `defer()` edilmeli (bkz. `TourScene.LIGHT_DEFER`).

class TourScene(models.Model):
    """Bir istasyonun sanal turundaki tek bir 360° nokta (Kabin içi, Kapı önü...)."""

    class Status(models.TextChoices):
        CAPTURING = "capturing", _("Çekim Bekliyor")
        PROCESSING = "processing", _("Birleştiriliyor")
        READY = "ready", _("Hazır")
        FAILED = "failed", _("Başarısız")

    class Source(models.TextChoices):
        GUIDED = "guided", _("Telefon (yönlendirmeli çekim)")
        UPLOAD = "upload", _("Panorama dosyası")

    LIGHT_DEFER = ("panorama", "thumbnail")

    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE, related_name="tour_scenes",
        verbose_name=_("Tesis"),
    )
    name = models.CharField(_("Sahne Adı"), max_length=120)
    description = models.TextField(_("Açıklama"), blank=True, default="")
    order = models.PositiveIntegerField(_("Sıra"), default=0)

    status = models.CharField(
        _("Durum"), max_length=16, choices=Status.choices, default=Status.CAPTURING,
        db_index=True,
    )
    error = models.TextField(_("Hata"), blank=True, default="")
    source = models.CharField(
        _("Kaynak"), max_length=16, choices=Source.choices, default=Source.GUIDED,
    )
    stitch_method = models.CharField(
        _("Birleştirme Yöntemi"), max_length=32, blank=True, default="",
        help_text=_("opencv / orientation / upload"),
    )

    panorama = models.BinaryField(_("Panorama (JPEG)"), null=True, blank=True)
    pano_width = models.PositiveIntegerField(_("Panorama Genişlik"), default=0)
    pano_height = models.PositiveIntegerField(_("Panorama Yükseklik"), default=0)
    thumbnail = models.BinaryField(_("Önizleme (JPEG)"), null=True, blank=True)
    image_version = models.PositiveIntegerField(
        _("Görüntü Sürümü"), default=0,
        help_text=_("Panorama her değiştiğinde artar (tarayıcı önbelleği/ETag)."),
    )

    # AI hotspot önerisi (dashboard.tour_ai) — durum + sonuç özeti.
    ai_status = models.CharField(_("AI Durumu"), max_length=16, blank=True, default="",
                                 help_text=_("boş / running / done / failed"))
    ai_error = models.TextField(_("AI Hatası"), blank=True, default="")
    ai_added = models.PositiveIntegerField(_("AI Önerilen İşaret"), default=0)
    ai_inventory = models.TextField(_("AI Envanter Notu"), blank=True, default="")

    initial_yaw = models.FloatField(_("Başlangıç Yönü (derece)"), default=0)
    initial_pitch = models.FloatField(_("Başlangıç Eğimi (derece)"), default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tour_scenes", verbose_name=_("Oluşturan"),
    )
    created_at = models.DateTimeField(_("Oluşturma"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Son Güncelleme"), auto_now=True)

    class Meta:
        verbose_name = _("Tur Sahnesi")
        verbose_name_plural = _("Tur Sahneleri")
        ordering = ["station_id", "order", "id"]

    def __str__(self):
        return self.name


class TourFrame(models.Model):
    """Yönlendirmeli çekimde telefondan gelen tek kare (birleştirme girdisi).

    Yönelim (yaw/pitch/roll) telefonun jiroskopundan gelir; yoksa
    `has_orientation=False` ve birleştirme yalnız görüntü eşleştirmesine dayanır.
    Birleştirmeden sonra da saklanır (AI hotspot önerisi perspektif kareleri kullanır).
    """

    scene = models.ForeignKey(
        TourScene, on_delete=models.CASCADE, related_name="frames", verbose_name=_("Sahne"),
    )
    seq = models.PositiveIntegerField(_("Sıra"), default=0)
    image = models.BinaryField(_("Kare (JPEG)"))
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    yaw = models.FloatField(_("Yön (derece)"), default=0)
    pitch = models.FloatField(_("Eğim (derece)"), default=0)
    roll = models.FloatField(_("Yatış (derece)"), default=0)
    hfov = models.FloatField(_("Yatay Görüş Açısı (derece)"), default=0)
    has_orientation = models.BooleanField(_("Yönelim Var"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Tur Karesi")
        verbose_name_plural = _("Tur Kareleri")
        ordering = ["scene_id", "seq", "id"]


class TourHotspot(models.Model):
    """Panorama üzerindeki tıklanabilir işaret (sensör / malzeme / sahne geçişi / not)."""

    class Kind(models.TextChoices):
        SENSOR = "sensor", _("Sensör")
        EQUIPMENT = "equipment", _("Malzeme / Ekipman")
        LINK = "link", _("Sahne Geçişi")
        NOTE = "note", _("Not")

    scene = models.ForeignKey(
        TourScene, on_delete=models.CASCADE, related_name="hotspots", verbose_name=_("Sahne"),
    )
    kind = models.CharField(_("Tür"), max_length=16, choices=Kind.choices, default=Kind.EQUIPMENT)
    yaw = models.FloatField(_("Yön (derece)"))
    pitch = models.FloatField(_("Eğim (derece)"))

    title = models.CharField(_("Başlık"), max_length=150, blank=True, default="")
    description = models.TextField(_("Açıklama"), blank=True, default="")
    brand = models.CharField(_("Marka"), max_length=100, blank=True, default="")
    model_name = models.CharField(_("Model"), max_length=100, blank=True, default="")
    serial_no = models.CharField(_("Seri No"), max_length=100, blank=True, default="")

    sensor = models.ForeignKey(
        "api.Sensor", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tour_hotspots", verbose_name=_("Sensör"),
    )
    target_scene = models.ForeignKey(
        TourScene, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="incoming_links", verbose_name=_("Hedef Sahne"),
    )
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tour_hotspots", verbose_name=_("Doküman"),
    )

    equipment_key = models.CharField(
        _("Ekipman Fotoğraf Maddesi"), max_length=64, blank=True, default="",
        help_text=_("Çekim listesi maddesi (dashboard.tour_checklist); panelde o maddenin "
                    "yakın çekim fotoğrafları gösterilir."),
    )
    ai_suggested = models.BooleanField(_("AI Önerisi"), default=False)
    ai_confidence = models.FloatField(_("AI Güveni"), null=True, blank=True)
    confirmed = models.BooleanField(_("Onaylı"), default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tour_hotspots", verbose_name=_("Oluşturan"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Tur İşareti")
        verbose_name_plural = _("Tur İşaretleri")
        ordering = ["scene_id", "id"]

    def __str__(self):
        return self.title or self.get_kind_display()


class EquipmentPhoto(models.Model):
    """Yönlendirmeli ekipman fotoğrafı (analizör, sensör, numune alıcı, PLC I/O...).

    Hangi maddenin hangi çekim adımı olduğu `item_key` + `shot_key` ile tutulur;
    madde/adım tanımları kodda sabittir (`dashboard.tour_checklist`). Sensöre özgü
    maddeler `sensor:<id>` anahtarıyla istasyonun sensörlerinden türetilir. Serbest
    fotoğraflar `item_key="custom"`. Görüntüler `TourScene` gibi DB'de tutulur.
    `brand/model_name/serial_no` etiket plakasından (elle veya AI ile) doldurulur.
    """

    LIGHT_DEFER = ("image", "thumbnail")

    station = models.ForeignKey(
        "api.Station", on_delete=models.CASCADE, related_name="equipment_photos",
        verbose_name=_("Tesis"),
    )
    item_key = models.CharField(_("Madde"), max_length=64, db_index=True)
    shot_key = models.CharField(_("Çekim Adımı"), max_length=32, blank=True, default="")
    label = models.CharField(_("Etiket"), max_length=150, blank=True, default="",
                             help_text=_("Serbest fotoğrafta veya ek ekipmanda ad (ör. Pompa 2)."))
    sensor = models.ForeignKey(
        "api.Sensor", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="equipment_photos", verbose_name=_("Sensör"),
    )
    caption = models.TextField(_("Not"), blank=True, default="")

    image = models.BinaryField(_("Fotoğraf (JPEG)"))
    thumbnail = models.BinaryField(_("Önizleme (JPEG)"), null=True, blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)

    brand = models.CharField(_("Marka"), max_length=100, blank=True, default="")
    model_name = models.CharField(_("Model"), max_length=100, blank=True, default="")
    serial_no = models.CharField(_("Seri No"), max_length=100, blank=True, default="")
    ai_status = models.CharField(_("AI Durumu"), max_length=16, blank=True, default="",
                                 help_text=_("boş / running / done / failed"))
    ai_text = models.TextField(_("AI Çıktısı"), blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="equipment_photos", verbose_name=_("Çeken"),
    )
    created_at = models.DateTimeField(_("Çekim Zamanı"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("Ekipman Fotoğrafı")
        verbose_name_plural = _("Ekipman Fotoğrafları")
        ordering = ["station_id", "item_key", "shot_key", "-created_at"]

    def __str__(self):
        return self.label or self.item_key
