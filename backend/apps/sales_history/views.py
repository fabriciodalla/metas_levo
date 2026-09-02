from datetime import date

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAppAdmin

from .services import (
    DistributionBaselineService,
    SalesHistorySyncService,
    first_day_n_months_ago,
    sync_lock,
)


class SyncDataView(APIView):
    """Dispara manualmente, a partir do SPA, o que hoje já roda via `sync_sales_history` (CLI):
    sincroniza o Postgres externo (somente leitura) e reconstrói a base de distribuição.
    Só o Administrador (`is_admin`) pode acionar — não é uma alocação de meta, é manutenção de dado
    de apoio ao cálculo de sugestão (P1-P4)."""

    permission_classes = [IsAuthenticated, IsAppAdmin]

    def post(self, request):
        # months_back=11: mesma janela de 12 meses (mês atual + 11 anteriores) que o CLI
        # `sync_sales_history` usa por padrão (--months=12 → months_back = 12 - 1). Os dois
        # precisam bater — senão cada entrada deixa um recorte de dado diferente na tabela.
        min_date = first_day_n_months_ago(date.today(), months_back=11)

        with sync_lock():
            accumulated_count = SalesHistorySyncService.sync_accumulated(min_date)
            portfolio_count = SalesHistorySyncService.sync_portfolio()
            baseline_count = DistributionBaselineService.rebuild()

        return Response(
            {
                "synced_since": min_date,
                "accumulated_count": accumulated_count,
                "portfolio_count": portfolio_count,
                "baseline_count": baseline_count,
            }
        )
