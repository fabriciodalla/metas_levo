from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import HierarchyNode

from .models import GoalAllocation
from .services import ChildAllocationSpec, DistributeGoalService

User = get_user_model()


class SelfVendedorAutoDistributionTests(TestCase):
    """Pedido explícito do usuário (2026-08-07): quando um Supervisor tem exatamente um Vendedor
    ativo sob ele, com o mesmo nome (autogestão — a mesma pessoa nas duas posições), repassar pra
    ele não é uma decisão real (só existe um alvo, sempre 100%) — a alocação do Vendedor fecha
    sozinha, no mesmo instante em que o Coordenador distribui pro Supervisor, sem passar pela tela
    "Meta Vendedor"."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=7)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.regional = HierarchyNode.objects.create(
            level=HierarchyNode.Level.REGIONAL, nome="Regional", parent=self.gerente
        )
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.regional
        )
        self.coordenador = User.objects.create_user(username="coord", password="x", hierarchy_node=self.local)

        self.parent_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup,
            quantity_kg=1000,
            criado_por=self.coordenador,
        )

    def _make_supervisor(self, nome: str) -> HierarchyNode:
        return HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome=nome, parent=self.local
        )

    def test_cascades_automatically_when_supervisor_has_a_single_self_named_vendedor(self):
        supervisor = self._make_supervisor("Fulano Da Silva")
        vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva", parent=supervisor
        )

        created = DistributeGoalService.distribute(
            self.parent_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=1000,
                    granularity="SUBGROUP",
                    subgroup_id=self.subgroup.id,
                )
            ],
            self.coordenador,
        )

        supervisor_allocation = created[0]
        supervisor_allocation.refresh_from_db()
        self.assertTrue(supervisor_allocation.distributed)

        vendedor_allocation = GoalAllocation.objects.get(owner_node=vendedor)
        self.assertEqual(vendedor_allocation.quantity_kg, 1000)
        self.assertEqual(vendedor_allocation.parent_allocation_id, supervisor_allocation.id)
        self.assertFalse(vendedor_allocation.distributed)
        self.assertEqual(vendedor_allocation.criado_por, self.coordenador)

    def test_does_not_cascade_when_vendedor_name_differs(self):
        supervisor = self._make_supervisor("Fulano Da Silva")
        HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Outro Nome", parent=supervisor)

        created = DistributeGoalService.distribute(
            self.parent_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=1000,
                    granularity="SUBGROUP",
                    subgroup_id=self.subgroup.id,
                )
            ],
            self.coordenador,
        )

        self.assertFalse(created[0].distributed)
        self.assertFalse(
            GoalAllocation.objects.filter(owner_node__level=HierarchyNode.Level.VENDEDOR).exists()
        )

    def test_does_not_cascade_when_supervisor_has_no_vendedor(self):
        supervisor = self._make_supervisor("Fulano Da Silva")

        created = DistributeGoalService.distribute(
            self.parent_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=1000,
                    granularity="SUBGROUP",
                    subgroup_id=self.subgroup.id,
                )
            ],
            self.coordenador,
        )

        self.assertFalse(created[0].distributed)

    def test_does_not_cascade_when_supervisor_has_more_than_one_active_vendedor(self):
        supervisor = self._make_supervisor("Fulano Da Silva")
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva", parent=supervisor
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Segundo Vendedor", parent=supervisor
        )

        created = DistributeGoalService.distribute(
            self.parent_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=1000,
                    granularity="SUBGROUP",
                    subgroup_id=self.subgroup.id,
                )
            ],
            self.coordenador,
        )

        self.assertFalse(created[0].distributed)
        self.assertFalse(
            GoalAllocation.objects.filter(owner_node__level=HierarchyNode.Level.VENDEDOR).exists()
        )

    def test_does_not_cascade_to_an_inactive_same_named_vendedor(self):
        supervisor = self._make_supervisor("Fulano Da Silva")
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva", parent=supervisor, ativo=False
        )

        created = DistributeGoalService.distribute(
            self.parent_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=1000,
                    granularity="SUBGROUP",
                    subgroup_id=self.subgroup.id,
                )
            ],
            self.coordenador,
        )

        self.assertFalse(created[0].distributed)
        self.assertFalse(
            GoalAllocation.objects.filter(owner_node__level=HierarchyNode.Level.VENDEDOR).exists()
        )

    def test_does_not_cascade_for_non_supervisor_targets(self):
        """Regional->Local, por exemplo: o filho recém-criado é dono de um nó LOCAL, não
        SUPERVISOR — a autogestão nunca se aplica fora do nível Supervisor."""
        local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.regional
        )
        regional_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.regional,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup,
            quantity_kg=1000,
            criado_por=self.coordenador,
        )
        coordenador_regional = User.objects.create_user(
            username="coord_regional", password="x", hierarchy_node=self.regional
        )

        created = DistributeGoalService.distribute(
            regional_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=local_b.id,
                    quantity_kg=1000,
                    granularity="SUBGROUP",
                    subgroup_id=self.subgroup.id,
                )
            ],
            coordenador_regional,
        )

        self.assertFalse(created[0].distributed)
