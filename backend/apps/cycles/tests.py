from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase

from apps.allocations.models import GoalAllocation
from apps.allocations.services import ChildAllocationSpec, CycleCompletenessChecker, DistributeGoalService
from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.hierarchy.models import HierarchyNode

from .models import Cycle
from .services import CloseCycleService, CycleAlreadyExistsError, CycleNotCompleteError, OpenCycleService

User = get_user_model()


class OpenCycleServiceTests(TestCase):
    def test_open_creates_cycle_in_aberto_status(self):
        cycle = OpenCycleService.open(2026, 9)

        self.assertEqual(cycle.ano, 2026)
        self.assertEqual(cycle.mes, 9)
        self.assertEqual(cycle.status, Cycle.Status.ABERTO)

    def test_open_rejects_month_that_already_has_a_cycle(self):
        Cycle.objects.create(ano=2026, mes=7)

        with self.assertRaises(CycleAlreadyExistsError):
            OpenCycleService.open(2026, 7)

    def test_open_rejects_duplicate_even_when_existing_cycle_is_closed(self):
        existing = Cycle.objects.create(ano=2026, mes=7, status=Cycle.Status.FECHADO)

        with self.assertRaises(CycleAlreadyExistsError):
            OpenCycleService.open(existing.ano, existing.mes)


class CloseCycleServiceTests(TestCase):
    def setUp(self):
        self.cycle = Cycle.objects.create(ano=2026, mes=7)
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguicas", group=self.group)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        self.vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor", parent=self.supervisor
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

    def _distribute_full_chain(self):
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
        (supervisor_alloc,) = DistributeGoalService.distribute(
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
            supervisor_alloc,
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

    def test_close_succeeds_when_fully_distributed_to_vendedor(self):
        self._distribute_full_chain()

        CloseCycleService.close(self.cycle)

        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.FECHADO)
        self.assertIsNotNone(self.cycle.closed_at)

    def test_close_rejects_when_allocation_stuck_at_intermediate_level(self):
        with self.assertRaises(CycleNotCompleteError):
            CloseCycleService.close(self.cycle)

        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.ABERTO)

    def test_close_rejects_when_already_closed(self):
        self._distribute_full_chain()
        CloseCycleService.close(self.cycle)

        with self.assertRaises(CycleNotCompleteError):
            CloseCycleService.close(self.cycle)

    def test_close_force_succeeds_despite_stuck_allocation(self):
        CloseCycleService.close(self.cycle, force=True)

        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.FECHADO)
        self.assertIsNotNone(self.cycle.closed_at)

    def test_close_force_still_rejects_when_already_closed(self):
        self._distribute_full_chain()
        CloseCycleService.close(self.cycle)

        with self.assertRaises(CycleNotCompleteError):
            CloseCycleService.close(self.cycle, force=True)


class EndToEndVendedorClosureTests(TestCase):
    """Critério de aceite do brief: a meta de um grupo, definida pelo Gerente, tem que chegar
    100% (sem sobra nem falta) na base do Vendedor, passando pelos 4 níveis e pela quebra
    grupo->subgrupo no Local, com ramificação real em subgrupo e em vendedores."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup_x = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.subgroup_y = ProductSubgroup.objects.create(nome="Salsicha", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=7)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        self.vendedor_1 = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 1", parent=self.supervisor
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

        self.total_kg = 1000
        self.gerente_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.gerente,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=self.total_kg,
            criado_por=self.gerente_user,
        )

    def _vendedor_total_kg(self) -> int:
        total = GoalAllocation.objects.filter(
            cycle=self.cycle, owner_node__level=HierarchyNode.Level.VENDEDOR
        ).aggregate(total=Sum("quantity_kg"))["total"]
        return total or 0

    def test_goal_reaches_100_percent_of_vendedor_base(self):
        (local_alloc,) = DistributeGoalService.distribute(
            self.gerente_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=self.local.id,
                    quantity_kg=self.total_kg,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                )
            ],
            criado_por=self.gerente_user,
        )

        # Local quebra grupo -> subgrupo: mesmo Supervisor recebe duas parcelas, uma por subgrupo.
        supervisor_x, supervisor_y = DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=400,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_y.id,
                ),
            ],
            criado_por=self.local_user,
        )

        # Supervisor distribui cada subgrupo entre os dois vendedores.
        DistributeGoalService.distribute(
            supervisor_x,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_1.id,
                    quantity_kg=350,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_2.id,
                    quantity_kg=250,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                ),
            ],
            criado_por=self.supervisor_user,
        )
        DistributeGoalService.distribute(
            supervisor_y,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_1.id,
                    quantity_kg=150,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_y.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_2.id,
                    quantity_kg=250,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_y.id,
                ),
            ],
            criado_por=self.supervisor_user,
        )

        # A soma de tudo que chegou nos Vendedores tem que ser exatamente a meta original.
        self.assertEqual(self._vendedor_total_kg(), self.total_kg)

        # Cada vendedor recebeu a soma das suas duas parcelas (subgrupo X + subgrupo Y).
        vendedor_1_total = GoalAllocation.objects.filter(owner_node=self.vendedor_1).aggregate(
            total=Sum("quantity_kg")
        )["total"]
        vendedor_2_total = GoalAllocation.objects.filter(owner_node=self.vendedor_2).aggregate(
            total=Sum("quantity_kg")
        )["total"]
        self.assertEqual(vendedor_1_total, 500)  # 350 + 150
        self.assertEqual(vendedor_2_total, 500)  # 250 + 250

        # A checagem formal de completude do produto concorda: nada preso, ciclo fecha.
        self.assertTrue(CycleCompletenessChecker.is_complete(self.cycle))
        self.assertEqual(CycleCompletenessChecker.stuck_allocations(self.cycle), [])

        CloseCycleService.close(self.cycle)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.FECHADO)

    def test_incomplete_branch_is_detected_and_blocks_close(self):
        # Distribui tudo até o Supervisor, mas "esquece" de repassar o subgrupo Y aos vendedores.
        (local_alloc,) = DistributeGoalService.distribute(
            self.gerente_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=self.local.id,
                    quantity_kg=self.total_kg,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                )
            ],
            criado_por=self.gerente_user,
        )
        supervisor_x, _supervisor_y = DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=400,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_y.id,
                ),
            ],
            criado_por=self.local_user,
        )
        DistributeGoalService.distribute(
            supervisor_x,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_1.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                )
            ],
            criado_por=self.supervisor_user,
        )
        # supervisor_y (400 kg) nunca é repassado aos vendedores — fica preso.

        self.assertEqual(self._vendedor_total_kg(), 600)  # só 60% da meta chegou na ponta
        self.assertFalse(CycleCompletenessChecker.is_complete(self.cycle))

        with self.assertRaises(CycleNotCompleteError):
            CloseCycleService.close(self.cycle)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.ABERTO)
