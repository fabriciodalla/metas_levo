from datetime import date

from django.core.management.base import BaseCommand

from apps.sales_history.services import (
    DistributionBaselineService,
    SalesHistorySyncService,
    first_day_n_months_ago,
    sync_lock,
)


class Command(BaseCommand):
    help = (
        "Sincroniza acumulado de vendas e carteira de clientes do Postgres externo (somente "
        "leitura) para as tabelas locais da aplicação."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--months",
            type=int,
            default=12,
            help=(
                "Quantos meses completos anteriores ao atual sincronizar (o mês corrente, ainda "
                "em andamento, nunca entra na janela). Default: 12 — janela confirmada com o "
                "usuário (H2 resolvida) para a decomposição tendência+sazonalidade de P1-P4, ver "
                "docs/decisions.md (Decisão 6). A âncora fica sempre em mês atual - 1, para o "
                "cálculo sazonal de P1 sempre ter o mesmo mês do ano anterior disponível "
                "(sem isso a janela fica 1 mês curta e a sugestão sai zerada — ver Decisão 6, "
                "revisão)."
            ),
        )

    def handle(self, *args, **options):
        min_date = first_day_n_months_ago(date.today(), options["months"])

        with sync_lock():
            accumulated_count = SalesHistorySyncService.sync_accumulated(min_date)
            self.stdout.write(f"Acumulado: {accumulated_count} linha(s) sincronizada(s) desde {min_date}.")

            portfolio_count = SalesHistorySyncService.sync_portfolio()
            self.stdout.write(f"Carteira: {portfolio_count} cliente(s) sincronizado(s).")

            baseline_count = DistributionBaselineService.rebuild()
            self.stdout.write(f"Base de distribuição: {baseline_count} linha(s) recalculada(s).")
