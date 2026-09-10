from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import ExternalProductMapping, ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.hierarchy.models import ExternalSalespersonMapping, HierarchyNode
from apps.sales_history.models import DistributionBaseline

from .models import GoalAllocation
from .services import (
    AllocationClosureError,
    AllocationScopeError,
    DistributionContextService,
    SplitGroupIntoSubgroupsService,
    SubgroupDistributionContextService,
    SubgroupSplitSpec,
)

User = get_user_model()


def _baseline(ano: int, mes: int, salesperson_name: str, subgroup_name: str, quantity: float) -> None:
    DistributionBaseline.objects.create(
        ano=ano,
        mes=mes,
        salesperson_name=salesperson_name,
        subgroup_name=subgroup_name,
        total_quantity=quantity,
    )


class SubgroupSupervisorContextTestsBase(TestCase):
    """Hierarquia/histórico compartilhados: um Coordenador Local com dois Supervisores, cada um
    com um Vendedor, vendendo dois subgrupos do mesmo grupo em proporção 75/25 tanto entre
    subgrupos (Linguiça/Salsicha) quanto entre Supervisores (A/B) — mesma proporção nos dois eixos
    de propósito, pra deixar as duas fórmulas fáceis de distinguir e conferir nos testes."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup_linguica = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.subgroup_salsicha = ProductSubgroup.objects.create(nome="Salsicha", group=self.group)
        ExternalProductMapping.objects.create(external_code="LINGUICA", subgroup=self.subgroup_linguica)
        ExternalProductMapping.objects.create(external_code="SALSICHA", subgroup=self.subgroup_salsicha)

        self.cycle = Cycle.objects.create(ano=2026, mes=1)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        self.supervisor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A", parent=self.local
        )
        self.supervisor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor B", parent=self.local
        )
        vendedor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor A", parent=self.supervisor_a
        )
        vendedor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor B", parent=self.supervisor_b
        )
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR A", hierarchy_node=vendedor_a)
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR B", hierarchy_node=vendedor_b)

        # 12 meses planos (sem tendência). Por subgrupo (somando os dois Supervisores):
        # Linguiça = 225+75 = 300/mês, Salsicha = 75+25 = 100/mês -> 75/25 entre subgrupos.
        # Por Supervisor (somando os dois subgrupos): A = 225+75 = 300/mês, B = 75+25 = 100/mês
        # -> 75/25 entre Supervisores também, com histórico sempre do GRUPO inteiro (nunca só de
        # um subgrupo), como manda a Decisão 6.
        for mes in range(1, 13):
            _baseline(2025, mes, "VENDEDOR A", "LINGUICA", 225)
            _baseline(2025, mes, "VENDEDOR A", "SALSICHA", 75)
            _baseline(2025, mes, "VENDEDOR B", "LINGUICA", 75)
            _baseline(2025, mes, "VENDEDOR B", "SALSICHA", 25)


class SubgroupDistributionContextServiceTests(SubgroupSupervisorContextTestsBase):
    """Etapa 1: quebra da meta GROUP do Coordenador Local em subgrupos — peso por
    `RecentAverageDistributionStrategy` (Decisão 6, revisão 2026-09-03): proporção pela média dos
    últimos 3 meses de cada subgrupo, com piso de 0,5% de participação (quem fica abaixo é
    zerado e redistribuído entre os demais)."""

    def test_suggests_split_weighted_by_subgroup_history_closing_exactly_with_parent(self):
        allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=1000,
            criado_por=User.objects.create_user(username="local1", password="x"),
        )

        contexts = SubgroupDistributionContextService.build(allocation)
        by_subgroup = {ctx.subgroup_id: ctx for ctx in contexts}

        self.assertEqual(set(by_subgroup), {self.subgroup_linguica.id, self.subgroup_salsicha.id})
        self.assertEqual(by_subgroup[self.subgroup_linguica.id].suggested_kg, 750)
        self.assertEqual(by_subgroup[self.subgroup_salsicha.id].suggested_kg, 250)
        self.assertEqual(
            by_subgroup[self.subgroup_linguica.id].suggested_kg
            + by_subgroup[self.subgroup_salsicha.id].suggested_kg,
            1000,
        )
        self.assertEqual(by_subgroup[self.subgroup_linguica.id].subgroup_nome, "Linguiça")
        self.assertAlmostEqual(by_subgroup[self.subgroup_linguica.id].historical_share_pct, 75.0)
        self.assertAlmostEqual(by_subgroup[self.subgroup_salsicha.id].historical_share_pct, 25.0)
        self.assertFalse(by_subgroup[self.subgroup_linguica.id].has_gap)

    def test_zeroes_subgroups_below_min_share_and_redistributes_to_the_rest(self):
        # Terceiro subgrupo do mesmo grupo com venda residual (1kg/mês, ~0,25% do total de
        # 401kg/mês do grupo) — abaixo do piso de 0,5%, deve ficar zerado, e os 1000kg da meta
        # continuam fechando 100% entre Linguiça/Salsicha (75/25), sem sobrar nada pro residual.
        subgroup_mortadela = ProductSubgroup.objects.create(nome="Mortadela", group=self.group)
        ExternalProductMapping.objects.create(external_code="MORTADELA", subgroup=subgroup_mortadela)
        for mes in range(1, 13):
            _baseline(2025, mes, "VENDEDOR A", "MORTADELA", 1)

        allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=1000,
            criado_por=User.objects.create_user(username="local8", password="x"),
        )

        contexts = SubgroupDistributionContextService.build(allocation)
        by_subgroup = {ctx.subgroup_id: ctx for ctx in contexts}

        self.assertEqual(by_subgroup[subgroup_mortadela.id].suggested_kg, 0)
        self.assertEqual(by_subgroup[self.subgroup_linguica.id].suggested_kg, 750)
        self.assertEqual(by_subgroup[self.subgroup_salsicha.id].suggested_kg, 250)
        self.assertEqual(
            by_subgroup[self.subgroup_linguica.id].suggested_kg
            + by_subgroup[self.subgroup_salsicha.id].suggested_kg
            + by_subgroup[subgroup_mortadela.id].suggested_kg,
            1000,
        )

    def test_splits_equally_when_no_history_for_any_subgroup(self):
        # Sem histórico algum, mas único subgrupo no grupo: a garantia de "sempre existe uma
        # sugestão" (RecentAverageDistributionStrategy, 2026-09-03) faz esse único alvo receber o
        # total inteiro, em vez de degradar pra "sem sugestão".
        group_without_history = ProductGroup.objects.create(nome="Laticínios")
        subgroup_without_history = ProductSubgroup.objects.create(nome="Queijo", group=group_without_history)
        ExternalProductMapping.objects.create(external_code="QUEIJO", subgroup=subgroup_without_history)

        allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.GROUP,
            group=group_without_history,
            quantity_kg=500,
            criado_por=User.objects.create_user(username="local2", password="x"),
        )

        contexts = SubgroupDistributionContextService.build(allocation)

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].suggested_kg, 500)

    def test_returns_empty_when_allocation_has_no_group(self):
        subgroup_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_linguica,
            quantity_kg=1000,
            criado_por=User.objects.create_user(username="local3", password="x"),
        )

        self.assertEqual(SubgroupDistributionContextService.build(subgroup_allocation), [])


class SplitGroupIntoSubgroupsServiceTests(SubgroupSupervisorContextTestsBase):
    """Tela "Distribuir Produtos": persiste a quebra da meta GROUP do Coordenador Local em metas
    SUBGROUP, permanecendo dona do mesmo nó Local."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="local4", password="x", hierarchy_node=self.local)
        self.allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=1000,
            criado_por=self.user,
        )

    def test_persists_children_owned_by_same_node_and_closes_exactly(self):
        specs = [
            SubgroupSplitSpec(subgroup_id=self.subgroup_linguica.id, quantity_kg=750),
            SubgroupSplitSpec(subgroup_id=self.subgroup_salsicha.id, quantity_kg=250),
        ]

        created = SplitGroupIntoSubgroupsService.split(self.allocation, specs, criado_por=self.user)
        by_subgroup = {c.subgroup_id: c for c in created}

        self.assertEqual(len(created), 2)
        self.assertTrue(all(c.owner_node_id == self.local.id for c in created))
        self.assertTrue(all(c.granularity == GoalAllocation.Granularity.SUBGROUP for c in created))
        self.assertTrue(all(c.parent_allocation_id == self.allocation.id for c in created))
        self.assertEqual(by_subgroup[self.subgroup_linguica.id].quantity_kg, 750)
        self.assertEqual(by_subgroup[self.subgroup_salsicha.id].quantity_kg, 250)

        self.allocation.refresh_from_db()
        self.assertTrue(self.allocation.distributed)

    def test_rejects_when_sum_does_not_close_with_parent(self):
        specs = [SubgroupSplitSpec(subgroup_id=self.subgroup_linguica.id, quantity_kg=999)]

        with self.assertRaises(AllocationClosureError):
            SplitGroupIntoSubgroupsService.split(self.allocation, specs, criado_por=self.user)

    def test_rejects_subgroup_not_belonging_to_allocation_group(self):
        other_group = ProductGroup.objects.create(nome="Laticínios")
        other_subgroup = ProductSubgroup.objects.create(nome="Queijo", group=other_group)
        specs = [SubgroupSplitSpec(subgroup_id=other_subgroup.id, quantity_kg=1000)]

        with self.assertRaises(AllocationScopeError):
            SplitGroupIntoSubgroupsService.split(self.allocation, specs, criado_por=self.user)

    def test_rejects_when_owner_level_is_not_local(self):
        gerente_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.gerente,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=1000,
            criado_por=User.objects.create_user(
                username="gerente5", password="x", hierarchy_node=self.gerente
            ),
        )
        specs = [SubgroupSplitSpec(subgroup_id=self.subgroup_linguica.id, quantity_kg=1000)]

        with self.assertRaises(AllocationScopeError):
            SplitGroupIntoSubgroupsService.split(
                gerente_allocation, specs, criado_por=gerente_allocation.criado_por
            )

    def test_rejects_when_caller_does_not_own_the_allocation(self):
        other_user = User.objects.create_user(username="other", password="x")
        specs = [SubgroupSplitSpec(subgroup_id=self.subgroup_linguica.id, quantity_kg=1000)]

        with self.assertRaises(AllocationScopeError):
            SplitGroupIntoSubgroupsService.split(self.allocation, specs, criado_por=other_user)


class DistributionContextServiceOnSubgroupTests(SubgroupSupervisorContextTestsBase):
    """A tela "Meta Supervisor"/"Meta Vendedor" chama `DistributionContextService.build()` numa
    alocação SUBGROUP já persistida (dona = Local ou Supervisor), resolvendo o grupo via
    `subgroup.group_id` em vez de `allocation.group_id`. Desde a Decisão 6 (revisão 2026-09-02),
    o peso da sugestão vem do histórico daquele SUBGRUPO específico, não do grupo inteiro."""

    def test_suggests_split_weighted_by_subgroup_history_not_full_group(self):
        # Terceiro subgrupo do mesmo grupo, só pra empurrar o Supervisor B pra dominar o GRUPO
        # inteiro (900/mês a mais, muito acima dos 300/mês do Supervisor A) — SALSICHA continua
        # exatamente como no fixture-base (A=75/mês, B=25/mês, proporção 75/25). Se a sugestão
        # ainda fechar 75/25 (300/100 de 400kg) mesmo com o grupo claramente puxando pra B, é
        # porque o peso realmente vem do SUBGRUPO — se ainda fosse do grupo inteiro, B levaria a
        # maior parte (o grupo como um todo favorece B por larga margem).
        subgroup_presunto = ProductSubgroup.objects.create(nome="Presunto", group=self.group)
        ExternalProductMapping.objects.create(external_code="PRESUNTO", subgroup=subgroup_presunto)
        for mes in range(1, 13):
            _baseline(2025, mes, "VENDEDOR B", "PRESUNTO", 900)

        subgroup_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_salsicha,
            quantity_kg=400,
            criado_por=User.objects.create_user(username="local6", password="x"),
        )

        contexts = DistributionContextService.build(subgroup_allocation)
        by_node = {ctx.owner_node_id: ctx for ctx in contexts}

        self.assertEqual(set(by_node), {self.supervisor_a.id, self.supervisor_b.id})
        self.assertEqual(by_node[self.supervisor_a.id].suggested_kg, 300)
        self.assertEqual(by_node[self.supervisor_b.id].suggested_kg, 100)

    def test_splits_equally_when_no_supervisor_has_history_in_the_subgroup(self):
        # Subgrupo novo do mesmo grupo, sem NENHUMA venda registrada (nem de A, nem de B) — a soma
        # do peso por subgrupo dá zero, mas a sugestão não cai de volta pro peso do grupo inteiro
        # (confirmado com o usuário 2026-09-02) nem degrada pra "nenhuma": reparte em partes iguais
        # entre os dois Supervisores (garantia confirmada com o usuário, 2026-09-03 — sempre existe
        # uma sugestão pré-preenchida, editável, mesmo sem dado nenhum).
        subgrupo_novo = ProductSubgroup.objects.create(nome="Mortadela", group=self.group)
        subgroup_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=subgrupo_novo,
            quantity_kg=400,
            criado_por=User.objects.create_user(username="local7", password="x"),
        )

        contexts = DistributionContextService.build(subgroup_allocation)
        by_node = {ctx.owner_node_id: ctx for ctx in contexts}

        self.assertEqual(set(by_node), {self.supervisor_a.id, self.supervisor_b.id})
        self.assertEqual(by_node[self.supervisor_a.id].suggested_kg, 200)
        self.assertEqual(by_node[self.supervisor_b.id].suggested_kg, 200)


class SubgroupSupervisorContextApiTests(APITestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=1)
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.gerente
        )
        self.user = User.objects.create_user(username="local", password="x", hierarchy_node=self.local)
        self.allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=1000,
            criado_por=self.user,
        )
        self.client.force_login(self.user)

    def test_subgroup_distribution_context_rejects_non_owner(self):
        admin_user = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("goal-allocation-subgroup-distribution-context", args=[self.allocation.id])
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_subgroup_distribution_context_returns_context_per_subgroup(self):
        # Sem histórico algum, mas único subgrupo no grupo: a garantia de "sempre existe uma
        # sugestão" (RecentAverageDistributionStrategy, 2026-09-03) faz esse único alvo receber o
        # total inteiro.
        response = self.client.get(
            reverse("goal-allocation-subgroup-distribution-context", args=[self.allocation.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["subgroup_id"], self.subgroup.id)
        self.assertEqual(response.data[0]["suggested_kg"], 1000)

    def test_subgroup_distribution_context_matches_a_new_vendedor_without_running_the_command_by_hand(self):
        """Mesmo bug real de `test_distribution_context.py`, agora no outro endpoint que também
        chama `SalesHistoryProvider.target_history` (`SubgroupDistributionContextService`)."""
        ExternalProductMapping.objects.create(external_code="LINGUICA", subgroup=self.subgroup)
        vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor Novo", parent=self.local
        )
        for mes in range(1, 13):
            DistributionBaseline.objects.create(
                ano=2025,
                mes=mes,
                salesperson_name="Vendedor Novo",
                subgroup_name="LINGUICA",
                total_quantity=100,
            )
        self.assertFalse(ExternalSalespersonMapping.objects.filter(hierarchy_node=vendedor).exists())

        response = self.client.get(
            reverse("goal-allocation-subgroup-distribution-context", args=[self.allocation.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(ExternalSalespersonMapping.objects.filter(hierarchy_node=vendedor).exists())
        self.assertEqual(sum(point["quantity_kg"] for point in response.data[0]["history"]), 1200)

    def test_split_subgroups_persists_and_closes_exactly(self):
        response = self.client.post(
            reverse("goal-allocation-split-subgroups", args=[self.allocation.id]),
            {"subgroups": [{"subgroup_id": self.subgroup.id, "quantity_kg": 1000}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["owner_node"], self.local.id)
        self.assertEqual(response.data[0]["granularity"], "SUBGROUP")

    def test_split_subgroups_rejects_when_it_does_not_close_exactly(self):
        response = self.client.post(
            reverse("goal-allocation-split-subgroups", args=[self.allocation.id]),
            {"subgroups": [{"subgroup_id": self.subgroup.id, "quantity_kg": 1}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
