"""Rapor Stüdyosu yerleşik şablonlarını tohumlar (idempotent).

Kullanım:
    python manage.py seed_report_templates

"Günlük Tesis Özeti" şablonu Station id=1'in analog parametrelerine bağlanır
(kapsam kuralı: parametreler sensörler üzerinden çözülür). Parametre yoksa
şablon veri bağlaması boş bırakılıp uyarı basılır — editörden düzenlenir.
Ayrıca zamanlama UI'ı boş kalmasın diye DEVRE DIŞI bir örnek ReportSchedule
(günlük 07:00, PDF) eklenir.
"""
from django.core.management.base import BaseCommand


TEMPLATE_NAME = "Günlük Tesis Özeti"


class Command(BaseCommand):
    help = "Rapor Stüdyosu yerleşik rapor şablonlarını tohumlar (idempotent)."

    def handle(self, *args, **options):
        from api.models import Parameter, ReportSchedule, ReportTemplate, Station

        station = Station.objects.filter(pk=1).first() or Station.objects.first()
        params = []
        if station:
            params = list(
                Parameter.objects.filter(
                    sensors__connection__station_id=station.pk,
                    sensors__is_active=True,
                    sensors__sensor_type__in=(0, 1),  # analog
                )
                .distinct()
                .order_by("id")[:4]
            )
        if not params:
            self.stdout.write(self.style.WARNING(
                "Uyarı: analog parametre bulunamadı — şablon veri bağlaması boş "
                "tohumlanıyor (editörden düzenleyin)."
            ))

        station_id = station.pk if station else None
        param_ids = [p.pk for p in params]

        blocks = [
            {"id": "b1", "type": "heading", "text": "Günlük Tesis Özeti", "level": 1},
            {
                "id": "b2", "type": "text",
                "text": "Bu rapor tesisin son 24 saatlik ölçüm özetini, saatlik "
                        "ortalamalarını ve son 7 günün günlük değerlerini içerir.",
            },
            {
                "id": "b3", "type": "kpi_cards",
                # İlk 3 parametrenin 24 saatlik ortalaması + ilk parametrenin anlık değeri
                "cards": [
                    {
                        "station_id": station_id, "parameter_id": pid, "agg": "avg",
                        "window": {"mode": "relative", "key": "last_24h"},
                    }
                    for pid in param_ids[:3]
                ] + [
                    {
                        "station_id": station_id, "parameter_id": pid, "agg": "last",
                        "window": {"mode": "relative", "key": "last_24h"},
                    }
                    for pid in param_ids[:1]
                ],
            },
            {
                "id": "b4", "type": "chart", "chart_type": "line",
                "title": "24 Saatlik Trend",
                "binding": {
                    "station_id": station_id, "parameter_ids": param_ids,
                    "bucket": "hourly",
                    "window": {"mode": "relative", "key": "last_24h"},
                    "columns": ["avg"],
                },
            },
            {
                "id": "b5", "type": "table",
                "title": "Saatlik Ortalamalar",
                "binding": {
                    "station_id": station_id, "parameter_ids": param_ids,
                    "bucket": "hourly",
                    "window": {"mode": "relative", "key": "last_24h"},
                    "columns": ["avg", "min", "max"],
                    "max_rows": 500,
                },
            },
            {"id": "b6", "type": "page_break"},
            {
                "id": "b7", "type": "table",
                "title": "Son 7 Gün — Günlük Özet",
                "binding": {
                    "station_id": station_id, "parameter_ids": param_ids,
                    "bucket": "daily",
                    "window": {"mode": "relative", "key": "last_7d"},
                    "columns": ["avg", "min", "max", "count", "bad_count"],
                    "max_rows": 100,
                },
            },
        ]

        tpl, created = ReportTemplate.objects.update_or_create(
            name=TEMPLATE_NAME,
            defaults={
                "description": "Yerleşik örnek şablon — son 24 saat KPI + trend + "
                               "saatlik tablo + 7 günlük özet.",
                "blocks": blocks,
                "page_size": "A4",
                "orientation": "portrait",
                "header_text": "Envisoft WebX — Otomatik Rapor",
                "footer_text": station.name if station else "",
                "show_logo": True,
                "is_template": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"  {'+' if created else '~'} Şablon: {tpl.name} "
            f"({len(param_ids)} parametre bağlandı)"
        ))

        # Örnek zamanlama — DEVRE DIŞI tohumlanır (kullanıcı bilinçli açsın).
        sched, s_created = ReportSchedule.objects.get_or_create(
            template=tpl,
            period="daily",
            defaults={
                "enabled": False,
                "output_pdf": True,
                "output_excel": True,
                "email_enabled": False,
                "email_subject": "{report_name} - {date}",
                "email_body": "Merhaba,\n\n{date} tarihli {report_name} raporu ektedir.\n\n"
                              "Bu e-posta Envisoft WebX tarafından otomatik gönderilmiştir.",
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"  {'+' if s_created else '~'} Zamanlama: {sched.period_summary} (devre dışı örnek)"
        ))
        self.stdout.write(self.style.SUCCESS("Rapor şablonu seed tamamlandı."))
