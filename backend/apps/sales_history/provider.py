"""SalesHistoryProvider — a porta formal que faltava (ver docs/roadmap.md, passo 6).

Resolve `DistributionBaseline` (agregado por texto do ERP: `salesperson_name`/`subgroup_name`)
para séries `MonthlyQuantity` por entidade interna (`ProductGroup`/`HierarchyNode`), usando os
mapeamentos de O3/O5 (`ExternalProductMapping` em catalog, `ExternalSalespersonMapping` em
hierarchy). É o que `SeasonalTrendSuggestionStrategy`/`SeasonalTrendDistributionStrategy` (P1-P4,
Decisão 6) consomem quando ligadas ao histórico real, em vez de dados injetados manualmente.

Meses sem nenhuma linha em `DistributionBaseline` entram com `quantity_kg=0` — as estratégias
assumem uma série mensal consecutiva sem buracos (o índice 1..N da regressão linear corresponde a
meses do calendário andando um a um).
"""

from apps.allocations.strategies import MonthlyQuantity
from apps.catalog.models import ExternalProductMapping, ProductSubgroup
from apps.hierarchy.models import ExternalSalespersonMapping, FeristaCoverage, HierarchyNode
from apps.hierarchy.services import ScopeResolver

from .models import DistributionBaseline


def _consecutive_months(last_month: tuple[int, int], count: int) -> list[tuple[int, int]]:
    """`count` meses consecutivos terminando em `last_month` (inclusive), do mais antigo pro mais
    recente — ordem exigida pelas estratégias de P1-P4."""
    ano, mes = last_month
    months = []
    for _ in range(count):
        months.append((ano, mes))
        mes -= 1
        if mes < 1:
            mes = 12
            ano -= 1
    return list(reversed(months))


def _external_codes_for(group_id: int | None, subgroup_id: int | None) -> list[str] | None:
    """`None` significa "sem filtro de produto" (soma tudo). Lista vazia é um resultado válido
    (nenhum subgrupo desse grupo tem mapeamento ainda) — não é o mesmo que "sem filtro"."""
    if subgroup_id is not None:
        subgroup_ids = [subgroup_id]
    elif group_id is not None:
        subgroup_ids = list(ProductSubgroup.objects.filter(group_id=group_id).values_list("id", flat=True))
    else:
        return None

    return list(
        ExternalProductMapping.objects.filter(subgroup_id__in=subgroup_ids).values_list(
            "external_code", flat=True
        )
    )


def _aggregate_by_month(queryset, months: list[tuple[int, int]]) -> list[MonthlyQuantity]:
    month_set = set(months)
    totals_by_month: dict[tuple[int, int], float] = {}
    for ano, mes, total in queryset.values_list("ano", "mes", "total_quantity"):
        if (ano, mes) in month_set:
            totals_by_month[(ano, mes)] = totals_by_month.get((ano, mes), 0.0) + float(total)

    return [
        MonthlyQuantity(ano=ano, mes=mes, quantity_kg=totals_by_month.get((ano, mes), 0.0))
        for ano, mes in months
    ]


class SalesHistoryProvider:
    """Único ponto de leitura de `DistributionBaseline` para consumo das estratégias de P1-P4."""

    @staticmethod
    def group_history(
        group_id: int, period_months: int, last_month: tuple[int, int]
    ) -> list[MonthlyQuantity]:
        """Série mensal (P1, Gerente) somando todos os subgrupos do grupo, via `ExternalProductMapping`."""
        external_codes = _external_codes_for(group_id=group_id, subgroup_id=None)
        months = _consecutive_months(last_month, period_months)
        queryset = DistributionBaseline.objects.filter(subgroup_name__in=external_codes)
        return _aggregate_by_month(queryset, months)

    @staticmethod
    def target_history(
        hierarchy_node_id: int,
        period_months: int,
        last_month: tuple[int, int],
        group_id: int | None = None,
        subgroup_id: int | None = None,
    ) -> list[MonthlyQuantity]:
        """Série mensal (P2-P4) para um alvo da distribuição: soma o histórico de todo vendedor
        descendente desse nó (ele mesmo, se já for VENDEDOR). Suporta filtro por grupo OU
        subgrupo, mas o peso da distribuição (P2-P4, Decisão 6) sempre usa `group_id` — nunca
        `subgroup_id` — mesmo quando o repasse resultante é por subgrupo (P3/P4); histórico por
        subgrupo é esparso demais pra pesar com confiança (ver docs/decisions.md, Decisão 6,
        refinamento 2026-07-21).

        Cobertura de férias (Decisão 13, revisão 2026-09-10): o ferista JÁ é um Vendedor normal,
        com `ExternalSalespersonMapping` próprio — soma normal (acima) já conta o que ele vendeu
        em nome próprio. O que falta é o histórico do TITULAR coberto: nos meses em que
        `FeristaCoverage` registra esse vendedor como `covering_node` de alguém, o volume que esse
        titular (`covered_node`) tem em `DistributionBaseline` naquele mês entra somado aqui — é a
        base de sugestão do ferista, que está assumindo uma carteira que ele não construiu.

        Guarda contra dobra de contagem: só soma o volume do titular quando ele **não** está
        dentro do próprio `vendedor_ids` desta chamada — se estiver (ex.: `target_history` pedido
        pro Supervisor comum aos dois), o volume do titular já entrou pela soma normal acima, via
        o nome dele mesmo; somar de novo aqui duplicaria. Só quando a chamada é especificamente
        pro nó do ferista (titular fora de escopo) é que essa soma extra faz sentido.
        """
        vendedor_ids = list(
            HierarchyNode.objects.filter(
                id__in=ScopeResolver.descendant_ids(hierarchy_node_id), level=HierarchyNode.Level.VENDEDOR
            ).values_list("id", flat=True)
        )
        vendedor_id_set = set(vendedor_ids)
        salesperson_names = list(
            ExternalSalespersonMapping.objects.filter(hierarchy_node_id__in=vendedor_ids).values_list(
                "external_name", flat=True
            )
        )

        months = _consecutive_months(last_month, period_months)
        external_codes = _external_codes_for(group_id=group_id, subgroup_id=subgroup_id)

        queryset = DistributionBaseline.objects.filter(salesperson_name__in=salesperson_names)
        if external_codes is not None:
            queryset = queryset.filter(subgroup_name__in=external_codes)
        history = _aggregate_by_month(queryset, months)

        month_set = set(months)
        coverage = [
            (covered_node_id, ano, mes)
            for covered_node_id, ano, mes in FeristaCoverage.objects.filter(
                covering_node_id__in=vendedor_ids
            ).values_list("covered_node_id", "ano", "mes")
            if (ano, mes) in month_set and covered_node_id not in vendedor_id_set
        ]
        if not coverage:
            return history

        titular_name_by_node_id = dict(
            ExternalSalespersonMapping.objects.filter(
                hierarchy_node_id__in={covered_node_id for covered_node_id, _, _ in coverage}
            ).values_list("hierarchy_node_id", "external_name")
        )

        coverage_queryset = DistributionBaseline.objects.filter(
            salesperson_name__in=set(titular_name_by_node_id.values())
        )
        if external_codes is not None:
            coverage_queryset = coverage_queryset.filter(subgroup_name__in=external_codes)

        totals_by_name_month: dict[tuple[str, int, int], float] = {}
        for name, ano, mes, total in coverage_queryset.values_list(
            "salesperson_name", "ano", "mes", "total_quantity"
        ):
            key = (name, ano, mes)
            totals_by_name_month[key] = totals_by_name_month.get(key, 0.0) + float(total)

        by_month = {(point.ano, point.mes): point.quantity_kg for point in history}
        for covered_node_id, ano, mes in coverage:
            titular_name = titular_name_by_node_id.get(covered_node_id)
            if titular_name is None:
                continue
            by_month[(ano, mes)] = by_month.get((ano, mes), 0.0) + totals_by_name_month.get(
                (titular_name, ano, mes), 0.0
            )

        return [MonthlyQuantity(ano=ano, mes=mes, quantity_kg=by_month[(ano, mes)]) for ano, mes in months]
