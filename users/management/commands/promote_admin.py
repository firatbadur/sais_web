"""
Belirtilen kullanıcıyı Sistem Yöneticisi (rol=1, is_staff=True, is_superuser=True) yapar.

Kullanım:
    python manage.py promote_admin <username>
"""
from django.core.management.base import BaseCommand, CommandError

from users.models import CustomUser


class Command(BaseCommand):
    help = "Kullanıcıyı Sistem Yöneticisi (rol=1) + staff + superuser yapar."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Güncellenecek kullanıcı adı")

    def handle(self, *args, **options):
        username = options["username"]
        try:
            user = CustomUser.objects.get(username=username)
        except CustomUser.DoesNotExist as exc:
            raise CommandError(f"'{username}' kullanıcısı bulunamadı.") from exc

        user.rol = 1
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"{username} artık rol=1 / is_staff={user.is_staff} / is_superuser={user.is_superuser}"
            )
        )
