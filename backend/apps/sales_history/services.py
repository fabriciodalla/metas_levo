import datetime
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import connection, connections, transaction

from apps.catalog.models import ExternalProductMapping, ProductGroup
from apps.hierarchy.models import ExternalSalespersonMapping, FeristaCoverage, HierarchyNode

from .models import AccumulatedSale, ClientPortfolioSnapshot, DistributionBaseline
from .provider import _consecutive_months
from .queries import ACUMULADO_SQL, CARTEIRA_SQL

# Chave arbitrária fixa pro advisory lock do Postgres (qualquer bigint serve, só precisa ser
# sempre a mesma). Ver `sync_lock`.
_SYNC_LOCK_ID = 8271_9430_01


@contextmanager
def sync_lock():
    """Serializa qualquer combinação de sync_accumulated + sync_portfolio + rebuild entre chamadas
    concorrentes — hoje isso pode acontecer via CLI (`sync_sales_history`) e via botão do SPA
    (`SyncDataView`) ao mesmo tempo, ou dois cliques/duas abas batendo o botão.

    Sem isso, duas sincronizações sobrepostas duplicam `AccumulatedSale`: cada uma roda seu
    próprio DELETE + bulk_create dentro de um `transaction.atomic()` isolado, mas nenhuma enxerga
    a outra até commitar (READ COMMITTED) — então o DELETE de uma não remove o INSERT ainda não
    commitado da outra, e o resultado final é a união dos dois lotes (dado 2x). Foi exatamente o
    que aconteceu em produção (ver investigação de 2026-09-02).

    `pg_advisory_xact_lock` bloqueia a segunda chamada até a primeira transação terminar (commit
    ou rollback) — sem precisar de unlock manual nem de infra nova (Celery/Redis).
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_SYNC_LOCK_ID])
        yield


def _fetch_as_dicts(alias: str, sql: str, params: list | None = None) -> list[dict]:
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def first_day_n_months_ago(today: datetime.date, months_back: int) -> datetime.date:
    """Compartilhado entre o comando `sync_sales_history` e o endpoint de sync do Administrador,
    pra não duplicar a aritmética de janela de meses (H2, 12 meses por padrão)."""
    year = today.year
    month = today.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return datetime.date(year, month, 1)


class SalesHistorySyncService:
    """Roda as duas consultas no Postgres externo (somente leitura) e grava o resultado nas
    tabelas locais da aplicação. Nenhuma fórmula de negócio roda aqui — só espelha os dados.
    """

    @staticmethod
    def sync_accumulated(min_date: datetime.date) -> int:
        rows = _fetch_as_dicts("sales_history", ACUMULADO_SQL, [min_date])

        with transaction.atomic():
            # Substitui a tabela inteira, não só `sale_date >= min_date`: um sync anterior rodado
            # com uma janela maior (ex.: --months=15 avulso) não pode deixar meses fora da janela
            # oficial (H2 = 12 meses, Decisão 6) sobrando pra sempre — `DistributionBaselineService
            # .rebuild()` lê a tabela toda, sem filtro de data, então qualquer linha esquecida aqui
            # entra na base de cálculo. Mesmo padrão que `sync_portfolio` já usava.
            AccumulatedSale.objects.all().delete()
            AccumulatedSale.objects.bulk_create(
                AccumulatedSale(
                    nk_supervisor=row["nk_supervisor"],
                    nk_vendedor=row["nk_vendedor"],
                    salesperson_name=row["nome_vendedor"],
                    client_code=row["clifor"],
                    cnpj=row["cnpj"] or "",
                    client_name=row["nome_cliente"] or "",
                    sale_date=row["dt_emissao"],
                    subgroup_name=row["ds_subgrupo"],
                    total_quantity=row["total_ps_atendido"],
                    total_value=row["total_vl_movtocontabil"],
                )
                for row in rows
            )

        return len(rows)

    @staticmethod
    def sync_portfolio() -> int:
        rows = _fetch_as_dicts("sales_history", CARTEIRA_SQL)

        with transaction.atomic():
            ClientPortfolioSnapshot.objects.all().delete()
            ClientPortfolioSnapshot.objects.bulk_create(
                ClientPortfolioSnapshot(
                    client_code=row["clifor"],
                    cnpj=row["cnpj"] or "",
                    client_name=row["nome_cliente"],
                    salesperson_name=row["nome_vendedor"],
                    nk_supervisor=row["nk_supervisor"],
                    municipio=row["municipio"] or "",
                    estado=row["estado"] or "",
                    registered_at=row["cadastro"],
                    last_changed_at=row["alterado"],
                )
                for row in rows
            )

        return len(rows)


class DistributionBaselineService:
    """Constrói a base de cálculo de distribuição de metas a partir das duas tabelas locais.

    Reatribui cada linha do acumulado ao vendedor ATUAL da carteira do cliente (join por
    `client_code`, comum às duas tabelas) — não importa quem historicamente vendeu, importa
    quanto o cliente comprou, e esse total conta para quem hoje é responsável por ele. Clientes do
    acumulado sem entrada na carteira atual entram com `salesperson_name=None` em vez de ficar de
    fora: sem vendedor vigente não dá pra atribuir a um Vendedor/Supervisor (P2-P4), mas o volume
    ainda é real e deve contar na sugestão de meta do Gerente (P1, que soma por subgrupo sem
    filtrar por vendedor — ver `SalesHistoryProvider.group_history`).

    Cobertura de férias (Decisão 13) no mês corrente: se um `FeristaCoverage` cobre o mês/ano de
    hoje, as linhas desse mês vendidas em nome do ferista já saem daqui atribuídas ao titular
    (`covered_node`), em vez de precisar do redirecionamento avulso de
    `SalesHistoryProvider.target_history`. Meses passados/futuros cadastrados em
    `FeristaCoverage` são ignorados aqui de propósito — só o mês atual da reconstrução conta —,
    continuam sendo tratados por `target_history` mês a mês. Sem isso, telas que leem
    `DistributionBaseline` direto (ex.: `VendorGroupSummaryService`) nunca veriam esse volume, já
    que o ferista não tem `ExternalSalespersonMapping` próprio.
    """

    @staticmethod
    @transaction.atomic
    def rebuild(today: datetime.date | None = None) -> int:
        today = today or datetime.date.today()

        current_salesperson_by_client = dict(
            ClientPortfolioSnapshot.objects.values_list("client_code", "salesperson_name")
        )

        titular_external_name_by_node_id = dict(
            ExternalSalespersonMapping.objects.values_list("hierarchy_node_id", "external_name")
        )
        ferista_redirect: dict[str, str] = {}
        for external_name, covered_node_id in FeristaCoverage.objects.filter(
            ano=today.year, mes=today.month
        ).values_list("external_name", "covered_node_id"):
            titular_external_name = titular_external_name_by_node_id.get(covered_node_id)
            if titular_external_name:
                ferista_redirect[external_name] = titular_external_name

        totals: dict[tuple[int, int, str | None, str], Decimal] = defaultdict(Decimal)
        rows = AccumulatedSale.objects.values_list(
            "sale_date", "client_code", "subgroup_name", "total_quantity"
        )
        for sale_date, client_code, subgroup_name, quantity in rows:
            salesperson_name = current_salesperson_by_client.get(client_code)
            if (
                sale_date.year == today.year
                and sale_date.month == today.month
                and salesperson_name in ferista_redirect
            ):
                salesperson_name = ferista_redirect[salesperson_name]
            key = (sale_date.year, sale_date.month, salesperson_name, subgroup_name)
            totals[key] += quantity

        DistributionBaseline.objects.all().delete()
        DistributionBaseline.objects.bulk_create(
            DistributionBaseline(
                ano=ano,
                mes=mes,
                salesperson_name=salesperson_name,
                subgroup_name=subgroup_name,
                # Regra de arredondamento confirmada pelo usuário (2026-07): >= 0,5 sobe, < 0,5
                # desce — não é o método do maior resto (P5/Decisão 7), que serve pra fechar o
                # repasse hierárquico; aqui é só a base histórica virando KG inteiro.
                total_quantity=total_quantity.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
            )
            for (ano, mes, salesperson_name, subgroup_name), total_quantity in totals.items()
        )

        return len(totals)


class VendorGroupSummaryService:
    """Visão do Administrador (tela Pré-processamento): quanto cada Vendedor ATIVO vendeu em
    média (3 e 12 meses) por Grupo de produto, segundo `DistributionBaseline` — e quais
    Vendedores ativos não têm nenhum nome batendo no histórico sincronizado (`mapeado=False`),
    sinal de que falta gente pra curar em `ExternalSalespersonMapping` antes da sugestão
    automática (P1-P4) enxergar o time todo."""

    @staticmethod
    def summary(today: datetime.date | None = None) -> dict:
        today = today or datetime.date.today()
        months_12 = _consecutive_months((today.year, today.month), 12)
        months_3 = set(months_12[-3:])
        months_12_set = set(months_12)

        vendedores = list(
            HierarchyNode.objects.filter(level=HierarchyNode.Level.VENDEDOR, ativo=True)
            .select_related("parent__parent")
            .order_by("nome")
        )
        name_to_node_id: dict[str, int] = dict(
            ExternalSalespersonMapping.objects.filter(
                hierarchy_node_id__in=[vendedor.id for vendedor in vendedores]
            ).values_list("external_name", "hierarchy_node_id")
        )
        mapped_node_ids = set(name_to_node_id.values())

        code_to_group: dict[str, tuple[int, str]] = {}
        for mapping in ExternalProductMapping.objects.select_related("group", "subgroup__group"):
            group = mapping.group or (mapping.subgroup.group if mapping.subgroup_id else None)
            if group is not None:
                code_to_group[mapping.external_code] = (group.id, group.nome)

        grupos = list(ProductGroup.objects.filter(ativo=True).order_by("nome").values("id", "nome"))

        totals: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: {"3": 0.0, "12": 0.0})
        rows = DistributionBaseline.objects.filter(salesperson_name__in=name_to_node_id.keys()).values_list(
            "ano", "mes", "salesperson_name", "subgroup_name", "total_quantity"
        )
        for ano, mes, salesperson_name, subgroup_name, quantity in rows:
            if (ano, mes) not in months_12_set:
                continue
            group_info = code_to_group.get(subgroup_name)
            if group_info is None:
                continue
            key = (name_to_node_id[salesperson_name], group_info[0])
            totals[key]["12"] += float(quantity)
            if (ano, mes) in months_3:
                totals[key]["3"] += float(quantity)

        vendedor_rows = []
        for vendedor in vendedores:
            supervisor = vendedor.parent
            local = supervisor.parent if supervisor else None
            grupo_totals = []
            for grupo in grupos:
                data = totals.get((vendedor.id, grupo["id"]))
                grupo_totals.append(
                    {
                        "grupo_id": grupo["id"],
                        "avg_3_months_kg": (data["3"] / 3) if data else 0.0,
                        "avg_12_months_kg": (data["12"] / 12) if data else 0.0,
                    }
                )
            vendedor_rows.append(
                {
                    "id": vendedor.id,
                    "nome": vendedor.nome,
                    "mapeado": vendedor.id in mapped_node_ids,
                    "supervisor_id": supervisor.id if supervisor else None,
                    "supervisor_nome": supervisor.nome if supervisor else None,
                    "local_id": local.id if local else None,
                    "local_nome": local.nome if local else None,
                    "totals": grupo_totals,
                }
            )

        return {"grupos": grupos, "vendedores": vendedor_rows}


@dataclass(frozen=True)
class VendorSubgroupExportRow:
    regional_nome: str
    local_nome: str
    vendedor_nome: str
    subgrupo_nome: str
    sum_3_months_kg: int
    sum_12_months_kg: int
    avg_3_months_kg: float
    avg_12_months_kg: float


class VendorSubgroupExportService:
    """CSV completo (Pré-processamento, "Resumo por vendedor e grupo"): mesma base de
    `VendorGroupSummaryService`, mas por SUBGRUPO em vez de grupo, com soma além da média — pedido
    do usuário pra auditar o dado bruto por trás das médias mostradas na tela, incluindo a
    ancestralidade até Coordenador Regional (a tela só mostra até Coordenador Local/Supervisor).

    Soma e média usam sempre o mesmo divisor fixo (3 ou 12 meses) — não a quantidade de linhas que
    contribuíram pra soma —, igual a `VendorGroupSummaryService`: um subgrupo sem venda num mês
    entra com 0 naquele mês (não fica de fora da soma nem muda o divisor)."""

    @staticmethod
    def rows(today: datetime.date | None = None) -> list[VendorSubgroupExportRow]:
        today = today or datetime.date.today()
        months_12 = _consecutive_months((today.year, today.month), 12)
        months_3 = set(months_12[-3:])
        months_12_set = set(months_12)

        vendedores = list(
            HierarchyNode.objects.filter(level=HierarchyNode.Level.VENDEDOR, ativo=True)
            .select_related("parent__parent__parent")
            .order_by("nome")
        )
        name_to_node_id: dict[str, int] = dict(
            ExternalSalespersonMapping.objects.filter(
                hierarchy_node_id__in=[vendedor.id for vendedor in vendedores]
            ).values_list("external_name", "hierarchy_node_id")
        )

        code_to_subgrupo_nome: dict[str, str] = {}
        for mapping in ExternalProductMapping.objects.select_related("group", "subgroup"):
            if mapping.subgroup_id:
                code_to_subgrupo_nome[mapping.external_code] = mapping.subgroup.nome
            elif mapping.group_id:
                code_to_subgrupo_nome[mapping.external_code] = mapping.group.nome

        totals: dict[tuple[int, str], dict[str, float]] = defaultdict(lambda: {"3": 0.0, "12": 0.0})
        rows_qs = DistributionBaseline.objects.filter(
            salesperson_name__in=name_to_node_id.keys()
        ).values_list("ano", "mes", "salesperson_name", "subgroup_name", "total_quantity")
        for ano, mes, salesperson_name, subgroup_name, quantity in rows_qs:
            if (ano, mes) not in months_12_set:
                continue
            subgrupo_nome = code_to_subgrupo_nome.get(subgroup_name)
            if subgrupo_nome is None:
                continue
            key = (name_to_node_id[salesperson_name], subgrupo_nome)
            totals[key]["12"] += float(quantity)
            if (ano, mes) in months_3:
                totals[key]["3"] += float(quantity)

        subgrupos_by_vendedor: dict[int, list[str]] = defaultdict(list)
        for vendedor_id, subgrupo_nome in totals:
            subgrupos_by_vendedor[vendedor_id].append(subgrupo_nome)

        # Pedido do usuário (2026-09-04): a base completa não deve trazer combinação
        # vendedor/subgrupo sem venda nos últimos 12 meses (média 12 meses <= 0) — inclui, por
        # consequência, quem não tem nenhum histórico sincronizado (sum_12 sempre 0 nesse caso).
        result: list[VendorSubgroupExportRow] = []
        for vendedor in vendedores:
            supervisor = vendedor.parent
            local = supervisor.parent if supervisor else None
            regional = local.parent if local else None
            regional_nome = regional.nome if regional else ""
            local_nome = local.nome if local else ""

            for subgrupo_nome in sorted(subgrupos_by_vendedor.get(vendedor.id, [])):
                data = totals[(vendedor.id, subgrupo_nome)]
                if data["12"] <= 0:
                    continue
                result.append(
                    VendorSubgroupExportRow(
                        regional_nome=regional_nome,
                        local_nome=local_nome,
                        vendedor_nome=vendedor.nome,
                        subgrupo_nome=subgrupo_nome,
                        sum_3_months_kg=round(data["3"]),
                        sum_12_months_kg=round(data["12"]),
                        avg_3_months_kg=data["3"] / 3,
                        avg_12_months_kg=data["12"] / 12,
                    )
                )

        return result
