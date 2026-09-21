import csv
import io
from datetime import date

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAppAdmin
from apps.hierarchy.models import HierarchyNode
from apps.hierarchy.services import ExternalSalespersonMatchingService

from .client_results import ClientResultsService
from .serializers import ClientAccumuladoResultSerializer
from .services import (
    DistributionBaselineService,
    SalesHistorySyncService,
    VendorGroupSummaryService,
    VendorSubgroupExportService,
    first_day_n_months_ago,
    sync_lock,
)


def _csv_safe(value):
    """Prefixa com apóstrofo campos que comecem com =, +, - ou @ — sem isso, um nome vindo do
    ERP externo pode ser interpretado como fórmula ao abrir o CSV no Excel/LibreOffice (CSV
    injection)."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


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
    Coordenador Local — ver `VendorSubgroupExportService`."""

    permission_classes = [IsAuthenticated, IsAppAdmin]

    def get(self, request):
        ExternalSalespersonMatchingService.sync()
        rows = VendorSubgroupExportService.rows()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "coordenador_local",
                "vendedor",
                "grupo",
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
                    _csv_safe(row.local_nome),
                    _csv_safe(row.vendedor_nome),
                    _csv_safe(row.grupo_nome),
                    _csv_safe(row.subgrupo_nome),
                    row.sum_3_months_kg,
                    row.sum_12_months_kg,
                    f"{row.avg_3_months_kg:.2f}",
                    f"{row.avg_12_months_kg:.2f}",
                ]
            )

        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=resumo_vendedor_subgrupo.csv"
        return response


def _resolve_acumulado_clientes_params(request):
    """Valida ano/mes/node e a visibilidade do usuário sobre o nó — compartilhado entre
    `ClientAccumuladoView` (a tela) e `ClientesSemCompraExportView` (o CSV), que recebem
    exatamente os mesmos parâmetros e a mesma regra de acesso. Retorna `(node, ano, mes, None)` em
    caso de sucesso, ou `(None, None, None, response_de_erro)` pra o caller retornar direto."""
    ano = request.query_params.get("ano")
    mes = request.query_params.get("mes")
    node_id = request.query_params.get("node")
    if not ano or not mes or not node_id:
        return (
            None,
            None,
            None,
            Response(
                {"detail": "Parâmetros ano, mes e node são obrigatórios."},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )

    try:
        ano = int(ano)
        mes = int(mes)
    except ValueError:
        return (
            None,
            None,
            None,
            Response({"detail": "ano e mes devem ser números."}, status=status.HTTP_400_BAD_REQUEST),
        )
    if not 1 <= mes <= 12:
        return (
            None,
            None,
            None,
            Response({"detail": "mes deve estar entre 1 e 12."}, status=status.HTTP_400_BAD_REQUEST),
        )

    node = get_object_or_404(HierarchyNode, id=node_id)

    if not HierarchyNode.objects.visible_to(request.user).filter(id=node.id).exists():
        return (
            None,
            None,
            None,
            Response({"detail": "Você não tem acesso a esse nó."}, status=status.HTTP_403_FORBIDDEN),
        )

    return node, ano, mes, None


class ClientAccumuladoView(APIView):
    """Tela "Acompanhamento > Acumulado de Clientes": captação, positivação e a lista de clientes
    sem compra no mês, consolidado na sub-árvore do nó filtrado — mesmo padrão de acesso do
    Acumulado de Vendas (`GoalAllocationViewSet.acumulado_vendas`): qualquer usuário autenticado
    pode consultar, desde que o nó esteja dentro da própria visibilidade (não é ferramenta de
    Administrador, ao contrário das outras views deste arquivo).

    Recebe `ano`/`mes` direto (não `cycle`, ao contrário do Acumulado de Vendas): esta tela não
    compara com meta nenhuma, então não faz sentido limitar o filtro aos meses que têm um `Cycle`
    de metas cadastrado — o usuário precisa poder olhar qualquer mês com dado de venda sincronizado
    (revisão 2026-09-17)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        node, ano, mes, error = _resolve_acumulado_clientes_params(request)
        if error:
            return error

        # Mesmo motivo do Acumulado de Vendas: garante que um Vendedor cadastrado depois da
        # última sincronização manual já entra na carteira/captação corretamente.
        ExternalSalespersonMatchingService.sync()
        result = ClientResultsService.build(node, ano, mes)
        return Response(ClientAccumuladoResultSerializer(result).data)


class ClientesSemCompraExportView(APIView):
    """CSV "desagrupado" da lista de clientes sem compra no ciclo: uma linha por cliente × subgrupo
    da última compra, em vez da linha única por cliente que a tela mostra (pedido do usuário,
    2026-09-18 — a tela agrupa por cliente pra não ficar gigante, mas a planilha traz o detalhe).
    Mesmos parâmetros/regra de acesso de `ClientAccumuladoView`."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        node, ano, mes, error = _resolve_acumulado_clientes_params(request)
        if error:
            return error

        ExternalSalespersonMatchingService.sync()
        result = ClientResultsService.build(node, ano, mes)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["clifor", "cliente", "ultima_compra", "subgrupo", "peso_kg"])
        for row in result.clientes_sem_compra:
            ultima_compra = (
                f"{row.ultima_compra_mes:02d}/{row.ultima_compra_ano}" if row.ultima_compra_ano else ""
            )
            cliente = _csv_safe(row.client_name.upper())
            if not row.itens_ultima_compra:
                writer.writerow([row.client_code, cliente, ultima_compra, "", ""])
                continue
            for item in row.itens_ultima_compra:
                writer.writerow(
                    [
                        row.client_code,
                        cliente,
                        ultima_compra,
                        _csv_safe(item.subgroup_name),
                        f"{item.peso_kg:.2f}",
                    ]
                )

        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = (
            f"attachment; filename=clientes_sem_compra_{node.id}_{mes:02d}-{ano}.csv"
        )
        return response
