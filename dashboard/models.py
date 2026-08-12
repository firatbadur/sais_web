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
