from datetime import date

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
from .results import (
    AccumulatedSalesResultsService,
    _build_group_result,
    _dias_uteis_decorridos,
    _dias_uteis_restantes,
    _dias_uteis_total_mes,
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


class AccumulatedSalesResultsServiceTests(TestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Frangos")
        self.subgroup_peito = ProductSubgroup.objects.create(nome="Peito", group=self.group)
        ExternalProductMapping.objects.create(external_code="PEITO", subgroup=self.subgroup_peito)

        self.cycle = Cycle.objects.create(ano=2026, mes=1)

        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor"
        )
        self.vendedor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor A", parent=self.supervisor
        )
        self.vendedor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor B", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR A", hierarchy_node=self.vendedor_a)
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR B", hierarchy_node=self.vendedor_b)

        self.criador = User.objects.create_user(username="sup", password="x", hierarchy_node=self.supervisor)

    def _meta(self, node: HierarchyNode, subgroup: ProductSubgroup, quantity_kg: int) -> None:
        GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=node,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=subgroup,
            quantity_kg=quantity_kg,
            criado_por=self.criador,
        )

    def test_aggregates_meta_and_realizado_across_leaves_and_marks_verde(self):
        self._meta(self.vendedor_a, self.subgroup_peito, 1000)
        self._meta(self.vendedor_b, self.subgroup_peito, 500)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 800)
        _baseline(2026, 1, "VENDEDOR B", "PEITO", 700)

        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)

        self.assertEqual(frangos.meta_kg, 1500)
        self.assertEqual(frangos.realizado_kg, 1500)
        self.assertAlmostEqual(frangos.pct, 1.0)
        self.assertEqual(frangos.status, "VERDE")
        self.assertEqual(frangos.faltam_kg, 0)
        self.assertEqual(frangos.subgrupos_com_meta, 1)
        self.assertEqual(frangos.subgrupos_atingidos, 1)
        self.assertTrue(frangos.subgrupos_atingiu)
        self.assertTrue(frangos.atingiu_grupo)

    def test_status_thresholds_amarelo_and_vermelho(self):
        self._meta(self.vendedor_a, self.subgroup_peito, 1000)

        _baseline(2026, 1, "VENDEDOR A", "PEITO", 920)  # 92% -> amarelo
        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)
        self.assertEqual(frangos.status, "AMARELO")

        DistributionBaseline.objects.all().delete()
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 500)  # 50% -> vermelho
        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)
        self.assertEqual(frangos.status, "VERMELHO")
        self.assertEqual(frangos.faltam_kg, 500)

    def test_group_without_any_meta_or_sale_is_sem_meta(self):
        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)

        self.assertEqual(frangos.status, "SEM_META")
        self.assertIsNone(frangos.pct)
        self.assertIsNone(frangos.atingiu_grupo)
        self.assertEqual(frangos.subgrupos, [])

    def test_subgroup_only_listed_when_it_has_meta_or_realizado(self):
        outro_subgrupo = ProductSubgroup.objects.create(nome="Coxa", group=self.group)
        ExternalProductMapping.objects.create(external_code="COXA", subgroup=outro_subgrupo)
        self._meta(self.vendedor_a, self.subgroup_peito, 1000)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 1000)
        # "Coxa" não tem meta nem venda -> não deve aparecer.
        # Venda avulsa sem meta associada (fora do planejado) em outro subgrupo -> deve aparecer.
        terceiro_subgrupo = ProductSubgroup.objects.create(nome="Asa", group=self.group)
        ExternalProductMapping.objects.create(external_code="ASA", subgroup=terceiro_subgrupo)
        _baseline(2026, 1, "VENDEDOR A", "ASA", 50)

        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)
        shown_ids = {row.subgroup_id for row in frangos.subgrupos}

        self.assertIn(self.subgroup_peito.id, shown_ids)
        self.assertIn(terceiro_subgrupo.id, shown_ids)
        self.assertNotIn(outro_subgrupo.id, shown_ids)

        venda_avulsa = next(row for row in frangos.subgrupos if row.subgroup_id == terceiro_subgrupo.id)
        self.assertEqual(venda_avulsa.meta_kg, 0)
        self.assertEqual(venda_avulsa.realizado_kg, 50)
        self.assertIsNone(venda_avulsa.pct)
        self.assertIsNone(venda_avulsa.atingiu)
        # Não conta pro denominador de "% subgrupos atingidos" (sem meta associada).
        self.assertEqual(frangos.subgrupos_com_meta, 1)

    def test_subgrupos_tolerance_zone_62_5_percent_counts_as_atingido(self):
        subgroups = [self.subgroup_peito]
        for nome, codigo in [("Coxa", "COXA"), ("Asa", "ASA"), ("Sassami", "SASSAMI")]:
            sg = ProductSubgroup.objects.create(nome=nome, group=self.group)
            ExternalProductMapping.objects.create(external_code=codigo, subgroup=sg)
            subgroups.append(sg)
        # 4 pares extras pra fechar 8 subgrupos com meta (1 já criado acima = Peito).
        extra_codes = ["MOELA", "FIGADO", "DORSO", "CARTILAGENS"]
        for nome, codigo in zip(["Moela", "Figado", "Dorso", "Cartilagens"], extra_codes):
            sg = ProductSubgroup.objects.create(nome=nome, group=self.group)
            ExternalProductMapping.objects.create(external_code=codigo, subgroup=sg)
            subgroups.append(sg)

        self.assertEqual(len(subgroups), 8)
        codes = ["PEITO", "COXA", "ASA", "SASSAMI"] + extra_codes
        for sg in subgroups:
            self._meta(self.vendedor_a, sg, 100)

        # 5 de 8 batem 100% (>=99.5%), 3 ficam bem abaixo -> 5/8 = 62,5% exatos.
        for codigo in codes[:5]:
            _baseline(2026, 1, "VENDEDOR A", codigo, 100)
        for codigo in codes[5:]:
            _baseline(2026, 1, "VENDEDOR A", codigo, 10)

        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)
        self.assertEqual(frangos.subgrupos_com_meta, 8)
        self.assertEqual(frangos.subgrupos_atingidos, 5)
        self.assertAlmostEqual(frangos.pct_subgrupos, 0.625)
        self.assertTrue(frangos.subgrupos_atingiu)

        # Derruba pra 4 de 8 (50%) -> abaixo da zona de tolerância.
        DistributionBaseline.objects.filter(subgroup_name=codes[4]).update(total_quantity=10)
        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)
        self.assertAlmostEqual(frangos.pct_subgrupos, 0.5)
        self.assertFalse(frangos.subgrupos_atingiu)

    def test_team_is_sorted_worst_status_first(self):
        vendedor_c = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor C", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR C", hierarchy_node=vendedor_c)

        self._meta(self.vendedor_a, self.subgroup_peito, 1000)
        self._meta(self.vendedor_b, self.subgroup_peito, 1000)
        self._meta(vendedor_c, self.subgroup_peito, 1000)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 1000)  # verde
        _baseline(2026, 1, "VENDEDOR B", "PEITO", 920)  # amarelo
        _baseline(2026, 1, "VENDEDOR C", "PEITO", 100)  # vermelho

        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)

        self.assertEqual(
            [member.node_id for member in result.equipe],
            [vendedor_c.id, self.vendedor_b.id, self.vendedor_a.id],
        )
        self.assertEqual([member.status_geral for member in result.equipe], ["VERMELHO", "AMARELO", "VERDE"])

    def test_status_geral_downgrades_to_amarelo_when_volume_ok_but_subgrupos_lacking(self):
        # Vendedor bate 100% do volume agregado, mas só atinge 1 de 2 subgrupos com meta
        # individual (50% < 62,5% de tolerância) -> não conta como "batida" plena no status geral,
        # mesma regra do badge de grupo (`atingiu_grupo`).
        outro_subgrupo = ProductSubgroup.objects.create(nome="Coxa", group=self.group)
        ExternalProductMapping.objects.create(external_code="COXA", subgroup=outro_subgrupo)
        self._meta(self.vendedor_a, self.subgroup_peito, 500)
        self._meta(self.vendedor_a, outro_subgrupo, 500)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 1000)  # 100% desse subgrupo
        _baseline(2026, 1, "VENDEDOR A", "COXA", 0)  # 0% desse subgrupo

        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        vendedor_a_result = next(m for m in result.equipe if m.node_id == self.vendedor_a.id)
        frangos = next(g for g in vendedor_a_result.grupos if g.group_id == self.group.id)

        self.assertAlmostEqual(frangos.pct, 1.0)
        self.assertEqual(frangos.status, "VERDE")
        self.assertFalse(frangos.subgrupos_atingiu)
        self.assertFalse(frangos.atingiu_grupo)
        self.assertEqual(vendedor_a_result.status_geral, "AMARELO")

    def test_same_month_last_year_and_last_3_months_avg(self):
        for mes in range(1, 13):
            _baseline(2025, mes, "VENDEDOR A", "PEITO", 100 + mes)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 500)
        self._meta(self.vendedor_a, self.subgroup_peito, 500)

        result = AccumulatedSalesResultsService.build(self.supervisor, self.cycle)
        frangos = next(g for g in result.grupos if g.group_id == self.group.id)

        self.assertEqual(frangos.same_month_last_year_kg, 101.0)  # jan/2025
        self.assertAlmostEqual(frangos.last_3_months_avg_kg, (110 + 111 + 112) / 3)  # out/nov/dez 2025

    def test_ritmo_kg_dia_util_uses_injected_today(self):
        # 16/02/2026 é uma segunda-feira; até 28/02/2026 (sábado), 10 dias úteis (16-20, 23-27).
        dias = _dias_uteis_restantes(2026, 2, hoje=date(2026, 2, 16))
        self.assertEqual(dias, 10)

    def test_dias_uteis_restantes_is_zero_outside_the_current_month(self):
        self.assertEqual(_dias_uteis_restantes(2020, 1, hoje=date(2026, 1, 1)), 0)

    def test_equipe_always_shows_node_where_same_person_holds_local_and_supervisor(self):
        # Caso real (Decisão O5): uma pessoa acumula o cargo de Coordenador Local e de Supervisor
        # de um dos seus próprios Supervisores — dois nós, mesmo `User` (um login só) vinculado
        # aos dois. Regra geral, sem exceção (revisão confirmada pelo usuário, 2026-09-17): a
        # equipe sempre mostra o 1º grau abaixo como uma linha normal, pra qualquer um que olhar —
        # tanto um superior de fora (ex.: o Gerente) quanto a própria pessoa navegando pela sua
        # posição de Coordenador. Quem quer ver os Vendedores dessa posição duplicada usa o
        # drill-down normal da linha (fora do escopo deste service, é o frontend que decide exibir
        # essa opção extra só pro dono do cargo duplo).
        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        local_sandro = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Sandro", parent=gerente
        )
        supervisor_sandro = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Sandro", parent=local_sandro
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor C", parent=supervisor_sandro
        )
        sandro_user = User.objects.create_user(username="sandro", password="x")
        sandro_user.hierarchy_nodes.add(local_sandro, supervisor_sandro)

        result_gerente = AccumulatedSalesResultsService.build(gerente, self.cycle)
        self.assertEqual([m.node_id for m in result_gerente.equipe], [local_sandro.id])

        result_local_self = AccumulatedSalesResultsService.build(local_sandro, self.cycle)
        self.assertEqual([m.node_id for m in result_local_self.equipe], [supervisor_sandro.id])

    def test_dias_uteis_decorridos_uses_injected_today(self):
        # 16/02/2026 é uma segunda-feira; de 01/02 (domingo) até 16/02 (inclusive), 11 dias úteis
        # (2-6, 9-13, 16).
        self.assertEqual(_dias_uteis_decorridos(2026, 2, hoje=date(2026, 2, 16)), 11)

    def test_dias_uteis_decorridos_is_zero_outside_the_current_month(self):
        self.assertEqual(_dias_uteis_decorridos(2020, 1, hoje=date(2026, 1, 1)), 0)

    def test_dias_uteis_total_mes(self):
        # Fevereiro/2026 (28 dias, começa numa domingo) tem 20 dias úteis.
        self.assertEqual(_dias_uteis_total_mes(2026, 2), 20)

    def test_tendencia_kg_projects_realizado_by_elapsed_business_days(self):
        self._meta(self.vendedor_a, self.subgroup_peito, 1000)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 550)

        frangos = _build_group_result(
            self.supervisor.id,
            self.cycle,
            self.group,
            dias_uteis_restantes=10,
            dias_uteis_decorridos=11,
            dias_uteis_total_mes=21,
        )
        # média de 50 kg/dia útil (550/11) projetada pros 21 dias úteis do mês -> 1050 kg.
        self.assertAlmostEqual(frangos.tendencia_kg, 1050.0)

    def test_tendencia_kg_is_none_without_elapsed_business_days(self):
        self._meta(self.vendedor_a, self.subgroup_peito, 1000)
        _baseline(2026, 1, "VENDEDOR A", "PEITO", 550)

        frangos = _build_group_result(
            self.supervisor.id,
            self.cycle,
            self.group,
            dias_uteis_restantes=0,
            dias_uteis_decorridos=0,
            dias_uteis_total_mes=21,
        )
        self.assertIsNone(frangos.tendencia_kg)


class AcumuladoVendasApiTests(APITestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Frangos")
        self.cycle = Cycle.objects.create(ano=2026, mes=1)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.supervisor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A", parent=self.local_a
        )
        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )

        self.user_a = User.objects.create_user(username="user_a", password="x", hierarchy_node=self.local_a)
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.client.force_login(self.user_a)

    def test_requires_cycle_and_node_query_params(self):
        response = self.client.get(reverse("goal-allocation-acumulado-vendas"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_can_drill_down_into_a_descendant_node(self):
        response = self.client.get(
            reverse("goal-allocation-acumulado-vendas"),
            {"cycle": self.cycle.id, "node": self.supervisor_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["node_id"], self.supervisor_a.id)

    def test_user_cannot_view_a_node_outside_their_branch(self):
        response = self.client.get(
            reverse("goal-allocation-acumulado-vendas"),
            {"cycle": self.cycle.id, "node": self.local_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_view_any_node(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("goal-allocation-acumulado-vendas"),
            {"cycle": self.cycle.id, "node": self.local_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_response_shape_includes_groups_and_team(self):
        response = self.client.get(
            reverse("goal-allocation-acumulado-vendas"),
            {"cycle": self.cycle.id, "node": self.local_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("grupos", response.data)
        self.assertIn("equipe", response.data)
        self.assertEqual(len(response.data["equipe"]), 1)
        self.assertEqual(response.data["equipe"][0]["node_id"], self.supervisor_a.id)
