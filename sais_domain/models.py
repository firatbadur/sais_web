"""
SAIS'e (atıksu sürekli izleme) özgü, alan-özel uzantı modelleri.

Çekirdek `api` uygulaması jenerik SCADA kavramları barındırır; Envisoft
entegrasyonu, Bakanlık talep tipi, atıksu istasyon tipleri gibi SAIS
platformuna özel bilgiler buraya izole edilir.
"""
from django.db import models


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
