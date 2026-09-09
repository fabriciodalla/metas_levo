from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import HierarchyNode

from .models import GoalAllocation
from .services import (
    ChildAllocationSpec,
    DistributeGoalService,
    ReopenAllocationService,
    VendedorAllocationReportService,
)

User = get_user_model()


class VendedorAllocationReportServiceTests(TestCase):
    """Achatamento da árvore até o Vendedor + status META/META AJUSTADA derivado de H4."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=7)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Fabio")
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        self.vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor", parent=self.supervisor
        )
        self.vendedor_2 = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 2", parent=self.supervisor
        )

        self.gerente_user = User.objects.create_user(
            username="gerente", password="x", hierarchy_node=self.gerente
        )
        self.local_user = User.objects.create_user(username="local", password="x", hierarchy_node=self.local)
        self.supervisor_user = User.objects.create_user(
            username="supervisor", password="x", hierarchy_node=self.supervisor
        )

        self.gerente_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.gerente,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=100,
            criado_por=self.gerente_user,
        )

    def _distribute_full_chain(self, vendedor_kg=100):
        (local_alloc,) = DistributeGoalService.distribute(
            self.gerente_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=self.local.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                )
            ],
            criado_por=self.gerente_user,
        )
        (self.supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_user,
        )
        DistributeGoalService.distribute(
            self.supervisor_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor.id,
                    quantity_kg=vendedor_kg,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.supervisor_user,
        )

    def test_flattens_the_chain_with_status_meta(self):
        self._distribute_full_chain()

        rows = VendedorAllocationReportService.rows_for_cycle(self.cycle)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.gerente_nome, "Fabio")
        self.assertEqual(row.local_nome, "Local")
        self.assertEqual(row.supervisor_nome, "Supervisor")
        self.assertEqual(row.vendedor_nome, "Vendedor")
        self.assertEqual(row.grupo_nome, "Embutidos")
        self.assertEqual(row.subgrupo_nome, "Linguiça")
        self.assertEqual(row.quantity_kg, 100)
        self.assertEqual(row.status, "META")

    def test_status_becomes_meta_ajustada_after_supervisor_reopens_and_redistributes(self):
        self._distribute_full_chain(vendedor_kg=100)

        # Reabre e reparte diferente entre os dois vendedores (mesmo total = 100, fechamento
        # intacto) — cenário de quebra de estoque: sobra de um vendedor vira falta do outro.
        ReopenAllocationService.reopen(self.supervisor_alloc, criado_por=self.supervisor_user)
        DistributeGoalService.distribute(
            self.supervisor_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor.id,
                    quantity_kg=60,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_2.id,
                    quantity_kg=40,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                ),
            ],
            criado_por=self.supervisor_user,
        )

        rows = {row.vendedor_nome: row for row in VendedorAllocationReportService.rows_for_cycle(self.cycle)}

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["Vendedor"].quantity_kg, 60)
        self.assertEqual(rows["Vendedor"].status, "META AJUSTADA")
        self.assertEqual(rows["Vendedor 2"].quantity_kg, 40)
        self.assertEqual(rows["Vendedor 2"].status, "META AJUSTADA")

    def test_status_propagates_when_an_ancestor_above_supervisor_is_reopened(self):
        self._distribute_full_chain()
        local_alloc = self.supervisor_alloc.parent_allocation

        # local_alloc só libera depois que o filho que já avançou (supervisor_alloc, já repassado
        # pro Vendedor) reseta a própria distribuição primeiro (trava nova, 2026-08-04).
        ReopenAllocationService.reopen(self.supervisor_alloc, criado_por=self.supervisor_user)
        ReopenAllocationService.reopen(local_alloc, criado_por=self.local_user)
        (new_supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_user,
        )
        DistributeGoalService.distribute(
            new_supervisor_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.supervisor_user,
        )

        rows = VendedorAllocationReportService.rows_for_cycle(self.cycle)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "META AJUSTADA")
