from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q

from apps.audit.models import AuditLogEntry
from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import FeristaCoverage, HierarchyNode
from apps.sales_history.provider import SalesHistoryProvider

from .models import GoalAllocation
from .strategies import (
    GroupSuggestion,
    LargestRemainderRoundingPolicy,
    MonthlyQuantity,
    RecentAverageDistributionStrategy,
    SeasonalTrendSuggestionStrategy,
    StrategyNotConfiguredError,
    default_distribution_registry,
)


class AllocationClosureError(ValidationError):
    """Falha na invariante de fechamento: soma das filhas não bate com o pai."""


class AllocationScopeError(ValidationError):
    """Falha de isolamento de escopo: só se distribui o que se possui, para filhos diretos."""


class AllocationReopenError(ValidationError):
    """Falha ao reabrir: alocação ainda não distribuída, ou ciclo já não está aberto."""


@dataclass(frozen=True)
class ChildAllocationSpec:
    owner_node_id: int
    quantity_kg: int
    granularity: str
    group_id: int | None = None
    subgroup_id: int | None = None
    product_id: int | None = None


class ClosureValidator:
    """Garante o fechamento exato, independente de qual estratégia gerou os valores."""

    @staticmethod
    def validate(parent_quantity_kg: int, children_quantities_kg: list[int]) -> None:
        for qty in children_quantities_kg:
            if not isinstance(qty, int) or isinstance(qty, bool):
                raise AllocationClosureError(f"Quantidade {qty!r} não é um inteiro.")
            if qty < 0:
                raise AllocationClosureError(f"Quantidade {qty} kg é negativa.")

        total = sum(children_quantities_kg)
        if total != parent_quantity_kg:
            raise AllocationClosureError(
                f"Soma das filhas ({total} kg) não fecha com o pai ({parent_quantity_kg} kg)."
            )


class DistributeGoalService:
    """Repassa uma GoalAllocation às filhas numa única transação, validada pelo ClosureValidator."""

    @staticmethod
    @transaction.atomic
    def distribute(
        parent: GoalAllocation, children: list[ChildAllocationSpec], criado_por
    ) -> list[GoalAllocation]:
        if not criado_por.hierarchy_nodes.filter(id=parent.owner_node_id).exists():
            raise AllocationScopeError("Você só pode distribuir uma alocação que possui.")

        created = DistributeGoalService._distribute_unchecked(parent, children, criado_por)

        # Autogestão (pedido do usuário, 2026-08-07): se algum filho recém-criado é dono de um
        # Supervisor cujo único Vendedor ativo é ele mesmo (mesmo nome), não existe decisão real
        # de repasse — fecha sozinho, sem passar pela tela "Meta Vendedor".
        for allocation in created:
            SelfVendedorAutoDistributionService.cascade_if_eligible(allocation, criado_por)

        return created

    @staticmethod
    def _distribute_unchecked(
        parent: GoalAllocation, children: list[ChildAllocationSpec], criado_por
    ) -> list[GoalAllocation]:
        """Núcleo do repasse, sem a checagem de posse — usada tanto por `distribute()` (repasse
        manual, a checagem já rodou antes de chamar) quanto por
        `SelfVendedorAutoDistributionService` (repasse automático Supervisor->Vendedor, disparado
        por quem distribuiu pro Supervisor e não ocupa o nó dele). Sempre chamada de dentro de uma
        transação já aberta por quem invoca — não tem `@transaction.atomic` próprio."""
        if parent.distributed:
            raise AllocationClosureError("Esta alocação já foi distribuída.")

        parent_id_by_node_id = dict(
            HierarchyNode.objects.filter(id__in=[child.owner_node_id for child in children]).values_list(
                "id", "parent_id"
            )
        )
        for child in children:
            if parent_id_by_node_id.get(child.owner_node_id) != parent.owner_node_id:
                raise AllocationScopeError(
                    f"O nó {child.owner_node_id} não é filho direto de quem está distribuindo."
                )

        ClosureValidator.validate(parent.quantity_kg, [child.quantity_kg for child in children])

        created = [
            GoalAllocation(
                cycle=parent.cycle,
                owner_node_id=child.owner_node_id,
                parent_allocation=parent,
                granularity=child.granularity,
                group_id=child.group_id,
                subgroup_id=child.subgroup_id,
                product_id=child.product_id,
                quantity_kg=child.quantity_kg,
                criado_por=criado_por,
            )
            for child in children
        ]
        for allocation in created:
            allocation.full_clean()
            allocation.save()

        parent.distributed = True
        parent.save(update_fields=["distributed", "updated_at"])

        return created


class SelfVendedorAutoDistributionService:
    """Quando um Supervisor tem exatamente um Vendedor ativo sob ele, com o mesmo nome (o padrão
    de autogestão já visto no sistema — a mesma pessoa ocupa as duas posições, ex.: Fabiano/
    Rafael, que além de coordenar/supervisionar também têm carteira própria), repassar pra ele
    nunca é uma decisão real: só existe um alvo possível, e a quantidade é sempre 100% do que o
    Supervisor recebeu. Pedido explícito do usuário (2026-08-07): a alocação do Vendedor é criada
    e fechada automaticamente no mesmo instante em que o Coordenador distribui pro Supervisor —
    sem passar pela tela "Meta Vendedor" pra esse caso específico (diferente de P1-P4, que só
    pré-preenchem e sempre exigem confirmação humana — aqui não existe escolha a confirmar).

    Só entra em cena logo depois de `DistributeGoalService` criar uma alocação cujo dono é
    SUPERVISOR — chamada de dentro da mesma transação de `distribute()`, por isso usa
    `_distribute_unchecked` (quem disparou o repasse pro Supervisor é o Coordenador, que não
    ocupa o nó do Supervisor — a checagem de posse normal sempre rejeitaria).
    """

    @staticmethod
    def is_self_managed_supervisor(node: HierarchyNode) -> bool:
        """Regra de elegibilidade única (usada aqui e por `GoalAllocationViewSet.get_queryset` pra
        excluir esses repasses de `has_further_distribution`, ver revisão 2026-09-03 abaixo): nó
        SUPERVISOR com exatamente 1 Vendedor ativo sob ele, mesmo nome."""
        if node.level != HierarchyNode.Level.SUPERVISOR:
            return False
        vendedores = list(node.children.filter(level=HierarchyNode.Level.VENDEDOR, ativo=True))
        if len(vendedores) != 1:
            return False
        return vendedores[0].nome.strip().lower() == node.nome.strip().lower()

    @staticmethod
    def self_managed_supervisor_ids() -> set[int]:
        """Mesma regra de `is_self_managed_supervisor`, em lote — usada por
        `GoalAllocationViewSet.get_queryset` pra excluir repasses de autogestão de
        `has_further_distribution` (revisão 2026-09-03): não é decisão real de ninguém — só existe
        1 alvo possível —, então resetar e redistribuir de novo não perde trabalho nenhum, seja
        qual for a quantidade. 2 queries (candidatos + prefetch dos vendedores), não N+1."""
        candidates = (
            HierarchyNode.objects.filter(level=HierarchyNode.Level.SUPERVISOR, ativo=True)
            .annotate(
                active_vendedor_count=Count(
                    "children",
                    filter=Q(children__level=HierarchyNode.Level.VENDEDOR, children__ativo=True),
                )
            )
            .filter(active_vendedor_count=1)
            .prefetch_related(
                Prefetch(
                    "children",
                    queryset=HierarchyNode.objects.filter(level=HierarchyNode.Level.VENDEDOR, ativo=True),
                    to_attr="active_vendedores",
                )
            )
        )
        return {
            node.id
            for node in candidates
            if node.active_vendedores[0].nome.strip().lower() == node.nome.strip().lower()
        }

    @staticmethod
    def cascade_if_eligible(allocation: GoalAllocation, criado_por) -> GoalAllocation | None:
        owner_node = allocation.owner_node
        if not SelfVendedorAutoDistributionService.is_self_managed_supervisor(owner_node):
            return None

        vendedor = owner_node.children.get(level=HierarchyNode.Level.VENDEDOR, ativo=True)
        child = ChildAllocationSpec(
            owner_node_id=vendedor.id,
            quantity_kg=allocation.quantity_kg,
            granularity=allocation.granularity,
            group_id=allocation.group_id,
            subgroup_id=allocation.subgroup_id,
            product_id=allocation.product_id,
        )
        return DistributeGoalService._distribute_unchecked(allocation, [child], criado_por)[0]


def _collect_descendants(allocation: GoalAllocation) -> list[GoalAllocation]:
    """Sub-árvore inteira de GoalAllocation abaixo de `allocation` (não inclui ela mesma), em
    ordem de nível (a última posição da lista contém as folhas mais profundas)."""
    descendants: list[GoalAllocation] = []
    frontier = [allocation]
    while frontier:
        children = list(GoalAllocation.objects.filter(parent_allocation__in=frontier))
        descendants.extend(children)
        frontier = children
    return descendants


class ReopenAllocationService:
    """Reabre uma alocação já distribuída (H4): invalida em cascata toda a sub-árvore de filhas,
    apagando-as e registrando o evento no AuditLogEntry, e devolve esta alocação a
    distributed=False. Depois de reaberta, DistributeGoalService.distribute() (sem nenhuma
    mudança) pode ser chamado de novo para refazer o repasse.

    Escopo mínimo (H4): só o ramo desta alocação para baixo é invalidado — a alocação-pai e
    irmãos não tocados permanecem válidos.
    """

    @staticmethod
    @transaction.atomic
    def reopen(allocation: GoalAllocation, criado_por) -> GoalAllocation:
        """Reabertura voluntária: só quem possui a alocação pode reabri-la (H4).

        Trava adicional (pedido do usuário, 2026-08-04) que `reopen_for_hierarchy_change` (O4)
        não tem: se algum filho direto já repassou adiante o que recebeu, com trabalho real por
        trás, bloqueia — reabrir aqui apagaria em cascata um trabalho que já avançou pra baixo,
        sem quem fez esse trabalho saber. Uma alocação com `distributed=False` nunca tem filhas
        (só `DistributeGoalService.distribute()` cria filhas, e ele sempre marca `distributed=
        True` no pai na mesma transação) — checar só os filhos diretos já garante que não existe
        nada mais fundo pendente de aviso. Destrava de baixo pra cima: cada filho bloqueado
        precisa resetar a distribuição dele primeiro (mesma regra, aplicada no nível dele) — só
        depois que nenhum filho direto estiver mais `distributed=True` este reset libera.

        "Trabalho real por trás" usa exatamente a mesma regra de `has_further_distribution`
        (`GoalAllocationViewSet`/`GoalAllocationSerializer`, revisão 2026-09-03) — um filho com
        0 kg, ou um repasse automático de autogestão (`SelfVendedorAutoDistributionService`, único
        alvo possível, mesmo com quantidade > 0) não bloqueiam. As duas checagens precisam ficar em
        sincronia: se o frontend mostra o botão (porque `has_further_distribution` deu `false`),
        este método tem que aceitar o reset — senão o botão aparece e a ação falha.
        """
        if not criado_por.hierarchy_nodes.filter(id=allocation.owner_node_id).exists():
            raise AllocationScopeError("Você só pode reabrir uma alocação que possui.")

        # As duas checagens básicas (já distribuída, ciclo aberto) também vivem em
        # `_reopen_unchecked` — repetidas aqui só pra ter precedência sobre a trava de filhos
        # abaixo (senão "ciclo já fechado" apareceria mascarado como "tem filho bloqueando").
        ReopenAllocationService._ensure_reopenable(allocation)

        blocking_names = ReopenAllocationService._blocking_children_names(allocation)
        if blocking_names:
            raise AllocationReopenError(
                f"Não é possível resetar: {', '.join(blocking_names)} já distribuiu a parte "
                "recebida adiante. Peça para essa pessoa resetar a distribuição dela primeiro — "
                "depois disso este reset libera."
            )

        return ReopenAllocationService._reopen_unchecked(allocation, changed_by=criado_por, motivo=None)

    @staticmethod
    def _blocking_children_names(allocation: GoalAllocation) -> list[str]:
        """Nomes dos filhos diretos que travam o reset de `allocation` — mesma regra de
        `has_further_distribution` (revisão 2026-09-03): um filho com 0 kg, ou um repasse
        automático de autogestão (`SelfVendedorAutoDistributionService`, único alvo possível,
        mesmo com quantidade > 0), não bloqueia. Compartilhado por `reopen()` e `reopen_group()` —
        as duas precisam ficar em sincronia com `has_further_distribution`
        (`GoalAllocationViewSet`/`GoalAllocationSerializer`): se o frontend mostra o botão, o
        backend tem que aceitar o reset."""
        self_managed_ids = SelfVendedorAutoDistributionService.self_managed_supervisor_ids()
        blocking = (
            allocation.children.filter(distributed=True, quantity_kg__gt=0)
            .exclude(owner_node_id__in=self_managed_ids)
            .select_related("owner_node")
        )
        return [child.owner_node.nome for child in blocking]

    @staticmethod
    @transaction.atomic
    def reopen_group(
        *, owner_node: HierarchyNode, cycle: Cycle, group_id: int, criado_por
    ) -> list[GoalAllocation]:
        """Reseta de uma vez só TODAS as alocações SUBGROUP já distribuídas de um grupo, que
        `owner_node` possui neste ciclo — pedido explícito do usuário (2026-09-03): as telas "Meta
        Supervisor"/"Meta Vendedor" (`SubgroupCascadeWorkspace`, frontend) resetam subgrupo por
        subgrupo via `reopen()`, o que é tedioso quando o nível errou a distribuição do grupo
        inteiro (um grupo pode ter dezenas de subgrupos). Tudo-ou-nada (confirmado com o usuário):
        se qualquer subgrupo tiver um filho bloqueando (mesma regra de `reopen()` — trabalho real,
        não autogestão nem 0 kg), a operação inteira falha listando todos os bloqueios de uma vez,
        sem resetar nada — evita um grupo pela metade resetado, difícil de auditar.

        Depois de resetado, cada subgrupo volta a `distributed=False`, pronto pra uma nova
        sugestão automática pré-preencher os campos no frontend — a persistência de fato continua
        exigindo o mesmo `distribute()`/"Salvar distribuição" de sempre (sugestão nunca se
        auto-aplica, ver [[project_goal-suggestion-workflow]])."""
        if not criado_por.hierarchy_nodes.filter(id=owner_node.id).exists():
            raise AllocationScopeError("Você só pode resetar alocações que possui.")

        allocations = list(
            GoalAllocation.objects.filter(
                owner_node=owner_node, cycle=cycle, subgroup__group_id=group_id, distributed=True
            )
        )
        if not allocations:
            raise AllocationReopenError("Nenhuma distribuição para resetar nesse grupo.")

        for allocation in allocations:
            ReopenAllocationService._ensure_reopenable(allocation)

        blocking_names: set[str] = set()
        for allocation in allocations:
            blocking_names.update(ReopenAllocationService._blocking_children_names(allocation))

        if blocking_names:
            raise AllocationReopenError(
                f"Não é possível resetar o grupo: {', '.join(sorted(blocking_names))} já "
                "distribuiu a parte recebida adiante. Peça pra essas pessoas resetarem a "
                "distribuição delas primeiro — depois disso este reset libera."
            )

        for allocation in allocations:
            ReopenAllocationService._reopen_unchecked(allocation, changed_by=criado_por, motivo=None)

        return allocations

    @staticmethod
    @transaction.atomic
    def reopen_for_hierarchy_change(
        allocation: GoalAllocation, changed_by, affected_node: HierarchyNode
    ) -> GoalAllocation:
        """Reabertura automática (O4): disparada quando um nó da hierarquia é desativado ou
        reparentado enquanto tem meta em ciclo aberto — não é o dono da alocação pedindo, por
        isso não passa pela checagem de posse de `reopen()`. `allocation` aqui é a alocação-PAI
        (quem distribuiu para o nó afetado), não a alocação do próprio nó afetado.
        """
        return ReopenAllocationService._reopen_unchecked(
            allocation,
            changed_by=changed_by,
            motivo={"motivo": "mudanca_hierarquia", "no_afetado_id": affected_node.id},
        )

    @staticmethod
    def _ensure_reopenable(allocation: GoalAllocation) -> None:
        if not allocation.distributed:
            raise AllocationReopenError("Esta alocação ainda não foi distribuída — nada para reabrir.")

        if allocation.cycle.status != Cycle.Status.ABERTO:
            raise AllocationReopenError("Só é possível reabrir alocações de um ciclo aberto.")

    @staticmethod
    def _reopen_unchecked(allocation: GoalAllocation, changed_by, motivo: dict | None) -> GoalAllocation:
        ReopenAllocationService._ensure_reopenable(allocation)

        descendants = _collect_descendants(allocation)

        changes = {
            "distributed": {"de": True, "para": False},
            "filhas_invalidadas": [
                {
                    "id": child.id,
                    "owner_node_id": child.owner_node_id,
                    "quantity_kg": child.quantity_kg,
                }
                for child in descendants
            ],
        }
        if motivo:
            changes.update(motivo)

        AuditLogEntry.objects.create(
            content_object=allocation,
            action=AuditLogEntry.Action.REABERTURA,
            changes=changes,
            changed_by=changed_by,
        )

        # Apaga da folha mais profunda para cima, senão on_delete=PROTECT em parent_allocation barra.
        for child in reversed(descendants):
            child.delete()

        allocation.distributed = False
        allocation.save(update_fields=["distributed", "updated_at"])

        return allocation


class HierarchyChangeReassignmentService:
    """O4: quando um nó da hierarquia é desativado (ativo=False) ou reparentado (parent mudou)
    enquanto tem meta em ciclo aberto, a alocação-PAI (quem tinha distribuído para esse nó) é
    reaberta em cascata — a meta "volta pro nó pai", que precisa redistribuir considerando a
    mudança (o nó afetado deixa de ser um alvo válido). Não decide para onde a meta vai depois
    disso — só garante que ela nunca fica presa/órfã num nó que saiu da estrutura ativa.

    Não cobre a alocação do PRÓPRIO nó afetado isoladamente — reabrir a alocação-pai já invalida
    em cascata toda a sub-árvore abaixo dela, incluindo a alocação do nó afetado e a de seus
    irmãos (o `distributed` do pai é tudo-ou-nada; não dá pra "devolver" só a fatia de um filho
    sem redistribuir o total de novo).
    """

    @staticmethod
    def detect_and_reassign_if_needed(
        previous: HierarchyNode | None, node: HierarchyNode, changed_by
    ) -> list[GoalAllocation]:
        """Compara o estado anterior (buscado do banco antes de salvar) com o novo e dispara a
        reatribuição só na transição real (ativo True->False, ou parent_id mudou) — criação de nó
        novo ou edição de outros campos não dispara nada. Chamado tanto pelo Django Admin
        (`HierarchyNodeAdmin.save_model`) quanto pela API de CRUD da SPA — dois pontos de entrada
        que editam hierarquia agora (Decisão 4 revista), então a checagem fica centralizada aqui
        em vez de duplicada em cada um."""
        if previous is None:
            return []

        was_deactivated = previous.ativo and not node.ativo
        was_reparented = previous.parent_id != node.parent_id
        if not (was_deactivated or was_reparented):
            return []

        return HierarchyChangeReassignmentService.reassign_open_cycle_allocations(node, changed_by=changed_by)

    @staticmethod
    @transaction.atomic
    def reassign_open_cycle_allocations(node: HierarchyNode, changed_by) -> list[GoalAllocation]:
        affected_allocations = GoalAllocation.objects.filter(
            owner_node=node, cycle__status=Cycle.Status.ABERTO, parent_allocation__isnull=False
        ).select_related("parent_allocation")

        reopened = []
        seen_parent_ids = set()
        for allocation in affected_allocations:
            parent = allocation.parent_allocation
            if parent.id in seen_parent_ids or not parent.distributed:
                continue
            seen_parent_ids.add(parent.id)
            ReopenAllocationService.reopen_for_hierarchy_change(
                parent, changed_by=changed_by, affected_node=node
            )
            reopened.append(parent)

        return reopened


@dataclass(frozen=True)
class StuckAllocation:
    allocation_id: int
    owner_node_id: int
    owner_node_level: str
    quantity_kg: int


class CycleCompletenessChecker:
    """Verifica a invariante end-to-end: 100% da meta do ciclo chega ao nível VENDEDOR.

    Alocações VENDEDOR são folhas legítimas e nunca contam como presas, mesmo com
    distributed=False. Tratamento de nós inativos / ramos sem vendedor ativo fica em
    aberto (ver docs/open-questions.md O4/O5) e não é decidido aqui.
    """

    @staticmethod
    def stuck_allocations(cycle) -> list[StuckAllocation]:
        pending = (
            GoalAllocation.objects.filter(cycle=cycle, distributed=False)
            .exclude(owner_node__level=HierarchyNode.Level.VENDEDOR)
            .select_related("owner_node")
        )
        return [
            StuckAllocation(
                allocation_id=allocation.id,
                owner_node_id=allocation.owner_node_id,
                owner_node_level=allocation.owner_node.level,
                quantity_kg=allocation.quantity_kg,
            )
            for allocation in pending
        ]

    @classmethod
    def is_complete(cls, cycle) -> bool:
        return not cls.stuck_allocations(cycle)


@dataclass(frozen=True)
class VendedorAllocationRow:
    gerente_nome: str
    local_nome: str
    supervisor_nome: str
    vendedor_nome: str
    grupo_nome: str
    subgrupo_nome: str
    quantity_kg: int
    status: str  # "META" ou "META AJUSTADA"


class VendedorAllocationReportService:
    """Achata a árvore de alocações do ciclo até a folha (Vendedor, sempre SUBGROUP — O1) numa
    linha por alocação, com o caminho completo até o Gerente. Fonte única tanto da
    tela de Metas quanto do CSV de exportação.

    Status "META" vs "META AJUSTADA": não é um campo novo no modelo — é derivado de H4
    (reabertura). Se a alocação em si ou qualquer ancestral na cadeia (`parent_allocation`) já
    foi reaberta neste ciclo (`AuditLogEntry.Action.REABERTURA`), a versão atual é fruto de um
    redo (ex.: Supervisor corrigindo por quebra de estoque no fim do mês) — "META AJUSTADA".
    Sem redo registrado, é a distribuição original — "META". Reaproveita o mecanismo já aprovado
    de reabrir/redistribuir em vez de inventar um novo campo/fluxo de ajuste.
    """

    @staticmethod
    def rows_for_cycle(cycle) -> list[VendedorAllocationRow]:
        allocations = list(
            GoalAllocation.objects.filter(cycle=cycle).select_related(
                "owner_node__parent__parent__parent", "subgroup__group"
            )
        )
        by_id = {allocation.id: allocation for allocation in allocations}

        reopened_ids = set(
            AuditLogEntry.objects.filter(
                content_type=ContentType.objects.get_for_model(GoalAllocation),
                object_id__in=by_id.keys(),
                action=AuditLogEntry.Action.REABERTURA,
            ).values_list("object_id", flat=True)
        )

        rows = []
        for allocation in allocations:
            if allocation.owner_node.level != HierarchyNode.Level.VENDEDOR:
                continue

            adjusted = False
            current = allocation
            while current is not None:
                if current.id in reopened_ids:
                    adjusted = True
                    break
                current = by_id.get(current.parent_allocation_id)

            supervisor = allocation.owner_node.parent
            local = supervisor.parent if supervisor else None
            gerente = local.parent if local else None

            rows.append(
                VendedorAllocationRow(
                    gerente_nome=gerente.nome if gerente else "",
                    local_nome=local.nome if local else "",
                    supervisor_nome=supervisor.nome if supervisor else "",
                    vendedor_nome=allocation.owner_node.nome,
                    grupo_nome=allocation.subgroup.group.nome if allocation.subgroup_id else "",
                    subgrupo_nome=allocation.subgroup.nome if allocation.subgroup_id else "",
                    quantity_kg=allocation.quantity_kg,
                    status="META AJUSTADA" if adjusted else "META",
                )
            )
        return rows


def previous_month(ano: int, mes: int) -> tuple[int, int]:
    """Mês imediatamente anterior a (ano, mes) — usado como `last_month` da janela de 12 meses
    de histórico do P1, de forma que a projeção (que olha um mês além do fim da janela) caia
    exatamente no mês do ciclo sendo planejado."""
    return (ano, mes - 1) if mes > 1 else (ano - 1, 12)


class GoalSuggestionService:
    """P1: sugestão automática por grupo pro Gerente, com breakdown auditável (tendência, índice
    sazonal, comparação com o mesmo mês do ano passado, aviso de lacuna no histórico) — ver
    Decisão 6 em docs/decisions.md. Só orquestra: a fórmula em si vive em `strategies.py`, os
    dados em `SalesHistoryProvider`."""

    PERIOD_MONTHS = 12

    @staticmethod
    def suggest_for_cycle(cycle: Cycle) -> dict[int, GroupSuggestion]:
        last_month = previous_month(cycle.ano, cycle.mes)
        group_ids = list(ProductGroup.objects.filter(ativo=True).values_list("id", flat=True))

        history_by_group = {
            group_id: SalesHistoryProvider.group_history(
                group_id, GoalSuggestionService.PERIOD_MONTHS, last_month
            )
            for group_id in group_ids
        }
        strategy = SeasonalTrendSuggestionStrategy(history_by_group)
        return strategy.suggest_detailed(group_ids, GoalSuggestionService.PERIOD_MONTHS)


@dataclass(frozen=True)
class ChildDistributionContext:
    """Contexto histórico de um alvo direto de uma alocação a distribuir — histórico de 12 meses,
    comparativos (mesmo mês ano passado, média últimos 3 meses) e participação, pra apoiar a
    decisão de quem está distribuindo manualmente (Gerente→Local, e agora
    também a Etapa 2 — Subgrupo→Supervisor — da quebra do Coordenador Local).

    `suggested_kg` só vem preenchido quando o nível de quem distribui tem fórmula AUTO ligada
    (GERENTE/LOCAL/SUPERVISOR — ver `default_distribution_registry`); sem AUTO ligada
    fica None e a UI não mostra número pré-calculado nenhum, só o histórico/comparativos.

    Quando a alocação sendo distribuída é de um SUBGRUPO específico, `history` (e tudo que dele
    deriva — `same_month_last_year_kg`, `last_3_months_avg_kg`, `historical_share_pct`, `has_gap`
    e o peso usado em `suggested_kg`) é sempre o histórico daquele subgrupo, não do grupo inteiro
    — ver Decisão 6, revisão 2026-09-02."""

    owner_node_id: int
    history: list[MonthlyQuantity]
    same_month_last_year_kg: float | None
    last_3_months_avg_kg: float | None
    historical_share_pct: float | None
    has_gap: bool
    suggested_kg: int | None


PERIOD_MONTHS = 12


def _build_child_distribution_contexts(
    *,
    owner_node: HierarchyNode,
    cycle: Cycle,
    group_id: int | None,
    total_kg: int,
    subgroup_id: int | None = None,
) -> list[ChildDistributionContext]:
    """Núcleo compartilhado entre `DistributionContextService` (Gerente→Local,
    e a distribuição "tudo de uma vez" pro nível LOCAL) e `SupervisorSplitContextService` (Etapa 2
    do wizard de subgrupo do Coordenador Local): pesa os filhos diretos de `owner_node` pelo
    histórico de cada um e reparte `total_kg` entre eles via a `DistributionStrategy` AUTO do
    nível, se houver. `total_kg` é parametrizado porque a Etapa 2 reparte o valor de um subgrupo
    específico (ainda não salvo), não a meta inteira do grupo.

    `subgroup_id`, quando informado (alocação sendo distribuída é de um subgrupo específico —
    Meta Supervisor/Meta Vendedor), faz TODO o histórico usado aqui (peso da sugestão automática
    incluído) ser o do subgrupo, não do grupo inteiro (Decisão 6, revisão 2026-09-02, a pedido
    explícito do usuário: quem não tem histórico de venda naquele subgrupo específico não deve
    puxar sugestão automática nele, mesmo tendo histórico forte no grupo como um todo — a edição
    manual continua livre, isso só afeta o valor pré-preenchido). Sem `subgroup_id` (alocação
    GROUP — Gerente→Regional, Regional→Local), o histórico continua sendo do grupo inteiro, como
    sempre foi.

    Quando NINGUÉM tem histórico na base escolhida (soma zero, ex.: `ExternalSalespersonMapping`
    ainda sem curadoria — O3 em docs/open-questions.md — ou subgrupo novo que ninguém vendeu
    ainda), `RecentAverageDistributionStrategy` (o `AUTO` default, ver `strategies.py`) não deixa a
    sugestão vazia: reparte `total_kg` em partes iguais entre os alvos (garantia confirmada com o
    usuário, 2026-09-03 — sempre existe uma sugestão pré-preenchida, editável, mesmo sem dado
    nenhum). O `except ValueError` abaixo continua só como rede de segurança pra alguma
    `DistributionStrategy` alternativa que ainda degrade dessa forma (ex.:
    `SeasonalTrendDistributionStrategy`, se alguém a registrar de volta)."""
    children_nodes = list(HierarchyNode.objects.filter(parent_id=owner_node.id, ativo=True))
    if not children_nodes or group_id is None:
        return []

    # Cobertura de férias (Decisão 13, revisão 2026-09-10): titular de férias sai da lista de
    # alvos deste ciclo — só o ferista (já um filho normal de `owner_node`) recebe meta na rota,
    # sem duplicidade. Fora do ciclo/mês coberto, o titular volta a aparecer normalmente.
    covered_this_cycle = set(
        FeristaCoverage.objects.filter(ano=cycle.ano, mes=cycle.mes).values_list("covered_node_id", flat=True)
    )
    children_nodes = [node for node in children_nodes if node.id not in covered_this_cycle]
    if not children_nodes:
        return []

    last_month = previous_month(cycle.ano, cycle.mes)
    history_by_target = {
        node.id: SalesHistoryProvider.target_history(
            node.id,
            PERIOD_MONTHS,
            last_month,
            group_id=group_id,
            subgroup_id=subgroup_id,
        )
        for node in children_nodes
    }

    total_12m_by_target = {
        node_id: sum(point.quantity_kg for point in history) for node_id, history in history_by_target.items()
    }
    grand_total = sum(total_12m_by_target.values())

    try:
        strategy = default_distribution_registry.resolve(
            owner_node.level, "AUTO", history_by_target=history_by_target
        )
        suggested_by_target = strategy.distribute(total_kg, [node.id for node in children_nodes])
    except StrategyNotConfiguredError:
        suggested_by_target = {}
    except ValueError:
        # Rede de segurança: o default (`RecentAverageDistributionStrategy`) não levanta mais
        # ValueError pra "ninguém tem histórico" (reparte em partes iguais, ver docstring acima) —
        # isso só dispara se alguém registrar uma DistributionStrategy alternativa que ainda
        # degrade dessa forma. Degrada pra "sem sugestão", igual a nível sem AUTO configurada, em
        # vez de derrubar o endpoint.
        suggested_by_target = {}

    result = []
    for node in children_nodes:
        history = history_by_target[node.id]
        last_3_months = history[-3:] if len(history) >= 3 else history
        result.append(
            ChildDistributionContext(
                owner_node_id=node.id,
                history=history,
                same_month_last_year_kg=(history[0].quantity_kg if len(history) == PERIOD_MONTHS else None),
                last_3_months_avg_kg=(
                    sum(point.quantity_kg for point in last_3_months) / len(last_3_months)
                    if last_3_months
                    else None
                ),
                historical_share_pct=(
                    total_12m_by_target[node.id] / grand_total * 100 if grand_total > 0 else None
                ),
                has_gap=any(point.quantity_kg == 0 for point in history),
                suggested_kg=suggested_by_target.get(node.id),
            )
        )
    return result


class DistributionContextService:
    """Monta o `ChildDistributionContext` de cada filho direto do nó que está distribuindo,
    reaproveitando `SalesHistoryProvider.target_history` (mesma fonte de P2-P4) e, quando
    aplicável, a `DistributionStrategy` AUTO já aprovada para o nível — nunca inventa fórmula
    nova aqui, só orquestra o que já existe em `strategies.py`.

    Funciona tanto pra alocação GROUP (Gerente→Local) quanto SUBGROUP — a
    tela "Meta Supervisor"/"Meta Vendedor" chama isso numa alocação SUBGROUP já persistida (dona
    = Coordenador Local ou Supervisor), e desde a Decisão 6 (revisão 2026-09-02) o peso passa a
    vir do histórico daquele SUBGRUPO específico (resolvido via `allocation.subgroup_id`), não do
    grupo inteiro — ver `_build_child_distribution_contexts` para o porquê da mudança."""

    PERIOD_MONTHS = PERIOD_MONTHS

    @staticmethod
    def build(allocation: GoalAllocation) -> list[ChildDistributionContext]:
        group_id = allocation.group_id
        if group_id is None and allocation.subgroup_id is not None:
            group_id = allocation.subgroup.group_id

        return _build_child_distribution_contexts(
            owner_node=allocation.owner_node,
            cycle=allocation.cycle,
            group_id=group_id,
            total_kg=allocation.quantity_kg,
            subgroup_id=allocation.subgroup_id,
        )


@dataclass(frozen=True)
class SubgroupDistributionContext:
    """Contexto histórico de um subgrupo de uma meta GROUP que o Coordenador Local está quebrando
    em subgrupos (Etapa 1 do wizard novo) — mesma forma de `ChildDistributionContext`, mas
    chaveado por subgrupo em vez de nó, porque aqui os "alvos" são categorias de produto, não
    posições da hierarquia."""

    subgroup_id: int
    subgroup_nome: str
    history: list[MonthlyQuantity]
    same_month_last_year_kg: float | None
    last_3_months_avg_kg: float | None
    historical_share_pct: float | None
    has_gap: bool
    suggested_kg: int | None


class SubgroupDistributionContextService:
    """Etapa 1 do wizard de quebra do Coordenador Local: sugere quanto cada SUBGRUPO recebe da
    meta GROUP recebida pelo nó LOCAL, pesando pelo histórico de vendas de cada subgrupo dentro
    do próprio escopo (sub-árvore) daquele nó — análogo a P1 (sugestão por grupo pro Gerente), um
    nível mais fundo. Comparação entre SUBGRUPOS dentro do mesmo nó, com volume agregado
    equivalente ao de P1 (não é peso entre nós/pessoas, então a fragilidade de dado esparso de
    P2-P4 não se aplica aqui).

    Peso: `RecentAverageDistributionStrategy` (Decisão 6, revisão 2026-09-03, a pedido do
    usuário) — proporção pela média dos últimos 3 meses de cada subgrupo, com piso de 0,5% de
    participação (quem fica abaixo é zerado e redistribuído entre os demais). Antes disso era
    `SeasonalTrendDistributionStrategy` (mesma de P1-P4 na época), trocada porque divergia demais
    da média real vendida pro caso de comparar produtos dentro de um catálogo — instanciada direto
    aqui (não via `default_distribution_registry`) porque esta etapa reparte entre SUBGRUPOS do
    mesmo nó, não entre nós/pessoas de um nível hierárquico. No mesmo dia, a troca foi estendida
    também ao registry (`default_distribution_registry`, `strategies.py`), então P2-P4 agora usam
    a mesma fórmula por padrão — ver strategies.py."""

    PERIOD_MONTHS = PERIOD_MONTHS

    @staticmethod
    def build(allocation: GoalAllocation) -> list[SubgroupDistributionContext]:
        if allocation.group_id is None:
            return []

        subgroups = list(ProductSubgroup.objects.filter(group_id=allocation.group_id, ativo=True))
        if not subgroups:
            return []

        last_month = previous_month(allocation.cycle.ano, allocation.cycle.mes)
        history_by_subgroup = {
            subgroup.id: SalesHistoryProvider.target_history(
                allocation.owner_node_id,
                SubgroupDistributionContextService.PERIOD_MONTHS,
                last_month,
                group_id=allocation.group_id,
                subgroup_id=subgroup.id,
            )
            for subgroup in subgroups
        }

        total_12m_by_subgroup = {
            subgroup_id: sum(point.quantity_kg for point in history)
            for subgroup_id, history in history_by_subgroup.items()
        }
        grand_total = sum(total_12m_by_subgroup.values())

        try:
            strategy = RecentAverageDistributionStrategy(
                history_by_subgroup, LargestRemainderRoundingPolicy()
            )
            suggested_by_subgroup = strategy.distribute(allocation.quantity_kg, [sg.id for sg in subgroups])
        except ValueError:
            suggested_by_subgroup = {}

        result = []
        for subgroup in subgroups:
            history = history_by_subgroup[subgroup.id]
            last_3_months = history[-3:] if len(history) >= 3 else history
            result.append(
                SubgroupDistributionContext(
                    subgroup_id=subgroup.id,
                    subgroup_nome=subgroup.nome,
                    history=history,
                    same_month_last_year_kg=(
                        history[0].quantity_kg
                        if len(history) == SubgroupDistributionContextService.PERIOD_MONTHS
                        else None
                    ),
                    last_3_months_avg_kg=(
                        sum(point.quantity_kg for point in last_3_months) / len(last_3_months)
                        if last_3_months
                        else None
                    ),
                    historical_share_pct=(
                        total_12m_by_subgroup[subgroup.id] / grand_total * 100 if grand_total > 0 else None
                    ),
                    has_gap=any(point.quantity_kg == 0 for point in history),
                    suggested_kg=suggested_by_subgroup.get(subgroup.id),
                )
            )
        return result


@dataclass(frozen=True)
class SubgroupSplitSpec:
    subgroup_id: int
    quantity_kg: int


class SplitGroupIntoSubgroupsService:
    """Tela "Distribuir Produtos": o Coordenador Local quebra a meta GROUP recebida em metas
    SUBGROUP, permanecendo dono do mesmo nó — não é um repasse pra outro nível da hierarquia (isso
    só acontece depois, na tela "Meta Supervisor", via `DistributeGoalService` normal), por isso
    não passa pela checagem de nó-filho direto de `DistributeGoalService.distribute()`. Reaproveita
    o mesmo `ClosureValidator` — a invariante de fechamento exato não muda, só quem é o dono das
    alocações filhas."""

    @staticmethod
    @transaction.atomic
    def split(parent: GoalAllocation, specs: list[SubgroupSplitSpec], criado_por) -> list[GoalAllocation]:
        if parent.distributed:
            raise AllocationClosureError("Esta alocação já foi distribuída.")

        if not criado_por.hierarchy_nodes.filter(id=parent.owner_node_id).exists():
            raise AllocationScopeError("Você só pode distribuir uma alocação que possui.")

        if (
            parent.owner_node.level != HierarchyNode.Level.LOCAL
            or parent.granularity != GoalAllocation.Granularity.GROUP
        ):
            raise AllocationScopeError(
                "Só é possível quebrar em subgrupos uma meta de grupo do Coordenador Local."
            )

        subgroup_ids = [spec.subgroup_id for spec in specs]
        subgroups_by_id = {sg.id: sg for sg in ProductSubgroup.objects.filter(id__in=subgroup_ids)}
        missing = set(subgroup_ids) - set(subgroups_by_id)
        if missing:
            raise AllocationScopeError(f"Subgrupo(s) inexistente(s): {sorted(missing)}.")
        wrong_group = [sg.id for sg in subgroups_by_id.values() if sg.group_id != parent.group_id]
        if wrong_group:
            raise AllocationScopeError("Todo subgrupo precisa pertencer ao grupo desta alocação.")

        ClosureValidator.validate(parent.quantity_kg, [spec.quantity_kg for spec in specs])

        created = [
            GoalAllocation(
                cycle=parent.cycle,
                owner_node=parent.owner_node,
                parent_allocation=parent,
                granularity=GoalAllocation.Granularity.SUBGROUP,
                subgroup_id=spec.subgroup_id,
                quantity_kg=spec.quantity_kg,
                criado_por=criado_por,
            )
            for spec in specs
        ]
        for allocation in created:
            allocation.full_clean()
            allocation.save()

        parent.distributed = True
        parent.save(update_fields=["distributed", "updated_at"])

        return created


class CreateRootAllocationError(ValidationError):
    """Falha ao criar a meta raiz (nível Gerente, sem alocação-pai)."""


class CreateRootAllocationService:
    """Cria a alocação raiz de um ciclo (nível Gerente, `parent_allocation=None`) — o ponto de
    partida da cascata, que hoje só existia via Django Admin/seed. A sugestão P1 pré-preenche o
    `quantity_kg` no frontend, mas o valor final sempre vem do usuário (revisável, nunca
    auto-persistido — ver [[project_goal-suggestion-workflow]])."""

    @staticmethod
    @transaction.atomic
    def create(
        *,
        cycle: Cycle,
        owner_node: HierarchyNode,
        granularity: str,
        quantity_kg: int,
        criado_por,
        group_id: int | None = None,
        subgroup_id: int | None = None,
        product_id: int | None = None,
    ) -> GoalAllocation:
        if not criado_por.hierarchy_nodes.filter(id=owner_node.id).exists():
            raise AllocationScopeError("Você só pode criar meta para um nó que possui.")
        if owner_node.level != HierarchyNode.Level.GERENTE or owner_node.parent_id is not None:
            raise AllocationScopeError(
                "A meta raiz só pode ser criada para um nó Gerente, no topo da hierarquia."
            )

        duplicate = GoalAllocation.objects.filter(
            cycle=cycle,
            owner_node=owner_node,
            parent_allocation__isnull=True,
            granularity=granularity,
            group_id=group_id,
            subgroup_id=subgroup_id,
            product_id=product_id,
        ).exists()
        if duplicate:
            raise CreateRootAllocationError(
                "Já existe uma meta raiz para esse grupo/subgrupo/produto neste ciclo."
            )

        allocation = GoalAllocation(
            cycle=cycle,
            owner_node=owner_node,
            parent_allocation=None,
            granularity=granularity,
            group_id=group_id,
            subgroup_id=subgroup_id,
            product_id=product_id,
            quantity_kg=quantity_kg,
            criado_por=criado_por,
        )
        allocation.full_clean()
        allocation.save()
        return allocation
