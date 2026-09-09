from django.core.management.base import BaseCommand

from apps.hierarchy.services import ExternalSalespersonMatchingService


class Command(BaseCommand):
    """Dispara `ExternalSalespersonMatchingService.sync()` manualmente — útil pra conferir/depurar
    fora do fluxo normal, mas não é mais o único jeito de rodar isso: a mesma sincronização já
    acontece sozinha a cada abertura da tela de distribuição (ver `allocations/views.py`)."""

    help = "Cria ExternalSalespersonMapping por igualdade exata de nome (Vendedor x DistributionBaseline)."

    def handle(self, *args, **options):
        created, unmatched = ExternalSalespersonMatchingService.sync()

        self.stdout.write(self.style.SUCCESS(f"Mapeamentos criados: {len(created)}"))
        for name in sorted(created):
            self.stdout.write(f"  + {name}")

        if unmatched:
            self.stdout.write(
                self.style.WARNING(f"\nSem correspondência exata (curadoria manual): {len(unmatched)}")
            )
            for name in sorted(unmatched):
                self.stdout.write(f"  ? {name}")
