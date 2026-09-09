from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.catalog.models import ProductGroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import HierarchyNode
from apps.hierarchy.services import ExternalSalespersonMatchingService

from .models import GoalAllocation
from .serializers import (
    ChildDistributionContextSerializer,
    CreateRootAllocationSerializer,
    DistributeRequestSerializer,
    GoalAllocationSerializer,
    GroupSuggestionSerializer,
    ResetGroupRequestSerializer,
    SplitSubgroupsRequestSerializer,
    SubgroupDistributionContextSerializer,
)
from .services import (
    AllocationClosureError,
    AllocationReopenError,
    AllocationScopeError,
    ChildAllocationSpec,
    CreateRootAllocationError,
    CreateRootAllocationService,
    DistributeGoalService,
    DistributionContextService,
    GoalSuggestionService,
    ReopenAllocationService,
    SelfVendedorAutoDistributionService,
    SplitGroupIntoSubgroupsService,
    SubgroupDistributionContextService,
    SubgroupSplitSpec,
)


class GoalAllocationViewSet(ReadOnlyModelViewSet):
    serializer_class = GoalAllocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `has_further_distribution` (exibido como `GoalAllocationSerializer` abaixo) diz se algum
        # filho direto já repassou adiante o que recebeu, com trabalho real por trás — é a trava
        # do botão "Resetar distribuição" (2026-08-04): reabrir aqui apagaria em cascata um
        # trabalho que já avançou pra baixo. Dois casos NÃO contam como trabalho real (revisão
        # 2026-09-03), então ficam de fora do bloqueio:
        #   - `quantity_kg__gt=0`: um filho com 0 kg não perde nada se recriado do zero.
        #   - `owner_node_id` fora de `self_managed_ids`: repasse automático de autogestão
        #     (`SelfVendedorAutoDistributionService`, único alvo possível) não é decisão de
        #     ninguém — acontece de novo sozinho, idêntico, se o pai for resetado e redistribuído
        #     com outra quantidade — então vale mesmo com quantidade > 0.
        # Subquery em vez de checar em Python pra não virar N+1 numa lista com centenas de
        # alocações; `self_managed_ids` é 1 query em lote (ver
        # `SelfVendedorAutoDistributionService.self_managed_supervisor_ids`), não por linha.
        self_managed_ids = SelfVendedorAutoDistributionService.self_managed_supervisor_ids()
        queryset = (
            GoalAllocation.objects.visible_to(self.request.user)
            .select_related("owner_node", "group", "subgroup__group")
            .prefetch_related("owner_node__users")
            .annotate(
                has_further_distribution=Exists(
                    GoalAllocation.objects.filter(
                        parent_allocation=OuterRef("pk"), distributed=True, quantity_kg__gt=0
                    ).exclude(owner_node_id__in=self_managed_ids)
                )
            )
        )
        cycle_id = self.request.query_params.get("cycle")
        if cycle_id:
            queryset = queryset.filter(cycle_id=cycle_id)
        return queryset.order_by("-created_at")

    @action(detail=True, methods=["post"])
    def distribute(self, request, pk=None):
        parent = self.get_object()

        request_serializer = DistributeRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        children = [
            ChildAllocationSpec(
                owner_node_id=child["owner_node_id"],
                quantity_kg=child["quantity_kg"],
                granularity=child["granularity"],
                group_id=child.get("group_id"),
                subgroup_id=child.get("subgroup_id"),
                product_id=child.get("product_id"),
            )
            for child in request_serializer.validated_data["children"]
        ]

        try:
            created = DistributeGoalService.distribute(parent, children, criado_por=request.user)
        except (AllocationClosureError, AllocationScopeError) as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(GoalAllocationSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="distribution-context")
    def distribution_context(self, request, pk=None):
        """Contexto histórico por filho direto (histórico 12 meses, comparativos, participação e,
        quando aplicável ao nível, sugestão AUTO já aprovada) — só apoia a decisão manual de quem
        está distribuindo, nunca substitui."""
        allocation = self.get_object()

        if not request.user.hierarchy_nodes.filter(id=allocation.owner_node_id).exists():
            return Response(
                {"detail": "Você não tem acesso a essa alocação."}, status=status.HTTP_403_FORBIDDEN
            )

        # Sem custo de rede (só tabelas locais já sincronizadas) — garante que um Vendedor
        # cadastrado depois da última sincronização manual já entra com histórico real na
        # sugestão, em vez de aparecer zerado até alguém lembrar de rodar o comando (bug real,
        # 2026-08-07, ver `ExternalSalespersonMatchingService`).
        ExternalSalespersonMatchingService.sync()
        contexts = DistributionContextService.build(allocation)
        return Response(ChildDistributionContextSerializer(contexts, many=True).data)

    @action(detail=True, methods=["get"], url_path="subgroup-distribution-context")
    def subgroup_distribution_context(self, request, pk=None):
        """Tela "Distribuir Produtos": sugestão de quanto cada subgrupo do grupo recebe da meta
        GROUP recebida pelo Coordenador Local, pra pré-preencher a divisão em subgrupos."""
        allocation = self.get_object()

        if not request.user.hierarchy_nodes.filter(id=allocation.owner_node_id).exists():
            return Response(
                {"detail": "Você não tem acesso a essa alocação."}, status=status.HTTP_403_FORBIDDEN
            )

        # Mesmo motivo de `distribution_context` acima.
        ExternalSalespersonMatchingService.sync()
        contexts = SubgroupDistributionContextService.build(allocation)
        return Response(SubgroupDistributionContextSerializer(contexts, many=True).data)

    @action(detail=True, methods=["post"], url_path="split-subgroups")
    def split_subgroups(self, request, pk=None):
        """Tela "Distribuir Produtos": persiste a quebra da meta GROUP em metas SUBGROUP, ainda
        dona do mesmo nó Local — a distribuição pra Supervisor acontece depois, na tela "Meta
        Supervisor", como um `distribute()` normal sobre cada uma dessas alocações SUBGROUP."""
        parent = self.get_object()

        request_serializer = SplitSubgroupsRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        specs = [
            SubgroupSplitSpec(subgroup_id=item["subgroup_id"], quantity_kg=item["quantity_kg"])
            for item in request_serializer.validated_data["subgroups"]
        ]

        try:
            created = SplitGroupIntoSubgroupsService.split(parent, specs, criado_por=request.user)
        except (AllocationClosureError, AllocationScopeError) as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(GoalAllocationSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        allocation = self.get_object()

        try:
            ReopenAllocationService.reopen(allocation, criado_por=request.user)
        except (AllocationScopeError, AllocationReopenError) as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(GoalAllocationSerializer(allocation).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="reset-group")
    def reset_group(self, request):
        """Telas "Meta Supervisor"/"Meta Vendedor" (`SubgroupCascadeWorkspace`): reseta de uma vez
        todos os subgrupos já distribuídos de um grupo, pro nível que os possui neste ciclo —
        alternativa em lote ao `reopen()` por subgrupo, pra quando o nível errou a distribuição do
        grupo inteiro."""
        request_serializer = ResetGroupRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        cycle = get_object_or_404(Cycle, id=data["cycle_id"])
        owner_node = get_object_or_404(HierarchyNode, id=data["owner_node_id"])

        try:
            reset_allocations = ReopenAllocationService.reopen_group(
                owner_node=owner_node, cycle=cycle, group_id=data["group_id"], criado_por=request.user
            )
        except (AllocationScopeError, AllocationReopenError) as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            GoalAllocationSerializer(reset_allocations, many=True).data, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"])
    def suggestions(self, request):
        """P1: sugestão automática por grupo pro Gerente, com breakdown auditável — pré-preenche
        a criação da meta raiz, nunca a substitui (revisão humana sempre exigida)."""
        cycle_id = request.query_params.get("cycle")
        owner_node_id = request.query_params.get("owner_node")
        if not cycle_id or not owner_node_id:
            return Response(
                {"detail": "Parâmetros cycle e owner_node são obrigatórios."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cycle = get_object_or_404(Cycle, id=cycle_id)
        owner_node = get_object_or_404(HierarchyNode, id=owner_node_id)

        if not request.user.hierarchy_nodes.filter(id=owner_node.id).exists():
            return Response({"detail": "Você não tem acesso a esse nó."}, status=status.HTTP_403_FORBIDDEN)

        suggestions_by_group = GoalSuggestionService.suggest_for_cycle(cycle)
        groups_by_id = {group.id: group for group in ProductGroup.objects.filter(ativo=True)}
        created_group_ids = set(
            GoalAllocation.objects.filter(
                cycle=cycle, owner_node=owner_node, parent_allocation__isnull=True
            ).values_list("group_id", flat=True)
        )

        payload = [
            {
                "group_id": group_id,
                "group_nome": groups_by_id[group_id].nome,
                "trend_kg": suggestion.trend_kg,
                "seasonal_index": suggestion.seasonal_index,
                "suggested_kg": suggestion.suggested_kg,
                "has_gap": suggestion.has_gap,
                "same_month_last_year_kg": suggestion.same_month_last_year_kg,
                "history": [
                    {"ano": point.ano, "mes": point.mes, "quantity_kg": point.quantity_kg}
                    for point in suggestion.history
                ],
                "already_created": group_id in created_group_ids,
            }
            for group_id, suggestion in suggestions_by_group.items()
            if group_id in groups_by_id
        ]

        return Response(GroupSuggestionSerializer(payload, many=True).data)

    @action(detail=False, methods=["post"], url_path="root", url_name="root")
    def create_root(self, request):
        """Cria a meta raiz do Gerente (nível topo, sem alocação-pai) — o passo que hoje só
        existia via Django Admin. O valor sugerido (P1) só pré-preenche no frontend; o que chega
        aqui já é a decisão final do usuário."""
        request_serializer = CreateRootAllocationSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        cycle = get_object_or_404(Cycle, id=data["cycle_id"])
        owner_node = get_object_or_404(HierarchyNode, id=data["owner_node_id"])

        try:
            allocation = CreateRootAllocationService.create(
                cycle=cycle,
                owner_node=owner_node,
                granularity=data["granularity"],
                quantity_kg=data["quantity_kg"],
                criado_por=request.user,
                group_id=data.get("group_id"),
                subgroup_id=data.get("subgroup_id"),
                product_id=data.get("product_id"),
            )
        except (AllocationScopeError, CreateRootAllocationError) as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(GoalAllocationSerializer(allocation).data, status=status.HTTP_201_CREATED)
