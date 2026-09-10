from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import HierarchyNode

from .models import GoalAllocation

User = get_user_model()


class GoalAllocationApiTests(APITestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.cycle = Cycle.objects.create(ano=2026, mes=7)
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )
        self.user = User.objects.create_user(username="gerente", password="x", hierarchy_node=self.gerente)
        self.allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.gerente,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=100,
            criado_por=self.user,
        )
        self.client.force_login(self.user)

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("goal-allocation-list"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_retrieve_outside_scope_returns_404(self):
        other_node = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Outro")
        other_user = User.objects.create_user(username="outro", password="x", hierarchy_node=other_node)
        other_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=other_node,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=10,
            criado_por=other_user,
        )

        response = self.client.get(reverse("goal-allocation-detail", kwargs={"pk": other_allocation.pk}))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_distribute_rejects_when_user_does_not_own_parent_via_api(self):
        # local_a é descendente de gerente, então a alocação é *visível* para self.user
        # (owner_node=gerente) via visible_to — mas ele não é o dono direto dela. Isso exercita
        # o AllocationScopeError do service através da view (visível != dono), diferente do 404
        # de test_retrieve_outside_scope_returns_404 (fora do escopo, nem visível).
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A1", parent=self.local_a
        )
        local_a_user = User.objects.create_user(username="local_a", password="x", hierarchy_node=self.local_a)
        local_a_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local_a,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=60,
            criado_por=local_a_user,
        )

        payload = {
            "children": [
                {
                    "owner_node_id": supervisor.id,
                    "quantity_kg": 60,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
            ]
        }

        response = self.client.post(
            reverse("goal-allocation-distribute", kwargs={"pk": local_a_allocation.pk}),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        local_a_allocation.refresh_from_db()
        self.assertFalse(local_a_allocation.distributed)

    def test_distribute_succeeds_and_returns_created_children(self):
        payload = {
            "children": [
                {
                    "owner_node_id": self.local_a.id,
                    "quantity_kg": 60,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
                {
                    "owner_node_id": self.local_b.id,
                    "quantity_kg": 40,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
            ]
        }

        response = self.client.post(
            reverse("goal-allocation-distribute", kwargs={"pk": self.allocation.pk}), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data), 2)
        self.allocation.refresh_from_db()
        self.assertTrue(self.allocation.distributed)

    def test_distribute_rejects_mismatched_sum(self):
        payload = {
            "children": [
                {
                    "owner_node_id": self.local_a.id,
                    "quantity_kg": 60,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
                {
                    "owner_node_id": self.local_b.id,
                    "quantity_kg": 30,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
            ]
        }

        response = self.client.post(
            reverse("goal-allocation-distribute", kwargs={"pk": self.allocation.pk}), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reopen_via_api_deletes_children_and_resets_distributed(self):
        distribute_payload = {
            "children": [
                {
                    "owner_node_id": self.local_a.id,
                    "quantity_kg": 60,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
                {
                    "owner_node_id": self.local_b.id,
                    "quantity_kg": 40,
                    "granularity": "GROUP",
                    "group_id": self.group.id,
                },
            ]
        }
        self.client.post(
            reverse("goal-allocation-distribute", kwargs={"pk": self.allocation.pk}),
            distribute_payload,
            format="json",
        )
        child_ids = list(
            GoalAllocation.objects.filter(parent_allocation=self.allocation).values_list("id", flat=True)
        )
        self.assertEqual(len(child_ids), 2)

        response = self.client.post(reverse("goal-allocation-reopen", kwargs={"pk": self.allocation.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["distributed"])
        self.allocation.refresh_from_db()
        self.assertFalse(self.allocation.distributed)
        self.assertFalse(GoalAllocation.objects.filter(id__in=child_ids).exists())

    def test_reopen_via_api_rejects_when_not_yet_distributed(self):
        response = self.client.post(reverse("goal-allocation-reopen", kwargs={"pk": self.allocation.pk}))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_only_shows_allocations_in_own_branch(self):
        other_node = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Outro")
        other_user = User.objects.create_user(username="outro", password="x", hierarchy_node=other_node)
        GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=other_node,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=10,
            criado_por=other_user,
        )

        response = self.client.get(reverse("goal-allocation-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data}
        self.assertEqual(ids, {self.allocation.id})

    def test_has_further_distribution_ignores_a_zero_kg_distributed_child(self):
        """Um filho distribuído com 0 kg (típico de `SelfVendedorAutoDistributionService`
        cascateando autogestão mesmo sem quantidade nenhuma) não é trabalho real a proteger — não
        deve travar o botão "Resetar distribuição" do pai (revisão 2026-09-03)."""
        GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local_a,
            parent_allocation=self.allocation,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=0,
            distributed=True,
            criado_por=self.user,
        )

        response = self.client.get(reverse("goal-allocation-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {item["id"]: item for item in response.data}
        self.assertFalse(by_id[self.allocation.id]["has_further_distribution"])

    def test_has_further_distribution_still_blocks_on_a_real_distributed_child(self):
        GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local_a,
            parent_allocation=self.allocation,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=100,
            distributed=True,
            criado_por=self.user,
        )

        response = self.client.get(reverse("goal-allocation-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {item["id"]: item for item in response.data}
        self.assertTrue(by_id[self.allocation.id]["has_further_distribution"])

    def test_has_further_distribution_ignores_a_self_managed_cascade_with_real_quantity(self):
        """Achado real (2026-09-03): um Supervisor em autogestão (único Vendedor ativo, mesmo
        nome) recebe um repasse automático COM quantidade real — não só 0 kg — e isso também não
        é decisão de ninguém (só existe 1 alvo possível), então não deve travar o reset do pai."""
        local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Fulano Da Silva", parent=local
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva", parent=supervisor
        )
        supervisor_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=supervisor,
            parent_allocation=self.allocation,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=500,
            distributed=True,
            criado_por=self.user,
        )
        GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=HierarchyNode.objects.get(nome="Fulano Da Silva", level=HierarchyNode.Level.VENDEDOR),
            parent_allocation=supervisor_allocation,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=500,
            distributed=False,
            criado_por=self.user,
        )

        response = self.client.get(reverse("goal-allocation-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {item["id"]: item for item in response.data}
        self.assertFalse(by_id[self.allocation.id]["has_further_distribution"])


class ResetGroupApiTests(APITestCase):
    """`POST /allocations/reset-group/` — telas "Meta Supervisor"/"Meta Vendedor": reseta de uma
    vez todos os subgrupos já distribuídos de um grupo (pedido explícito do usuário, 2026-09-03)."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup_x = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.subgroup_y = ProductSubgroup.objects.create(nome="Salsicha", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=7)

        self.local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local")
        self.user = User.objects.create_user(username="local", password="x", hierarchy_node=self.local)
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 1", parent=self.supervisor
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 2", parent=self.supervisor
        )

        self.alloc_x = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_x,
            quantity_kg=1000,
            criado_por=self.user,
        )
        self.alloc_y = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_y,
            quantity_kg=500,
            criado_por=self.user,
        )
        self.client.force_login(self.user)

    def _distribute_both_to_supervisor(self):
        for allocation, qty in ((self.alloc_x, 1000), (self.alloc_y, 500)):
            self.client.post(
                reverse("goal-allocation-distribute", kwargs={"pk": allocation.pk}),
                {
                    "children": [
                        {
                            "owner_node_id": self.supervisor.id,
                            "quantity_kg": qty,
                            "granularity": "SUBGROUP",
                            "subgroup_id": allocation.subgroup_id,
                        }
                    ]
                },
                format="json",
            )

    def test_resets_all_subgroups_of_the_group_via_api(self):
        self._distribute_both_to_supervisor()

        response = self.client.post(
            reverse("goal-allocation-reset-group"),
            {"cycle_id": self.cycle.id, "owner_node_id": self.local.id, "group_id": self.group.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({item["id"] for item in response.data}, {self.alloc_x.id, self.alloc_y.id})
        self.alloc_x.refresh_from_db()
        self.alloc_y.refresh_from_db()
        self.assertFalse(self.alloc_x.distributed)
        self.assertFalse(self.alloc_y.distributed)

    def test_rejects_when_a_subgroup_has_real_downstream_work(self):
        self._distribute_both_to_supervisor()
        supervisor_alloc_x = GoalAllocation.objects.get(owner_node=self.supervisor, subgroup=self.subgroup_x)
        vendedor_1 = HierarchyNode.objects.get(nome="Vendedor 1")
        vendedor_2 = HierarchyNode.objects.get(nome="Vendedor 2")
        self.client.force_login(
            User.objects.create_user(username="sup", password="x", hierarchy_node=self.supervisor)
        )
        self.client.post(
            reverse("goal-allocation-distribute", kwargs={"pk": supervisor_alloc_x.pk}),
            {
                "children": [
                    {
                        "owner_node_id": vendedor_1.id,
                        "quantity_kg": 600,
                        "granularity": "SUBGROUP",
                        "subgroup_id": self.subgroup_x.id,
                    },
                    {
                        "owner_node_id": vendedor_2.id,
                        "quantity_kg": 400,
                        "granularity": "SUBGROUP",
                        "subgroup_id": self.subgroup_x.id,
                    },
                ]
            },
            format="json",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("goal-allocation-reset-group"),
            {"cycle_id": self.cycle.id, "owner_node_id": self.local.id, "group_id": self.group.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Supervisor", response.data["detail"])
        self.alloc_x.refresh_from_db()
        self.assertTrue(self.alloc_x.distributed)

    def test_rejects_when_caller_does_not_own_the_node(self):
        self._distribute_both_to_supervisor()
        other_user = User.objects.create_user(username="other", password="x")
        self.client.force_login(other_user)

        response = self.client.post(
            reverse("goal-allocation-reset-group"),
            {"cycle_id": self.cycle.id, "owner_node_id": self.local.id, "group_id": self.group.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
