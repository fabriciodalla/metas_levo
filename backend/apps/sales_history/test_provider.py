from decimal import Decimal

from django.test import TestCase

from apps.allocations.strategies import (
    LargestRemainderRoundingPolicy,
    MonthlyQuantity,
    SeasonalTrendDistributionStrategy,
    SeasonalTrendSuggestionStrategy,
)
from apps.catalog.models import ExternalProductMapping, ProductGroup, ProductSubgroup
from apps.hierarchy.models import ExternalSalespersonMapping, FeristaCoverage, HierarchyNode

from .models import DistributionBaseline
from .provider import SalesHistoryProvider


def _baseline(ano, mes, salesperson_name, subgroup_name, quantity):
    return DistributionBaseline.objects.create(
        ano=ano,
        mes=mes,
        salesperson_name=salesperson_name,
        subgroup_name=subgroup_name,
        total_quantity=Decimal(quantity),
    )


class SalesHistoryProviderGroupHistoryTests(TestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.linguica = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.salsicha = ProductSubgroup.objects.create(nome="Salsicha", group=self.group)
        ExternalProductMapping.objects.create(external_code="LINGUICA", subgroup=self.linguica)
        ExternalProductMapping.objects.create(external_code="SALSICHA", subgroup=self.salsicha)

        for mes in (1, 2, 3):
            _baseline(2025, mes, "FULANO", "LINGUICA", 100)
            _baseline(2025, mes, "FULANO", "SALSICHA", 50)

    def test_sums_across_all_mapped_subgroups_of_the_group(self):
        history = SalesHistoryProvider.group_history(self.group.id, period_months=3, last_month=(2025, 3))

        self.assertEqual([entry.quantity_kg for entry in history], [150.0, 150.0, 150.0])
        self.assertEqual([(entry.ano, entry.mes) for entry in history], [(2025, 1), (2025, 2), (2025, 3)])

    def test_fills_zero_for_months_without_data(self):
        history = SalesHistoryProvider.group_history(self.group.id, period_months=5, last_month=(2025, 3))

        self.assertEqual(
            [(entry.ano, entry.mes, entry.quantity_kg) for entry in history],
            [
                (2024, 11, 0.0),
                (2024, 12, 0.0),
                (2025, 1, 150.0),
                (2025, 2, 150.0),
                (2025, 3, 150.0),
            ],
        )

    def test_ignores_subgroup_names_without_mapping(self):
        _baseline(2025, 3, "FULANO", "SEM_MAPEAMENTO", 999)

        history = SalesHistoryProvider.group_history(self.group.id, period_months=1, last_month=(2025, 3))

        self.assertEqual(history, [MonthlyQuantity(ano=2025, mes=3, quantity_kg=150.0)])


class SalesHistoryProviderTargetHistoryTests(TestCase):
    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.linguica = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        ExternalProductMapping.objects.create(external_code="LINGUICA", subgroup=self.linguica)

        self.local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local")
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        self.vendedor_1 = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 1", parent=self.supervisor
        )
        self.vendedor_2 = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 2", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR UM", hierarchy_node=self.vendedor_1)
        ExternalSalespersonMapping.objects.create(
            external_name="VENDEDOR DOIS", hierarchy_node=self.vendedor_2
        )

        for mes in (1, 2):
            _baseline(2025, mes, "VENDEDOR UM", "LINGUICA", 60)
            _baseline(2025, mes, "VENDEDOR DOIS", "LINGUICA", 40)

    def test_sums_all_vendedor_descendants_of_the_target_node(self):
        history = SalesHistoryProvider.target_history(
            self.supervisor.id, period_months=2, last_month=(2025, 2)
        )

        self.assertEqual([entry.quantity_kg for entry in history], [100.0, 100.0])

    def test_works_for_a_leaf_vendedor_node_directly(self):
        history = SalesHistoryProvider.target_history(
            self.vendedor_1.id, period_months=2, last_month=(2025, 2)
        )

        self.assertEqual([entry.quantity_kg for entry in history], [60.0, 60.0])

    def test_filters_by_subgroup_id(self):
        outro_subgroup = ProductSubgroup.objects.create(nome="Salsicha", group=self.group)
        ExternalProductMapping.objects.create(external_code="SALSICHA", subgroup=outro_subgroup)
        _baseline(2025, 2, "VENDEDOR UM", "SALSICHA", 999)

        history = SalesHistoryProvider.target_history(
            self.supervisor.id, period_months=1, last_month=(2025, 2), subgroup_id=self.linguica.id
        )

        self.assertEqual(history[0].quantity_kg, 100.0)

    def test_vendedor_without_mapping_contributes_nothing(self):
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Sem Mapeamento", parent=self.supervisor
        )
        _baseline(2025, 2, "NOME QUE NAO ESTA MAPEADO", "LINGUICA", 500)

        history = SalesHistoryProvider.target_history(
            self.supervisor.id, period_months=1, last_month=(2025, 2)
        )

        self.assertEqual(history[0].quantity_kg, 100.0)


class SalesHistoryProviderEndToEndStrategyTests(TestCase):
    """Prova que O3/O5, uma vez mapeados, destravam P1-P4 com histórico real (não mais dados
    injetados manualmente nos testes de strategies.py)."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        ExternalProductMapping.objects.create(external_code="LINGUICA", subgroup=self.subgroup)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )
        vendedor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor A", parent=self.local_a
        )
        vendedor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor B", parent=self.local_b
        )
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR A", hierarchy_node=vendedor_a)
        ExternalSalespersonMapping.objects.create(external_name="VENDEDOR B", hierarchy_node=vendedor_b)

        # Local A tem histórico bem maior que Local B.
        for mes in range(1, 13):
            _baseline(2025, mes, "VENDEDOR A", "LINGUICA", 300)
            _baseline(2025, mes, "VENDEDOR B", "LINGUICA", 100)

    def test_group_history_feeds_suggestion_strategy(self):
        history = SalesHistoryProvider.group_history(self.group.id, period_months=12, last_month=(2025, 12))
        strategy = SeasonalTrendSuggestionStrategy(history_by_group={self.group.id: history})

        result = strategy.suggest(group_ids=[self.group.id], period_months=12)

        self.assertEqual(result, {self.group.id: 400})  # 300 + 100, plano (sem tendência real)

    def test_target_history_feeds_distribution_strategy_and_closes_exactly(self):
        history_by_target = {
            self.local_a.id: SalesHistoryProvider.target_history(
                self.local_a.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
            ),
            self.local_b.id: SalesHistoryProvider.target_history(
                self.local_b.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
            ),
        }
        strategy = SeasonalTrendDistributionStrategy(
            history_by_target=history_by_target, rounding_policy=LargestRemainderRoundingPolicy()
        )

        result = strategy.distribute(total_kg=400, target_ids=[self.local_a.id, self.local_b.id])

        self.assertEqual(sum(result.values()), 400)


class SalesHistoryProviderFeristaCoverageTests(TestCase):
    """Decisão 13, revisão 2026-09-10: histórico do titular, num mês coberto, conta pro nó do
    ferista que está cobrindo (base pra sugestão de meta do ferista) — fora disso, cada um conta
    só o próprio histórico, igual qualquer Vendedor mapeado normalmente."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        ExternalProductMapping.objects.create(external_code="LINGUICA", subgroup=self.subgroup)

        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor"
        )
        self.titular = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Titular", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="TITULAR", hierarchy_node=self.titular)
        self.ferista = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Ferista", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="FERISTA", hierarchy_node=self.ferista)

        for mes in range(1, 13):
            _baseline(2025, mes, "TITULAR", "LINGUICA", 100)

    def test_covered_month_adds_titular_volume_to_ferista(self):
        _baseline(2025, 6, "FERISTA", "LINGUICA", 50)  # ferista já vendia algo em nome próprio
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2025, mes=6)

        history = SalesHistoryProvider.target_history(
            self.ferista.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
        )

        by_month = {(p.ano, p.mes): p.quantity_kg for p in history}
        self.assertEqual(by_month[(2025, 6)], 150.0)  # 50 do ferista + 100 do titular coberto
        self.assertEqual(by_month[(2025, 7)], 0.0)  # mês sem cobertura, ferista não vendeu nada

    def test_titular_volume_outside_covered_month_does_not_count_for_ferista(self):
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2025, mes=6)

        history = SalesHistoryProvider.target_history(
            self.ferista.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
        )

        by_month = {(p.ano, p.mes): p.quantity_kg for p in history}
        self.assertEqual(by_month[(2025, 7)], 0.0)  # julho não é coberto, os 100 do titular ficam só com ele

    def test_titular_own_history_is_unaffected_by_being_covered(self):
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2025, mes=6)

        history = SalesHistoryProvider.target_history(
            self.titular.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
        )

        by_month = {(p.ano, p.mes): p.quantity_kg for p in history}
        self.assertEqual(by_month[(2025, 6)], 100.0)  # titular continua com o próprio histórico

    def test_supervisor_aggregate_does_not_double_count_covered_month(self):
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2025, mes=6)

        history = SalesHistoryProvider.target_history(
            self.supervisor.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
        )

        by_month = {(p.ano, p.mes): p.quantity_kg for p in history}
        # Continua 100, não 200: titular e ferista estão os dois no escopo do Supervisor — o
        # volume do titular já entra pela soma normal, somar de novo aqui duplicaria.
        self.assertEqual(by_month[(2025, 6)], 100.0)

    def test_same_ferista_redirects_from_different_titulares_in_different_months(self):
        outro_titular = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Outro Titular", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="OUTRO TITULAR", hierarchy_node=outro_titular)
        for mes in range(1, 13):
            _baseline(2025, mes, "OUTRO TITULAR", "LINGUICA", 200)

        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2025, mes=6)
        FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=outro_titular, ano=2025, mes=7
        )

        history = {
            (p.ano, p.mes): p.quantity_kg
            for p in SalesHistoryProvider.target_history(
                self.ferista.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
            )
        }

        self.assertEqual(history[(2025, 6)], 100.0)  # cobriu o titular original em junho
        self.assertEqual(history[(2025, 7)], 200.0)  # cobriu o outro titular em julho

    def test_same_ferista_covering_two_titulares_in_the_same_month_sums_both(self):
        outro_titular = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Outro Titular", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="OUTRO TITULAR", hierarchy_node=outro_titular)
        for mes in range(1, 13):
            _baseline(2025, mes, "OUTRO TITULAR", "LINGUICA", 200)

        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2025, mes=6)
        FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=outro_titular, ano=2025, mes=6
        )

        history = {
            (p.ano, p.mes): p.quantity_kg
            for p in SalesHistoryProvider.target_history(
                self.ferista.id, period_months=12, last_month=(2025, 12), group_id=self.group.id
            )
        }

        self.assertEqual(history[(2025, 6)], 300.0)  # 100 do titular + 200 do outro, junho os dois
