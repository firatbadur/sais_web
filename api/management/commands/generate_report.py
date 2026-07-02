"""Bir rapor şablonundan senkron rapor üretimi (debug/manuel).

Kullanım:
    python manage.py generate_report --template-id 1
    python manage.py generate_report --template-id 1 --formats pdf,excel

Celery worker gerektirmez — `api.reporting.generate_report` doğrudan çağrılır.
Zamanlanmış üretim için bkz. `api.tasks.dispatch_report_schedules`.
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rapor şablonundan senkron PDF/Excel üretir (Rapor Stüdyosu debug komutu)."

    def add_arguments(self, parser):
        parser.add_argument("--template-id", type=int, required=True, help="ReportTemplate id")
        parser.add_argument(
            "--formats", default="pdf",
            help="Virgülle ayrılmış format listesi: pdf,excel (default: pdf)",
        )
        parser.add_argument("--schedule-id", type=int, default=None,
                            help="Opsiyonel ReportSchedule id (e-posta testi için)")

    def handle(self, *args, **options):
        from api import reporting

        formats = [f.strip() for f in (options["formats"] or "").split(",") if f.strip()]
        bad = [f for f in formats if f not in ("pdf", "excel")]
        if bad:
            raise CommandError(f"Bilinmeyen format: {', '.join(bad)} (pdf | excel)")

        rep = reporting.generate_report(
            options["template_id"],
            schedule_id=options["schedule_id"],
            trigger="manual",
            formats=formats or None,
        )

        style = self.style.SUCCESS if rep.status == "success" else self.style.ERROR
        self.stdout.write(style(f"GeneratedReport #{rep.id} -> {rep.status}"))
        if rep.pdf_file:
            self.stdout.write(f"  PDF : {rep.pdf_file} ({rep.pdf_size} bayt)")
        if rep.xlsx_file:
            self.stdout.write(f"  XLSX: {rep.xlsx_file} ({rep.xlsx_size} bayt)")
        if rep.email_status:
            self.stdout.write(f"  E-posta: {rep.email_status} — {rep.email_info[:200]}")
        if rep.error:
            self.stdout.write(self.style.WARNING(f"  Uyarı/Hata: {rep.error}"))
