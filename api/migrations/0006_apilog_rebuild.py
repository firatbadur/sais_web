"""ApiLog modelini sıfırdan kurar.

Eski ApiLog tablosu (type/url/data/header/param/token/response/status/time_iso)
şemasından yeni şemaya (direction/method/url/headers/body/response/duration/...)
geçişi tek migration ile yapar. Eski `api_log` tablosu manuel olarak düşürüldüğü
için state'te DeleteModel uygulanır ama DB tarafında DROP TABLE yapılmaz.
Yeni tablo standart CreateModel ile oluşturulur.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0005_command_delete_outputrequest_command_cmd_queue_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1) Django state'inden eski ApiLog'u sil; DB tarafında DROP atılmaz
        # çünkü tablo zaten elle düşürüldü.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ApiLog"),
            ],
            database_operations=[],
        ),
        # 2) Yeni şemayla taze tablo oluştur.
        migrations.CreateModel(
            name="ApiLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("direction", models.CharField(
                    choices=[("in", "Gelen (Inbound)"), ("out", "Giden (Outbound)")],
                    db_index=True, max_length=3, verbose_name="Yön",
                )),
                ("method", models.CharField(db_index=True, max_length=10, verbose_name="HTTP Method")),
                ("url", models.CharField(
                    db_index=True, max_length=2048, verbose_name="URL",
                    help_text="Inbound: request.path; Outbound: tam URL",
                )),
                ("query_string", models.TextField(blank=True, default="", verbose_name="Query String")),
                ("request_headers", models.TextField(
                    blank=True, default="",
                    help_text="JSON-serialized; Authorization/Cookie/X-API-Key maskeli",
                    verbose_name="Request Headers",
                )),
                ("request_body", models.TextField(blank=True, default="", verbose_name="Request Body")),
                ("response_status", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="Response Status")),
                ("response_body", models.TextField(blank=True, default="", verbose_name="Response Body")),
                ("duration_ms", models.IntegerField(blank=True, null=True, verbose_name="Süre (ms)")),
                ("error_message", models.CharField(blank=True, default="", max_length=1000, verbose_name="Hata Mesajı")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Kayıt Zamanı")),
                ("remote_ip", models.GenericIPAddressField(blank=True, null=True, verbose_name="Client IP")),
                ("user_agent", models.CharField(blank=True, default="", max_length=500, verbose_name="User-Agent")),
                ("target_host", models.CharField(
                    blank=True, default="",
                    help_text="Giden çağrıda hedef hostname (filtre için)",
                    max_length=255, verbose_name="Hedef Host",
                )),
                ("source_component", models.CharField(
                    blank=True, default="",
                    help_text="Çağrıyı yapan iç bileşen (örn. bakanlik_uploader)",
                    max_length=100, verbose_name="Kaynak Modül",
                )),
                ("retry_count", models.IntegerField(
                    blank=True, null=True,
                    help_text="0 = ilk deneme, >0 = retry",
                    verbose_name="Deneme No",
                )),
                ("user", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="api_logs", to=settings.AUTH_USER_MODEL,
                    verbose_name="Kullanıcı",
                )),
            ],
            options={
                "verbose_name_plural": "API Log Kayıtları",
                "db_table": "api_log",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="apilog",
            index=models.Index(fields=["-created_at"], name="apilog_created_idx"),
        ),
        migrations.AddIndex(
            model_name="apilog",
            index=models.Index(fields=["direction", "-created_at"], name="apilog_dir_created_idx"),
        ),
        migrations.AddIndex(
            model_name="apilog",
            index=models.Index(fields=["url", "response_status"], name="apilog_url_status_idx"),
        ),
    ]
