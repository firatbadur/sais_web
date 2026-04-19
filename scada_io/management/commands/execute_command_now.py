"""
Tek bir Command'ı manuel yürüt — debug/test için.

Worker'a enqueue etmek yerine sync olarak `execute_command` task'ını çağırır.

Kullanım:
    python manage.py execute_command_now <command_id>
"""
from django.core.management.base import BaseCommand, CommandError

from api.models import Command as CommandModel
from scada_io.tasks import execute_command


class Command(BaseCommand):
    help = "Tek bir Command için execute_command task'ını sync çalıştırır."

    def add_arguments(self, parser):
        parser.add_argument("cmd_id", type=int, help="Command.id")

    def handle(self, *args, **options):
        cmd_id = options["cmd_id"]
        if not CommandModel.objects.filter(pk=cmd_id).exists():
            raise CommandError(f"Command {cmd_id} bulunamadı.")

        self.stdout.write(f"Executing Command {cmd_id} ...")
        execute_command(cmd_id)

        cmd = CommandModel.objects.get(pk=cmd_id)
        self.stdout.write(self.style.SUCCESS(
            f"Tamamlandı. status={cmd.status}, attempt_count={cmd.attempt_count}, "
            f"error_message={cmd.error_message or '-'}, response_data={cmd.response_data or '-'}"
        ))
