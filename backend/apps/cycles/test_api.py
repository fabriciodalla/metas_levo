from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.allocations.models import GoalAllocation
from apps.allocations.services import ChildAllocationSpec, DistributeGoalService
from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.hierarchy.models import HierarchyNode

from .models import Cycle

User = get_user_model()


class CycleApiTests(APITestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.cycle = Cycle.objects.create(ano=2026, mes=7)
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
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

    def _distribute_full_chain_to_vendedor(self):
        """Local->Supervisor->Vendedor sob self.gerente, tudo distribuído por
        self.user (dono de todos os nós — atalho válido, O5 é 1:N)."""
        subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=local
        )
        vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor", parent=supervisor
        )
        self.user.hierarchy_nodes.add(local, supervisor)

        (local_alloc,) = DistributeGoalService.distribute(
            self.allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=local.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                )
            ],
            criado_por=self.user,
        )
        (supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=subgroup.id,
                )
            ],
            criado_por=self.user,
        )
        DistributeGoalService.distribute(
            supervisor_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=vendedor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=subgroup.id,
                )
            ],
            criado_por=self.user,
        )

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("cycle-list"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_open_rejects_non_admin(self):
        response = self.client.post(reverse("cycle-open"), {"ano": 2026, "mes": 8})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_open_creates_cycle_for_admin(self):
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.post(reverse("cycle-open"), {"ano": 2026, "mes": 8})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["ano"], 2026)
        self.assertEqual(response.data["mes"], 8)
        self.assertEqual(response.data["status"], Cycle.Status.ABERTO)
        self.assertTrue(Cycle.objects.filter(ano=2026, mes=8).exists())

    def test_open_rejects_month_that_already_has_a_cycle(self):
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.post(reverse("cycle-open"), {"ano": self.cycle.ano, "mes": self.cycle.mes})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_completeness_rejects_non_admin(self):
        response = self.client.get(reverse("cycle-completeness", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_completeness_reports_stuck_allocation_for_admin(self):
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("cycle-completeness", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["complete"])
        self.assertEqual(len(response.data["stuck_allocations"]), 1)

    def test_close_rejects_non_admin(self):
        response = self.client.post(reverse("cycle-close", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.ABERTO)

    def test_close_rejects_incomplete_cycle(self):
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.post(reverse("cycle-close", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.ABERTO)

    def test_close_force_succeeds_despite_incomplete_cycle(self):
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.post(reverse("cycle-close", kwargs={"pk": self.cycle.pk}), {"force": True})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.FECHADO)

    def test_close_succeeds_when_complete(self):
        vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor", parent=self.gerente
        )
        DistributeGoalService.distribute(
            self.allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=vendedor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                )
            ],
            criado_por=self.user,
        )
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.post(reverse("cycle-close", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], Cycle.Status.FECHADO)

    def test_distribution_overview_rejects_non_admin(self):
        response = self.client.get(reverse("cycle-distribution-overview", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_distribution_overview_lists_allocation_owners_for_admin(self):
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("cycle-distribution-overview", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["owner_node_nome"], "Gerente")
        self.assertEqual(entry["owner_node_usernames"], ["gerente"])
        self.assertFalse(entry["distributed"])
        self.assertIsNone(entry["owner_node_parent_id"])
        self.assertIsNone(entry["owner_node_parent_nome"])
        self.assertEqual(entry["group_nome"], "Embutidos")
        self.assertIsNone(entry["subgroup_nome"])

    def test_distribution_overview_resolves_superior_and_group_for_subgroup_allocation(self):
        self._distribute_full_chain_to_vendedor()
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("cycle-distribution-overview", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        vendedor_entry = next(e for e in response.data if e["owner_node_nome"] == "Vendedor")
        self.assertEqual(vendedor_entry["owner_node_parent_nome"], "Supervisor")
        self.assertEqual(vendedor_entry["group_nome"], "Embutidos")
        self.assertEqual(vendedor_entry["subgroup_nome"], "Linguiça")

    def test_export_rejects_non_admin(self):
        response = self.client.get(reverse("cycle-export", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_export_returns_csv_with_vendedor_rows(self):
        self._distribute_full_chain_to_vendedor()
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("cycle-export", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode("utf-8")
        header, row, *_ = body.splitlines()
        self.assertEqual(
            header,
            "gerente,coordenador_local,supervisor,vendedor,grupo,subgrupo,meta_kg,ciclo,status",
        )
        self.assertEqual(row, "Gerente,Local,Supervisor,Vendedor,Embutidos,Linguiça,100,07/2026,META")

    def test_vendedor_report_rejects_non_admin(self):
        response = self.client.get(reverse("cycle-vendedor-report", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_vendedor_report_returns_flattened_rows_for_admin(self):
        self._distribute_full_chain_to_vendedor()
        admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("cycle-vendedor-report", kwargs={"pk": self.cycle.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row["gerente"], "Gerente")
        self.assertEqual(row["local"], "Local")
        self.assertEqual(row["supervisor"], "Supervisor")
        self.assertEqual(row["vendedor"], "Vendedor")
        self.assertEqual(row["grupo"], "Embutidos")
        self.assertEqual(row["subgrupo"], "Linguiça")
        self.assertEqual(row["quantity_kg"], 100)
        self.assertEqual(row["status"], "META")
