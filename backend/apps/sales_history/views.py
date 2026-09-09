import csv
import io
from datetime import date

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAppAdmin
from apps.hierarchy.services import ExternalSalespersonMatchingService

from .services import (
    DistributionBaselineService,
    SalesHistorySyncService,
    VendorGroupSummaryService,
    VendorSubgroupExportService,
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
        # months_back=12: mesma janela do CLI `sync_sales_history` (--months=12 → months_back=12,
        # âncora em mês atual - 1 pra trás, mês corrente nunca entra). Os dois precisam bater —
        # senão cada entrada deixa um recorte de dado diferente na tabela. A âncora em "mês atual -
        # 1" (não no mês atual) garante que o cálculo sazonal de P1 sempre tenha o mesmo mês do ano
        # anterior disponível (ver Decisão 6, revisão).
        min_date = first_day_n_months_ago(date.today(), months_back=12)

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


class VendorGroupSummaryView(APIView):
    """Tabela Vendedor x Grupo (médias de 3/12 meses) + sinalização de Vendedor ativo sem
    mapeamento no histórico sincronizado — ver `VendorGroupSummaryService`."""

    permission_classes = [IsAuthenticated, IsAppAdmin]

    def get(self, request):
        # Garante que o mapeamento de nome esteja atualizado antes de decidir quem está
        # "sem sincronização" — mesmo refresh automático já disparado nas telas de distribuição.
        ExternalSalespersonMatchingService.sync()
        return Response(VendorGroupSummaryService.summary())


class VendorSubgroupExportView(APIView):
    """CSV completo por Vendedor x Subgrupo (soma e média de 3/12 meses), com a ancestralidade até
    Coordenador Regional — ver `VendorSubgroupExportService`."""

    permission_classes = [IsAuthenticated, IsAppAdmin]

    def get(self, request):
        ExternalSalespersonMatchingService.sync()
        rows = VendorSubgroupExportService.rows()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "coordenador_regional",
                "coordenador_local",
                "vendedor",
                "subgrupo",
                "soma_3_meses_kg",
                "soma_12_meses_kg",
                "media_3_meses_kg",
                "media_12_meses_kg",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.regional_nome,
                    row.local_nome,
                    row.vendedor_nome,
                    row.subgrupo_nome,
                    row.sum_3_months_kg,
                    row.sum_12_months_kg,
                    f"{row.avg_3_months_kg:.2f}",
                    f"{row.avg_12_months_kg:.2f}",
                ]
            )

        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=resumo_vendedor_subgrupo.csv"
        return response
