"""
Idempotent admin (rol=1) kullanıcı oluşturur — installer ilk kurulumda kullanır.

Bilgileri argümandan veya ortam değişkenlerinden okur:
    DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD

`createsuperuser --noinput` yerine bu komut kullanılır çünkü CustomUser.rol=1
ataması staff/superuser bayraklarını otomatik açar (users/models.py save()).
Kullanıcı zaten varsa parola/rol güncellenmez (idempotent, tekrar çalıştırılabilir).

Kullanım:
    python manage.py seed_admin_user --username admin --email a@b.com --password ***
    # veya ortam değişkenleriyle:
    DJANGO_SUPERUSER_USERNAME=admin ... python manage.py seed_admin_user
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from users.models import CustomUser


class Command(BaseCommand):
    help = "Idempotent admin (rol=1) kullanıcı oluşturur (installer first-run)."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=None)
        parser.add_argument("--email", default=None)
        parser.add_argument("--password", default=None)

    def handle(self, *args, **options):
        username = options["username"] or os.getenv("DJANGO_SUPERUSER_USERNAME")
        email = options["email"] or os.getenv("DJANGO_SUPERUSER_EMAIL", "")
        password = options["password"] or os.getenv("DJANGO_SUPERUSER_PASSWORD")

        if not username or not password:
            raise CommandError(
                "username ve password zorunlu (--username/--password veya "
                "DJANGO_SUPERUSER_USERNAME/PASSWORD ortam değişkenleri)."
            )

        existing = CustomUser.objects.filter(username=username).first()
        if existing:
            self.stdout.write(self.style.WARNING(
                f"'{username}' zaten var — değişiklik yapılmadı (idempotent)."
            ))
            return

        user = CustomUser(username=username, email=email, rol=1)
        user.set_password(password)
        user.save()  # rol=1 → is_staff/is_superuser otomatik açılır
        self.stdout.write(self.style.SUCCESS(
            f"Admin oluşturuldu: {username} (rol=1, superuser)."
        ))
