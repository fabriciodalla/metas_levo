import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

REQUIRED_VARS = ["ADMIN_EMAIL", "ADMIN_NAME", "ADMIN_PASSWORD"]


class Command(BaseCommand):
    help = (
        "Cria (ou atualiza) o administrador da aplicação a partir de ADMIN_EMAIL, ADMIN_NAME e "
        "ADMIN_PASSWORD. Idempotente: rodar de novo com o usuário já existente atualiza a senha "
        "e garante is_admin=True, em vez de falhar ou duplicar. Não roda no entrypoint de "
        "propósito — precisa ser disparado manualmente, senão a senha seria revertida a cada "
        "reinício do contêiner."
    )

    def handle(self, *args, **options):
        missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
        if missing:
            raise CommandError(
                "Variável(is) de ambiente faltando: " + ", ".join(missing) + ". "
                "Nenhum usuário foi criado ou alterado."
            )

        email = os.environ["ADMIN_EMAIL"]
        username = os.environ["ADMIN_NAME"]
        password = os.environ["ADMIN_PASSWORD"]

        User = get_user_model()
        user = User.objects.filter(email=email).first()

        if user is None:
            User.objects.create_superuser(username=username, email=email, password=password, is_admin=True)
            action = "criado"
        else:
            user.username = username
            user.set_password(password)
            user.is_superuser = True
            user.is_staff = True
            user.is_admin = True
            user.save()
            action = "atualizado"

        self.stdout.write(self.style.SUCCESS(f"Administrador {action}: {email}"))
