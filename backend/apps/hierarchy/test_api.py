from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.allocations.models import GoalAllocation
from apps.allocations.services import ChildAllocationSpec, DistributeGoalService
from apps.catalog.models import ProductGroup
from apps.cycles.models import Cycle

from .models import FeristaCoverage, HierarchyNode

User = get_user_model()


class HierarchyNodeApiTests(APITestCase):
    def setUp(self):
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )
        self.user_a = User.objects.create_user(username="user_a", password="x", hierarchy_node=self.local_a)

    def test_requires_authentication(self):
        response = self.client.get(reverse("hierarchy-node-list"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_lists_only_own_branch(self):
        self.client.force_login(self.user_a)

        response = self.client.get(reverse("hierarchy-node-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data}
        self.assertEqual(ids, {self.local_a.id})
        self.assertNotIn(self.local_b.id, ids)


class HierarchyNodeAdminApiTests(APITestCase):
    """CRUD de hierarquia pela SPA (Decisão 4 revista) — antes só existia via Django Admin,
    ver test_admin.py para os testes equivalentes daquele caminho."""

    def setUp(self):
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.plain_user = User.objects.create_user(username="user", password="x", hierarchy_node=self.local)

    def test_non_admin_cannot_create_node(self):
        self.client.force_login(self.plain_user)

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {"level": HierarchyNode.Level.SUPERVISOR, "nome": "Supervisor X", "parent": self.local.id},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_node_with_valid_parent_level(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {"level": HierarchyNode.Level.SUPERVISOR, "nome": "Supervisor X", "parent": self.local.id},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(HierarchyNode.objects.filter(nome="Supervisor X", parent=self.local).exists())

    def test_rejects_parent_of_wrong_level(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {"level": HierarchyNode.Level.VENDEDOR, "nome": "Vendedor X", "parent": self.local.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_gerente_with_parent(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {"level": HierarchyNode.Level.GERENTE, "nome": "Outro Gerente", "parent": self.local.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_node_as_its_own_parent(self):
        """A API (diferente do Django Admin) não chamava `full_clean()` — um nó podia virar pai
        de si mesmo sem barrar (achado investigando um bug real de duplicação de nó, 2026-07-22)."""
        self.client.force_login(self.admin)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )

        response = self.client.patch(
            reverse("hierarchy-node-detail", args=[supervisor.id]), {"parent": supervisor.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        supervisor.refresh_from_db()
        self.assertEqual(supervisor.parent_id, self.local.id)

    def test_rejects_non_gerente_without_parent(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {"level": HierarchyNode.Level.LOCAL, "nome": "Local Órfão"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivating_node_with_legacy_invalid_parent_still_succeeds(self):
        """Um PATCH que só muda `ativo` não pode ser bloqueado por uma relação nível/pai
        inconsistente herdada de antes (ex.: sobra de um cargo removido) — a validação só deve
        reexaminar nível/pai quando um dos dois está de fato no payload."""
        self.client.force_login(self.admin)
        # Nível/pai inconsistente (VENDEDOR com pai LOCAL, deveria ser SUPERVISOR) — o model não
        # impede isso, só o serializer; simula um resto de dado já existente no banco.
        inconsistent = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Órfão Inconsistente", parent=self.local
        )

        response = self.client.patch(
            reverse("hierarchy-node-detail", args=[inconsistent.id]), {"ativo": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        inconsistent.refresh_from_db()
        self.assertFalse(inconsistent.ativo)

    def test_admin_can_create_representante_node(self):
        """Representante: Vendedor sem usuário vinculado por design (sem acesso ao sistema), mas
        que participa normalmente do acumulado/distribuição de meta."""
        self.client.force_login(self.admin)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {
                "level": HierarchyNode.Level.VENDEDOR,
                "nome": "Representante X",
                "parent": supervisor.id,
                "is_representante": True,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        node = HierarchyNode.objects.get(nome="Representante X")
        self.assertTrue(node.is_representante)
        self.assertEqual(node.users.count(), 0)

    def test_rejects_representante_at_non_vendedor_level(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("hierarchy-node-list"),
            {
                "level": HierarchyNode.Level.SUPERVISOR,
                "nome": "Supervisor Representante?",
                "parent": self.local.id,
                "is_representante": True,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_marking_occupied_node_as_representante(self):
        self.client.force_login(self.admin)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor Ocupado", parent=supervisor
        )
        User.objects.create_user(username="ocupante", password="x", hierarchy_node=vendedor)

        response = self.client.patch(
            reverse("hierarchy-node-detail", args=[vendedor.id]), {"is_representante": True}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivating_via_api_triggers_reassignment(self):
        group = ProductGroup.objects.create(nome="Embutidos")
        cycle = Cycle.objects.create(ano=2026, mes=7)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        gerente_user = User.objects.create_user(username="gerente", password="x", hierarchy_node=self.gerente)
        local_user = User.objects.create_user(username="local", password="x", hierarchy_node=self.local)
        gerente_alloc = GoalAllocation.objects.create(
            cycle=cycle,
            owner_node=self.gerente,
            granularity=GoalAllocation.Granularity.GROUP,
            group=group,
            quantity_kg=100,
            criado_por=gerente_user,
        )
        (local_alloc,) = DistributeGoalService.distribute(
            gerente_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=self.local.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=group.id,
                )
            ],
            criado_por=gerente_user,
        )
        DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=group.id,
                )
            ],
            criado_por=local_user,
        )

        self.client.force_login(self.admin)
        response = self.client.patch(
            reverse("hierarchy-node-detail", args=[supervisor.id]), {"ativo": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        local_alloc.refresh_from_db()
        self.assertFalse(local_alloc.distributed)


class FeristaCoverageApiTests(APITestCase):
    """Função do Administrador (Decisão 13, revisão 2026-09-10): cadastrar quem cobre quem, em
    qual mês — ferista e titular são ambos Vendedores normais da hierarquia."""

    def setUp(self):
        self.vendedor = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Titular")
        self.ferista = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Ferista")
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor"
        )
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.plain_user = User.objects.create_user(
            username="user", password="x", hierarchy_node=self.vendedor
        )

    def test_non_admin_cannot_create_coverage(self):
        self.client.force_login(self.plain_user)

        response = self.client.post(
            reverse("ferista-coverage-list"),
            {"covering_node": self.ferista.id, "covered_node": self.vendedor.id, "ano": 2026, "mes": 3},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_any_authenticated_user_can_list(self):
        self.client.force_login(self.plain_user)

        response = self.client.get(reverse("ferista-coverage-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_can_create_coverage(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("ferista-coverage-list"),
            {"covering_node": self.ferista.id, "covered_node": self.vendedor.id, "ano": 2026, "mes": 3},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(FeristaCoverage.objects.filter(covering_node=self.ferista).exists())

    def test_rejects_covered_node_that_is_not_vendedor(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("ferista-coverage-list"),
            {"covering_node": self.ferista.id, "covered_node": self.supervisor.id, "ano": 2026, "mes": 3},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_covering_node_that_is_not_vendedor(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("ferista-coverage-list"),
            {"covering_node": self.supervisor.id, "covered_node": self.vendedor.id, "ano": 2026, "mes": 3},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_allows_same_ferista_covering_a_second_titular_in_the_same_month(self):
        self.client.force_login(self.admin)
        other_titular = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Outro Titular")
        FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=self.vendedor, ano=2026, mes=3
        )

        response = self.client.post(
            reverse("ferista-coverage-list"),
            {"covering_node": self.ferista.id, "covered_node": other_titular.id, "ano": 2026, "mes": 3},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            FeristaCoverage.objects.filter(covering_node=self.ferista, ano=2026, mes=3).count(), 2
        )

    def test_rejects_titular_covered_by_two_feristas_in_the_same_month(self):
        self.client.force_login(self.admin)
        other_ferista = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Outro Ferista")
        FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=self.vendedor, ano=2026, mes=3
        )

        response = self.client.post(
            reverse("ferista-coverage-list"),
            {"covering_node": other_ferista.id, "covered_node": self.vendedor.id, "ano": 2026, "mes": 3},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_delete_coverage(self):
        self.client.force_login(self.admin)
        coverage = FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=self.vendedor, ano=2026, mes=3
        )

        response = self.client.delete(reverse("ferista-coverage-detail", args=[coverage.id]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(FeristaCoverage.objects.filter(id=coverage.id).exists())
