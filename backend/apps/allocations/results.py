"""Tela "Acompanhamento > Acumulado de Vendas": Meta vs Realizado por grupo/subgrupo de produto,
consolidado na sub-árvore de um nó da hierarquia, com detalhe de equipe (subordinados diretos) —
pra apoiar o usuário a ver o que falta pra bater a meta, não só o quanto já vendeu.

Cortes de status confirmados explicitamente com o usuário (2026-09-15, ver docs/decisions.md):
verde >=99,5% (mesma tolerância de fechamento usado em P1-P4), amarelo 90%-99,4%, vermelho <90%
para o volume do grupo; para "% subgrupos atingidos", >=62,5% conta como atingido (zona de
tolerância 62,5%-65% arredonda pra cima, mesma regra do sistema de bonificação legado
`fechamento_levo`, adaptada aqui só pro acompanhamento — não paga nada, só informa)."""

import calendar
from dataclasses import dataclass
from datetime import date

from django.db.models import Sum

from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import HierarchyNode
from apps.hierarchy.services import ScopeResolver
from apps.sales_history.provider import SalesHistoryProvider

from .models import GoalAllocation

TOLERANCIA_VOLUME = 0.995
AMARELO_MINIMO = 0.90
SUBGRUPOS_MINIMO = 0.625
HISTORY_MONTHS = 12  # meses anteriores ao mês do ciclo, além do próprio mês

_STATUS_RISCO = {"VERMELHO": 0, "AMARELO": 1, "VERDE": 2, "SEM_META": 3}


@dataclass(frozen=True)
class SubgroupResult:
    subgroup_id: int
    subgroup_nome: str
    meta_kg: int
    realizado_kg: float
    pct: float | None
    atingiu: bool | None


@dataclass(frozen=True)
class GroupResult:
    group_id: int
    group_nome: str
    meta_kg: int
    realizado_kg: float
    pct: float | None
    faltam_kg: int
    status: str
    ritmo_kg_dia_util: float | None
    tendencia_kg: float | None
    same_month_last_year_kg: float | None
    last_3_months_avg_kg: float | None
    subgrupos_com_meta: int
    subgrupos_atingidos: int
    pct_subgrupos: float | None
    subgrupos_atingiu: bool | None
    atingiu_grupo: bool | None
    subgrupos: list[SubgroupResult]


@dataclass(frozen=True)
class TeamGroupSummary:
    group_id: int
    group_nome: str
    meta_kg: int
    realizado_kg: float
    pct: float | None
    status: str
    pct_subgrupos: float | None
    subgrupos_atingiu: bool | None
    atingiu_grupo: bool | None


@dataclass(frozen=True)
class TeamMemberResult:
    node_id: int
    node_nome: str
    status_geral: str
    grupos: list[TeamGroupSummary]


@dataclass(frozen=True)
class AccumulatedSalesResult:
    node_id: int
    node_nome: str
    cycle_id: int
    cycle_ano: int
    cycle_mes: int
    dias_uteis_restantes: int
    grupos: list[GroupResult]
    equipe: list[TeamMemberResult]


def _dias_uteis_restantes(ano: int, mes: int, hoje: date | None = None) -> int:
    """Dias úteis (segunda a sexta) entre hoje (inclusive) e o último dia do mês do ciclo. Fora do
    mês corrente (ciclo já fechado ou ainda não iniciado), não existe "ritmo diário" a mostrar.
    `hoje` é parametrizado só pra permitir teste determinístico — em uso real é sempre `date.
    today()`."""
    hoje = hoje or date.today()
    if (hoje.year, hoje.month) != (ano, mes):
        return 0
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return sum(1 for dia in range(hoje.day, ultimo_dia + 1) if date(ano, mes, dia).weekday() < 5)


def _dias_uteis_decorridos(ano: int, mes: int, hoje: date | None = None) -> int:
    """Dias úteis (segunda a sexta) do dia 1 até hoje (inclusive) — usado como base pra "tendência
    de faturamento" (média diária já realizada, projetada pro mês inteiro). Fora do mês corrente,
    0 (mesma convenção de `_dias_uteis_restantes`)."""
    hoje = hoje or date.today()
    if (hoje.year, hoje.month) != (ano, mes):
        return 0
    return sum(1 for dia in range(1, hoje.day + 1) if date(ano, mes, dia).weekday() < 5)


def _dias_uteis_total_mes(ano: int, mes: int) -> int:
    """Total de dias úteis (segunda a sexta) do mês inteiro, independente de "hoje" — o
    multiplicador da tendência de faturamento."""
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return sum(1 for dia in range(1, ultimo_dia + 1) if date(ano, mes, dia).weekday() < 5)


def _status_volume(pct: float | None) -> str:
    if pct is None:
        return "SEM_META"
    if pct >= TOLERANCIA_VOLUME:
        return "VERDE"
    if pct >= AMARELO_MINIMO:
        return "AMARELO"
    return "VERMELHO"


def _priority_key(row: SubgroupResult) -> tuple[int, float]:
    """Ordena a tabela de subgrupos por prioridade de ação: quem ainda não atingiu e está mais
    perto do alvo primeiro, quem já atingiu por último, vendas sem meta associada (fora do
    planejado) no meio — informativo, mas sem ação de "faltam X kg" fazendo sentido."""
    if row.atingiu is True:
        return (2, 0.0)
    if row.pct is None:
        return (1, 0.0)
    return (0, -row.pct)


def _effective_team_children(node: HierarchyNode) -> list[HierarchyNode]:
    """Filhos diretos de `node` pra fins da tabela "Equipe" — sempre um nível abaixo (Gerente->
    Coordenador, Coordenador->Supervisor, Supervisor->Vendedor), sem exceção: mesmo quando a mesma
    pessoa acumula o cargo de `node` e de um dos filhos (ex.: um Coordenador que também é
    Supervisor de um dos seus próprios Supervisores — Decisão O5), esse filho aparece como uma
    linha normal da equipe pra qualquer um que olhar (o próprio dono do cargo duplo ou um superior
    de fora, ex. o Gerente). Quem quer ver os Vendedores daquela posição duplicada usa o
    drill-down normal daquela linha — é o frontend, não este cálculo, que decide mostrar essa
    opção extra só pro dono do cargo duplo (revisão confirmada pelo usuário, 2026-09-17, ver
    docs/decisions.md)."""
    return list(HierarchyNode.objects.filter(parent_id=node.id, ativo=True).order_by("nome"))


def _leaf_ids(node_id: int) -> list[int]:
    return list(
        HierarchyNode.objects.filter(
            id__in=ScopeResolver.descendant_ids(node_id), level=HierarchyNode.Level.VENDEDOR
        ).values_list("id", flat=True)
    )


def _meta_by_subgroup(leaf_ids: list[int], cycle: Cycle, group_id: int) -> dict[int, int]:
    rows = (
        GoalAllocation.objects.filter(
            cycle=cycle,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            owner_node_id__in=leaf_ids,
            subgroup__group_id=group_id,
        )
        .values("subgroup_id")
        .annotate(total=Sum("quantity_kg"))
    )
    return {row["subgroup_id"]: row["total"] for row in rows}


def _build_group_result(
    node_id: int,
    cycle: Cycle,
    group: ProductGroup,
    dias_uteis_restantes: int,
    dias_uteis_decorridos: int,
    dias_uteis_total_mes: int,
) -> GroupResult:
    leaf_ids = _leaf_ids(node_id)
    meta_by_subgroup = _meta_by_subgroup(leaf_ids, cycle, group.id)
    meta_total = sum(meta_by_subgroup.values())

    history = SalesHistoryProvider.target_history(
        node_id, HISTORY_MONTHS + 1, (cycle.ano, cycle.mes), group_id=group.id
    )
    realizado_total = history[-1].quantity_kg
    same_month_last_year_kg = history[0].quantity_kg if len(history) == HISTORY_MONTHS + 1 else None
    previous_3 = history[-4:-1]
    last_3_months_avg_kg = (
        sum(point.quantity_kg for point in previous_3) / len(previous_3) if previous_3 else None
    )

    realizado_by_subgroup = SalesHistoryProvider.target_month_breakdown(
        node_id, cycle.ano, cycle.mes, group.id
    )

    subgroup_rows = []
    for subgroup in ProductSubgroup.objects.filter(group_id=group.id, ativo=True):
        meta_kg = meta_by_subgroup.get(subgroup.id, 0)
        realizado_kg = realizado_by_subgroup.get(subgroup.id, 0.0)
        if meta_kg <= 0 and realizado_kg <= 0:
            continue
        pct_sg = realizado_kg / meta_kg if meta_kg > 0 else None
        atingiu_sg = pct_sg >= TOLERANCIA_VOLUME if pct_sg is not None else None
        subgroup_rows.append(
            SubgroupResult(
                subgroup_id=subgroup.id,
                subgroup_nome=subgroup.nome,
                meta_kg=meta_kg,
                realizado_kg=realizado_kg,
                pct=pct_sg,
                atingiu=atingiu_sg,
            )
        )
    subgroup_rows.sort(key=_priority_key)

    subgrupos_com_meta = sum(1 for row in subgroup_rows if row.meta_kg > 0)
    subgrupos_atingidos = sum(1 for row in subgroup_rows if row.meta_kg > 0 and row.atingiu)
    pct_subgrupos = subgrupos_atingidos / subgrupos_com_meta if subgrupos_com_meta > 0 else None
    subgrupos_atingiu = pct_subgrupos >= SUBGRUPOS_MINIMO if pct_subgrupos is not None else None

    pct = realizado_total / meta_total if meta_total > 0 else None
    faltam_kg = int(max(meta_total - realizado_total, 0)) if meta_total > 0 else 0
    status = _status_volume(pct)

    if meta_total <= 0:
        ritmo_kg_dia_util = None
    elif faltam_kg <= 0:
        ritmo_kg_dia_util = 0.0
    elif dias_uteis_restantes <= 0:
        ritmo_kg_dia_util = None
    else:
        ritmo_kg_dia_util = faltam_kg / dias_uteis_restantes

    tendencia_kg = (
        (realizado_total / dias_uteis_decorridos) * dias_uteis_total_mes
        if dias_uteis_decorridos > 0
        else None
    )

    atingiu_grupo = None
    if meta_total > 0:
        atingiu_grupo = bool(pct is not None and pct >= TOLERANCIA_VOLUME and subgrupos_atingiu)

    return GroupResult(
        group_id=group.id,
        group_nome=group.nome,
        meta_kg=meta_total,
        realizado_kg=realizado_total,
        pct=pct,
        faltam_kg=faltam_kg,
        status=status,
        ritmo_kg_dia_util=ritmo_kg_dia_util,
        tendencia_kg=tendencia_kg,
        same_month_last_year_kg=same_month_last_year_kg,
        last_3_months_avg_kg=last_3_months_avg_kg,
        subgrupos_com_meta=subgrupos_com_meta,
        subgrupos_atingidos=subgrupos_atingidos,
        pct_subgrupos=pct_subgrupos,
        subgrupos_atingiu=subgrupos_atingiu,
        atingiu_grupo=atingiu_grupo,
        subgrupos=subgroup_rows,
    )


def _effective_group_risk(g: TeamGroupSummary) -> int:
    """Risco de um grupo pra fins do "status geral" da equipe: mesma regra de combinar Volume e
    Subgrupos usada no badge do card de grupo (`atingiu_grupo`) — bateu os dois é VERDE; bateu só
    o volume mas não os subgrupos não conta como "batida" plena (rebaixa pra AMARELO, mesmo corte
    de "precisa de atenção"); volume amarelo/vermelho já é o pior caso, subgrupos não muda isso."""
    if g.meta_kg <= 0:
        return _STATUS_RISCO["SEM_META"]
    if g.atingiu_grupo:
        return _STATUS_RISCO["VERDE"]
    if g.status == "VERDE":
        return _STATUS_RISCO["AMARELO"]
    return _STATUS_RISCO[g.status]


def _team_group_summary(full: GroupResult) -> TeamGroupSummary:
    return TeamGroupSummary(
        group_id=full.group_id,
        group_nome=full.group_nome,
        meta_kg=full.meta_kg,
        realizado_kg=full.realizado_kg,
        pct=full.pct,
        status=full.status,
        pct_subgrupos=full.pct_subgrupos,
        subgrupos_atingiu=full.subgrupos_atingiu,
        atingiu_grupo=full.atingiu_grupo,
    )


class AccumulatedSalesResultsService:
    """Monta o retrato Meta vs Realizado de um nó (consolidado de toda a sub-árvore de Vendedores
    abaixo dele) mais a tabela de equipe (um resumo por subordinado direto, pra navegação de
    drill-down) — nunca soma médias de percentuais entre subordinados, sempre recalcula sobre o
    total agregado, mesma filosofia do `fechamento_levo` (ver docstring do módulo)."""

    @staticmethod
    def build(node: HierarchyNode, cycle: Cycle) -> AccumulatedSalesResult:
        dias_uteis_restantes = _dias_uteis_restantes(cycle.ano, cycle.mes)
        dias_uteis_decorridos = _dias_uteis_decorridos(cycle.ano, cycle.mes)
        dias_uteis_total_mes = _dias_uteis_total_mes(cycle.ano, cycle.mes)
        groups = list(ProductGroup.objects.filter(ativo=True).order_by("nome"))

        grupos = [
            _build_group_result(
                node.id, cycle, group, dias_uteis_restantes, dias_uteis_decorridos, dias_uteis_total_mes
            )
            for group in groups
        ]

        equipe = []
        for child in _effective_team_children(node):
            team_groups = [
                _team_group_summary(
                    _build_group_result(
                        child.id,
                        cycle,
                        group,
                        dias_uteis_restantes=0,
                        dias_uteis_decorridos=dias_uteis_decorridos,
                        dias_uteis_total_mes=dias_uteis_total_mes,
                    )
                )
                for group in groups
            ]
            worst_rank = min(
                (_effective_group_risk(g) for g in team_groups), default=_STATUS_RISCO["SEM_META"]
            )
            status_geral = next(nome for nome, rank in _STATUS_RISCO.items() if rank == worst_rank)
            equipe.append(
                TeamMemberResult(
                    node_id=child.id, node_nome=child.nome, status_geral=status_geral, grupos=team_groups
                )
            )
        equipe.sort(key=lambda member: _STATUS_RISCO[member.status_geral])

        return AccumulatedSalesResult(
            node_id=node.id,
            node_nome=node.nome,
            cycle_id=cycle.id,
            cycle_ano=cycle.ano,
            cycle_mes=cycle.mes,
            dias_uteis_restantes=dias_uteis_restantes,
            grupos=grupos,
            equipe=equipe,
        )
