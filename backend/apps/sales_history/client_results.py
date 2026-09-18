"""Tela "Acompanhamento > Acumulado de Clientes": foco no cliente, não no grupo/meta — captação e
positivação (os dois KPIs que a empresa usa pra pagamento, confirmados com o usuário em
2026-09-17) mais o resumo da carteira e a lista de clientes sem compra no ciclo.

Escopo por carteira ATUAL (`ClientPortfolioSnapshot`), não por quem historicamente vendeu — mesma
filosofia de `DistributionBaselineService.rebuild` ("importa quanto o cliente comprou pra quem hoje
é responsável por ele"): um cliente que mudou de vendedor conta inteiro para o dono atual, mesmo
que parte do histórico tenha sido vendida por outra pessoa.

Fórmulas confirmadas pelo usuário (2026-09-17):
- **Positivação**: nº de clientes que compraram no ciclo (distinto, via `AccumulatedSale`) dividido
  pelo nº de clientes distintos da CARTEIRA (não de `AccumulatedSale`) com
  `ClientPortfolioSnapshot.last_changed_at` ACUMULADO até o fim do mês anterior (`<=` último dia do
  mês anterior, não uma janela fechada só daquele mês — revisão 2026-09-17, quarta volta: a janela
  estreita de um único mês subestimava demais a base, deixando a positivação passar de 1000%). O
  numerador continua vindo do histórico de vendas do próprio mês do ciclo (isso o usuário confirmou
  que já estava certo).
- **Captação**: nº de clientes DISTINTOS da carteira cuja data de ÚLTIMA ALTERAÇÃO cadastral no
  ERP (`ClientPortfolioSnapshot.last_changed_at`) cai dentro do mês do ciclo (revisão 2026-09-17,
  segunda volta: `registered_at`/"Cadastro" não bastava — o próprio usuário confirmou que
  `last_changed_at`/"Alterado" é quem realmente reflete cliente novo entrando na carteira filtrada,
  não só `registered_at`). Não é inferida a partir da primeira venda em `AccumulatedSale` (essa
  forma sofria da limitação de só enxergar os últimos ~12 meses sincronizados).
- **Ticket médio**: em KG (`total_quantity`), não em R$ (revisão 2026-09-17: era `total_value`
  antes, o usuário pediu peso) — soma do peso vendido no ciclo pros clientes ativos, dividido pela
  quantidade de clientes ativos.
- **Ticket médio por volume** (2026-09-18): mesma fórmula do ticket médio acima, só que calculada
  separadamente por `ProductGroup` (hoje FRANGOS e REVENDA) — peso vendido no ciclo dentro daquele
  grupo dividido pelos clientes que compraram algo daquele grupo no ciclo (não pelo total de
  clientes ativos geral). Mapeamento `subgroup_name` (ERP) -> grupo via `ExternalProductMapping`,
  mesmo padrão de `HierarchyResultsService.summary` (`sales_history/services.py`).

Metas de pagamento (confirmadas pelo usuário, 2026-09-18) — só Captação e Positivação têm meta;
os outros cards desta tela continuam informativos, sem meta:
- **Meta de Positivação**: fixa em `POSITIVACAO_META_PCT` (65%), igual pra qualquer nó/nível.
- **Meta de Captação**: 2 clientes por Vendedor ATIVO no escopo do nó (`CAPTACAO_META_POR_VENDEDOR`).
  Sobe a hierarquia por soma simples (Supervisor = 2 × nº de vendedores dele; Coordenador Local =
  soma dos supervisores; Gerente = soma dos coordenadores) — na prática isso equivale a contar todo
  Vendedor ativo na subárvore do nó (via `ScopeResolver.descendant_ids`, que já é inclusive e cobre
  os 4 níveis) e multiplicar por 2, sem precisar de uma fórmula por nível: a soma bottom-up e a
  contagem direta dão o mesmo número.

Metas em número absoluto pros cards "Base Clientes" e "Clientes ativos" (confirmado pelo usuário,
2026-09-18, segunda revisão do mesmo dia) — não são metas novas, são as mesmas metas de
Captação/Positivação acima só que expressas como número absoluto sobre a carteira acumulada até o
mês anterior (`clientes_ativos_mes_anterior`), pra esses dois cards indicarem visualmente "o
caminho" até bater as duas metas de pagamento, em vez de só descrever o que já é o valor atual:
- **Meta de Base Clientes**: `clientes_ativos_mes_anterior + captacao_meta` — a carteira toda
  (ativa ou não) precisa crescer pelo menos `captacao_meta` clientes sobre a base do mês anterior.
- **Meta de Clientes ativos**: `clientes_ativos_mes_anterior × POSITIVACAO_META_PCT`, arredondado
  (>= 0,5 sobe, mesma convenção do resto do produto) — é a mesma meta de 65% de Positivação, só que
  em nº de clientes em vez de percentual.

Clientes sem compra — agrupado na tela, desagrupado na exportação (pedido do usuário, 2026-09-18):
`ClientInactiveRow.itens_ultima_compra` carrega o detalhe por subgrupo (nome + peso) da última
compra de cada cliente. A tela (`ClientAccumuladoView`) usa isso pra uma seta expansível, mantendo
uma linha por cliente; o CSV de exportação (`ClientesSemCompraExportView`) usa a mesma lista pra
gerar uma linha por item (cliente × subgrupo) — mesma fonte de dado, duas representações.

Filtro por mês (revisão 2026-09-17, quinta volta): esta tela NÃO usa `Cycle` (o ciclo de metas) —
recebe `ano`/`mes` direto, sem depender de existir um ciclo de meta cadastrado pra aquele mês. Ela
não compara com meta nenhuma, então limitar o filtro aos meses que têm `Cycle` (criados só quando
alguém abre um ciclo de distribuição) escondia meses com dado de venda real já sincronizado.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from apps.catalog.models import ExternalProductMapping, ProductGroup
from apps.hierarchy.models import ExternalSalespersonMapping, HierarchyNode
from apps.hierarchy.services import ScopeResolver

from .models import AccumulatedSale, ClientPortfolioSnapshot

POSITIVACAO_META_PCT = 0.65
CAPTACAO_META_POR_VENDEDOR = 2


@dataclass(frozen=True)
class ClientInactivePurchaseItem:
    subgroup_name: str
    peso_kg: float


@dataclass(frozen=True)
class ClientInactiveRow:
    client_code: int
    client_name: str
    ultima_compra_ano: int | None
    ultima_compra_mes: int | None
    itens_ultima_compra: list[ClientInactivePurchaseItem]
    peso_ultima_compra_kg: float


@dataclass(frozen=True)
class ClientGroupTicketMedio:
    grupo_id: int
    grupo_nome: str
    clientes_ativos: int
    ticket_medio_kg: float | None


@dataclass(frozen=True)
class ClientAccumuladoResult:
    node_id: int
    node_nome: str
    ano: int
    mes: int
    carteira_total: int
    clientes_ativos: int
    clientes_ativos_mes_anterior: int
    positivacao_pct: float | None
    positivacao_meta_pct: float
    captacao: int
    captacao_meta: int
    base_clientes_meta: int
    clientes_ativos_meta: int
    clientes_sem_compra_count: int
    ticket_medio_kg: float | None
    ticket_medio_por_grupo: list[ClientGroupTicketMedio] = field(default_factory=list)
    clientes_sem_compra: list[ClientInactiveRow] = field(default_factory=list)


def _month_bounds(ano: int, mes: int) -> tuple[date, date]:
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, 1), date(ano, mes, ultimo_dia)


def _previous_month(ano: int, mes: int) -> tuple[int, int]:
    return (ano - 1, 12) if mes == 1 else (ano, mes - 1)


def _salesperson_names_for(node_id: int) -> list[str]:
    vendedor_ids = HierarchyNode.objects.filter(
        id__in=ScopeResolver.descendant_ids(node_id), level=HierarchyNode.Level.VENDEDOR
    ).values_list("id", flat=True)
    return list(
        ExternalSalespersonMapping.objects.filter(hierarchy_node_id__in=vendedor_ids).values_list(
            "external_name", flat=True
        )
    )


def _build_inactive_rows(
    inactive_codes: set[int], client_name_by_code: dict[int, str]
) -> list[ClientInactiveRow]:
    """Última venda de cada cliente sem compra no ciclo, olhando TODO o histórico sincronizado
    (não só o escopo do nó) — quem vendeu historicamente não importa aqui, só "quando" e "o quê".

    Traz o detalhe por subgrupo (`itens_ultima_compra`), não só os nomes: usado tanto pra seta de
    expandir da tela (agrupada por cliente, uma linha só) quanto pro CSV de exportação (desagrupado
    por subgrupo, uma linha por item) — pedido do usuário, 2026-09-18."""
    history_by_code: dict[int, list[tuple[date, str, Decimal]]] = defaultdict(list)
    rows = AccumulatedSale.objects.filter(client_code__in=inactive_codes).values_list(
        "client_code", "sale_date", "subgroup_name", "total_quantity"
    )
    for client_code, sale_date, subgroup_name, quantity in rows:
        history_by_code[client_code].append((sale_date, subgroup_name, quantity))

    result = []
    for code in inactive_codes:
        entries = history_by_code.get(code, [])
        if not entries:
            result.append(
                ClientInactiveRow(
                    client_code=code,
                    client_name=client_name_by_code[code],
                    ultima_compra_ano=None,
                    ultima_compra_mes=None,
                    itens_ultima_compra=[],
                    peso_ultima_compra_kg=0.0,
                )
            )
            continue

        ultima_data = max(sale_date for sale_date, _, _ in entries)
        # Soma por subgrupo (não uma linha por entrada bruta): `AccumulatedSale` pode ter mais de
        # uma linha pro mesmo subgrupo/data por causa do agrupamento por empresa da venda (ver
        # docstring do model, "8 colunas, não só vendedor+cliente+subgrupo+data").
        peso_by_subgroup: dict[str, Decimal] = defaultdict(Decimal)
        for sale_date, subgroup, qty in entries:
            if sale_date == ultima_data:
                peso_by_subgroup[subgroup] += qty

        itens_ultima_compra = [
            ClientInactivePurchaseItem(subgroup_name=subgroup, peso_kg=float(peso))
            for subgroup, peso in sorted(peso_by_subgroup.items())
        ]
        result.append(
            ClientInactiveRow(
                client_code=code,
                client_name=client_name_by_code[code],
                ultima_compra_ano=ultima_data.year,
                ultima_compra_mes=ultima_data.month,
                itens_ultima_compra=itens_ultima_compra,
                peso_ultima_compra_kg=sum(item.peso_kg for item in itens_ultima_compra),
            )
        )

    # Maior peso da última compra primeiro: quem tinha mais volume é quem representa mais
    # faturamento em risco de não retomar — critério de priorização, não fórmula de pagamento.
    result.sort(key=lambda row: row.peso_ultima_compra_kg, reverse=True)
    return result


def _ticket_medio_por_grupo(
    clientes_ativos: set[int], cur_start: date, cur_end: date
) -> list[ClientGroupTicketMedio]:
    """Mesma fórmula do ticket médio geral (peso ÷ clientes ativos), mas separada por
    `ProductGroup` — o denominador de cada grupo é só quem comprou algo daquele grupo, não o total
    geral de ativos."""
    code_to_group_id: dict[str, int] = {}
    for mapping in ExternalProductMapping.objects.select_related("group", "subgroup__group"):
        group = mapping.group or (mapping.subgroup.group if mapping.subgroup_id else None)
        if group is not None:
            code_to_group_id[mapping.external_code] = group.id

    grupos = list(ProductGroup.objects.filter(ativo=True).order_by("nome").values("id", "nome"))

    peso_by_group: dict[int, Decimal] = defaultdict(Decimal)
    clientes_by_group: dict[int, set[int]] = defaultdict(set)
    rows = AccumulatedSale.objects.filter(
        client_code__in=clientes_ativos, sale_date__gte=cur_start, sale_date__lte=cur_end
    ).values_list("client_code", "subgroup_name", "total_quantity")
    for client_code, subgroup_name, quantity in rows:
        group_id = code_to_group_id.get(subgroup_name)
        if group_id is None:
            continue
        peso_by_group[group_id] += quantity
        clientes_by_group[group_id].add(client_code)

    return [
        ClientGroupTicketMedio(
            grupo_id=grupo["id"],
            grupo_nome=grupo["nome"],
            clientes_ativos=len(clientes_by_group.get(grupo["id"], set())),
            ticket_medio_kg=(
                float(peso_by_group[grupo["id"]]) / len(clientes_by_group[grupo["id"]])
                if clientes_by_group.get(grupo["id"])
                else None
            ),
        )
        for grupo in grupos
    ]


class ClientResultsService:
    """Monta o retrato de carteira/captação/positivação de um nó (consolidado de toda a sub-árvore
    de Vendedores abaixo dele), usando a carteira ATUAL como universo de clientes."""

    @staticmethod
    def build(node: HierarchyNode, ano: int, mes: int) -> ClientAccumuladoResult:
        salesperson_names = _salesperson_names_for(node.id)

        vendedores_ativos_count = HierarchyNode.objects.filter(
            id__in=ScopeResolver.descendant_ids(node.id), level=HierarchyNode.Level.VENDEDOR, ativo=True
        ).count()
        captacao_meta = CAPTACAO_META_POR_VENDEDOR * vendedores_ativos_count

        carteira = list(
            ClientPortfolioSnapshot.objects.filter(salesperson_name__in=salesperson_names).values_list(
                "client_code", "client_name", "last_changed_at"
            )
        )
        client_name_by_code = {code: name for code, name, _ in carteira}
        carteira_codes = set(client_name_by_code.keys())

        cur_start, cur_end = _month_bounds(ano, mes)
        prev_ano, prev_mes = _previous_month(ano, mes)
        _, prev_end = _month_bounds(prev_ano, prev_mes)

        # Conjunto (não soma linha a linha) pra garantir contagem distinta por client_code, mesmo
        # que a fonte um dia deixe de ter unicidade garantida por `client_code` (pedido explícito
        # do usuário, 2026-09-17).
        captacao_codes = {
            code
            for code, _, last_changed_at in carteira
            if last_changed_at is not None and cur_start <= last_changed_at <= cur_end
        }
        captacao = len(captacao_codes)

        clientes_ativos = set(
            AccumulatedSale.objects.filter(
                client_code__in=carteira_codes, sale_date__gte=cur_start, sale_date__lte=cur_end
            ).values_list("client_code", flat=True)
        )
        # Denominador da positivação ("mês anterior"): vem da CARTEIRA (last_changed_at), não de
        # AccumulatedSale — pedido explícito do usuário (2026-09-17), diferente do numerador.
        # Não é uma janela fechada só de fevereiro: é ACUMULADO até o fim do mês anterior (qualquer
        # `last_changed_at` <= último dia do mês anterior) — o usuário corrigiu explicitamente que
        # restringir à janela estreita de um único mês subestimava demais a base (só ~460 clientes
        # contra 5 mil ativos no mês, positivação virava >1000%).
        clientes_ativos_mes_anterior = len(
            {
                code
                for code, _, last_changed_at in carteira
                if last_changed_at is not None and last_changed_at <= prev_end
            }
        )

        positivacao_pct = (
            len(clientes_ativos) / clientes_ativos_mes_anterior if clientes_ativos_mes_anterior > 0 else None
        )

        base_clientes_meta = clientes_ativos_mes_anterior + captacao_meta
        clientes_ativos_meta = int(
            (Decimal(clientes_ativos_mes_anterior) * Decimal(str(POSITIVACAO_META_PCT))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

        peso_total = AccumulatedSale.objects.filter(
            client_code__in=clientes_ativos, sale_date__gte=cur_start, sale_date__lte=cur_end
        ).aggregate(total=Sum("total_quantity"))["total"] or Decimal("0")
        ticket_medio_kg = float(peso_total) / len(clientes_ativos) if clientes_ativos else None

        ticket_medio_por_grupo = _ticket_medio_por_grupo(clientes_ativos, cur_start, cur_end)

        clientes_sem_compra_codes = carteira_codes - clientes_ativos
        clientes_sem_compra = _build_inactive_rows(clientes_sem_compra_codes, client_name_by_code)

        return ClientAccumuladoResult(
            node_id=node.id,
            node_nome=node.nome,
            ano=ano,
            mes=mes,
            carteira_total=len(carteira_codes),
            clientes_ativos=len(clientes_ativos),
            clientes_ativos_mes_anterior=clientes_ativos_mes_anterior,
            positivacao_pct=positivacao_pct,
            positivacao_meta_pct=POSITIVACAO_META_PCT,
            captacao=captacao,
            captacao_meta=captacao_meta,
            base_clientes_meta=base_clientes_meta,
            clientes_ativos_meta=clientes_ativos_meta,
            clientes_sem_compra_count=len(clientes_sem_compra_codes),
            ticket_medio_kg=ticket_medio_kg,
            ticket_medio_por_grupo=ticket_medio_por_grupo,
            clientes_sem_compra=clientes_sem_compra,
        )
