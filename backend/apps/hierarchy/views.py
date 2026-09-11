from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import IsAppAdmin

from .models import FeristaCoverage, HierarchyNode
from .serializers import FeristaCoverageSerializer, HierarchyNodeSerializer

WRITE_ACTIONS = ("create", "update", "partial_update")


class HierarchyNodeViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Sem destroy: a gestão de hierarquia inativa nós (`ativo=False`), nunca apaga — evitar
    perder o histórico de `parent` referenciado por alocações/closure passadas."""

    serializer_class = HierarchyNodeSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in WRITE_ACTIONS:
            return [IsAuthenticated(), IsAppAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        return HierarchyNode.objects.visible_to(self.request.user).order_by("level", "nome")

    def perform_update(self, serializer):
        previous = HierarchyNode.objects.get(pk=serializer.instance.pk)
        node = serializer.save()

        # Import tardio: allocations importa hierarchy.models, evita ciclo com hierarchy.views.
        from apps.allocations.services import HierarchyChangeReassignmentService

        HierarchyChangeReassignmentService.detect_and_reassign_if_needed(
            previous, node, changed_by=self.request.user
        )


class FeristaCoverageViewSet(viewsets.ModelViewSet):
    """Função do Administrador (Decisão 13, revisão 2026-09-10): quem cobre quem, em qual mês. Com
    destroy — diferente de hierarquia/catálogo, nada mais referencia uma linha daqui, então
    corrigir um cadastro errado é só apagar e recriar."""

    serializer_class = FeristaCoverageSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in WRITE_ACTIONS + ("destroy",):
            return [IsAuthenticated(), IsAppAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = FeristaCoverage.objects.select_related("covering_node", "covered_node").order_by(
            "-ano", "-mes", "covering_node__nome"
        )
        ano = self.request.query_params.get("ano")
        if ano:
            queryset = queryset.filter(ano=ano)
        return queryset
