from django.core.management.base import BaseCommand

from apps.sales_history.services import DistributionBaselineService, sync_lock


class Command(BaseCommand):
    help = (
        "Reconstrói a base de cálculo de distribuição de metas a partir de AccumulatedSale + "
        "ClientPortfolioSnapshot já sincronizados localmente — não consulta o Postgres externo. "
        "Útil para recalcular sem esperar o próximo sync mensal."
    )

    def handle(self, *args, **options):
        # Mesmo lock do sync_sales_history/SyncDataView: sem ele, rodar isso enquanto um sync
        # está no meio do DELETE+bulk_create de AccumulatedSale leria a tabela num estado
        # transitório (parcial ou vazia) e gravaria uma base de distribuição errada.
        with sync_lock():
            count = DistributionBaselineService.rebuild()
        self.stdout.write(self.style.SUCCESS(f"Base de distribuição reconstruída: {count} linha(s)."))
