import csv
import io

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.accounts.permissions import IsAppAdmin
from apps.allocations.models import GoalAllocation
from apps.allocations.serializers import AllocationOverviewSerializer
from apps.allocations.services import CycleCompletenessChecker, VendedorAllocationReportService

from .models import Cycle
from .serializers import CycleSerializer, StuckAllocationSerializer
from .services import CloseCycleService, CycleAlreadyExistsError, CycleNotCompleteError, OpenCycleService

ADMIN_ONLY_ACTIONS = ("open", "distribution_overview", "export", "vendedor_report")


class CycleViewSet(ReadOnlyModelViewSet):
    queryset = Cycle.objects.all().order_by("-ano", "-mes")
    serializer_class = CycleSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ADMIN_ONLY_ACTIONS:
            return [IsAuthenticated(), IsAppAdmin()]
        return super().get_permissions()

    @action(detail=True, methods=["get"])
    def completeness(self, request, pk=None):
        cycle = self.get_object()
        stuck = CycleCompletenessChecker.stuck_allocations(cycle)
        return Response(
            {
                "complete": not stuck,
                "stuck_allocations": StuckAllocationSerializer(stuck, many=True).data,
            }
        )

    @action(detail=False, methods=["post"])
    def open(self, request):
        serializer = CycleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            cycle = OpenCycleService.open(serializer.validated_data["ano"], serializer.validated_data["mes"])
        except CycleAlreadyExistsError as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CycleSerializer(cycle).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        cycle = self.get_object()
        force = bool(request.data.get("force"))
        try:
            CloseCycleService.close(cycle, force=force)
        except CycleNotCompleteError as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CycleSerializer(cycle).data)

    @action(detail=True, methods=["get"], url_path="distribution-overview")
    def distribution_overview(self, request, pk=None):
        """Visão completa do Administrador: toda alocação do ciclo, em qualquer nível, com quem
        é dono do nó — não passa por `visible_to` (é intencionalmente global)."""
        cycle = self.get_object()
        allocations = (
            GoalAllocation.objects.filter(cycle=cycle)
            .select_related("owner_node__parent", "criado_por", "group", "subgroup__group")
            .prefetch_related("owner_node__users")
            .order_by("owner_node__level", "owner_node__nome")
        )
        return Response(AllocationOverviewSerializer(allocations, many=True).data)

    @action(detail=True, methods=["get"], url_path="vendedor-report")
    def vendedor_report(self, request, pk=None):
        """Meta no nível Vendedor (folha, SUBGROUP — O1), achatada com o caminho até o
        Gerente — usada pela tela de Metas; mesma fonte de dados do `export` (CSV) abaixo."""
        cycle = self.get_object()
        rows = VendedorAllocationReportService.rows_for_cycle(cycle)
        return Response(
            [
                {
                    "gerente": row.gerente_nome,
                    "local": row.local_nome,
                    "supervisor": row.supervisor_nome,
                    "vendedor": row.vendedor_nome,
                    "grupo": row.grupo_nome,
                    "subgrupo": row.subgrupo_nome,
                    "quantity_kg": row.quantity_kg,
                    "status": row.status,
                }
                for row in rows
            ]
        )

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        """Baixa em CSV a meta no nível Vendedor do ciclo inteiro — mesma linha por linha que a
        tela de Metas (`VendedorAllocationReportService`)."""
        cycle = self.get_object()
        rows = VendedorAllocationReportService.rows_for_cycle(cycle)
        ciclo_label = f"{cycle.mes:02d}/{cycle.ano}"

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "gerente",
                "coordenador_local",
                "supervisor",
                "vendedor",
                "grupo",
                "subgrupo",
                "meta_kg",
                "ciclo",
                "status",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.gerente_nome,
                    row.local_nome,
                    row.supervisor_nome,
                    row.vendedor_nome,
                    row.grupo_nome,
                    row.subgrupo_nome,
                    row.quantity_kg,
                    ciclo_label,
                    row.status,
                ]
            )

        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f"attachment; filename=meta_{cycle.ano}_{cycle.mes:02d}.csv"
        return response
