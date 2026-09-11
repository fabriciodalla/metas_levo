from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.sales_history.models import DistributionBaseline

from .models import ExternalSalespersonMapping, FeristaCoverage, HierarchyClosure, HierarchyNode
from .services import ExternalSalespersonMatchingService


class HierarchyNodeTests(TestCase):
    def test_create_root_gerente_node(self):
        node = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente Levo")
        self.assertIsNone(node.parent)
        self.assertTrue(node.ativo)

    def test_child_node_links_to_parent(self):
        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente Levo")
        local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local Curitiba", parent=gerente
        )
        self.assertEqual(local.parent, gerente)
        self.assertIn(local, gerente.children.all())


class HierarchyClosureTests(TestCase):
    def test_closure_includes_self_and_all_ancestors(self):
        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=gerente)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=local
        )

        ancestors_of_supervisor = set(
            HierarchyClosure.objects.filter(descendant=supervisor).values_list("ancestor_id", flat=True)
        )
        self.assertEqual(ancestors_of_supervisor, {gerente.id, local.id, supervisor.id})

        descendants_of_gerente = set(
            HierarchyClosure.objects.filter(ancestor=gerente).values_list("descendant_id", flat=True)
        )
        self.assertEqual(descendants_of_gerente, {gerente.id, local.id, supervisor.id})

    def test_closure_updates_when_node_is_reparented(self):
        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=gerente
        )
        local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=gerente
        )
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=local_a
        )

        supervisor.parent = local_b
        supervisor.save()

        ancestors_of_supervisor = set(
            HierarchyClosure.objects.filter(descendant=supervisor).values_list("ancestor_id", flat=True)
        )
        self.assertEqual(ancestors_of_supervisor, {gerente.id, local_b.id, supervisor.id})
        self.assertNotIn(local_a.id, ancestors_of_supervisor)

    def test_closure_row_removed_when_node_is_deleted(self):
        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=gerente)

        local.delete()

        self.assertFalse(HierarchyClosure.objects.filter(descendant_id=local.id).exists())


class ExternalSalespersonMappingTests(TestCase):
    def test_links_external_name_to_a_hierarchy_node(self):
        vendedor = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Fulano")

        mapping = ExternalSalespersonMapping.objects.create(
            external_name="FULANO DA SILVA", hierarchy_node=vendedor
        )

        self.assertEqual(mapping.hierarchy_node, vendedor)

    def test_external_name_is_unique(self):
        vendedor_a = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="A")
        vendedor_b = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="B")
        ExternalSalespersonMapping.objects.create(external_name="FULANO", hierarchy_node=vendedor_a)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExternalSalespersonMapping.objects.create(external_name="FULANO", hierarchy_node=vendedor_b)


class ExternalSalespersonMatchingServiceTests(TestCase):
    """Bug real (2026-08-07): um Vendedor novo (ex.: Rafael Pereira de Quadros) com histórico real
    em `DistributionBaseline` saía com sugestão zerada até alguém lembrar de rodar o comando
    manual `match_external_salespersons` — `sync()` é o mesmo casamento, chamado sozinho a cada
    abertura da tela de distribuição (ver `allocations/views.py`)."""

    def test_sync_creates_mapping_for_exact_name_match(self):
        vendedor = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva")
        DistributionBaseline.objects.create(
            ano=2026, mes=7, salesperson_name="Fulano Da Silva", subgroup_name="X", total_quantity=100
        )

        created, unmatched = ExternalSalespersonMatchingService.sync()

        self.assertEqual(created, ["Fulano Da Silva"])
        self.assertEqual(unmatched, [])
        mapping = ExternalSalespersonMapping.objects.get(hierarchy_node=vendedor)
        self.assertEqual(mapping.external_name, "Fulano Da Silva")

    def test_sync_does_not_approximate_inexact_name(self):
        HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Fulano")
        DistributionBaseline.objects.create(
            ano=2026, mes=7, salesperson_name="Fulano Da Silva", subgroup_name="X", total_quantity=100
        )

        created, unmatched = ExternalSalespersonMatchingService.sync()

        self.assertEqual(created, [])
        self.assertEqual(unmatched, ["Fulano"])
        self.assertFalse(ExternalSalespersonMapping.objects.exists())

    def test_sync_is_idempotent(self):
        HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva")
        DistributionBaseline.objects.create(
            ano=2026, mes=7, salesperson_name="Fulano Da Silva", subgroup_name="X", total_quantity=100
        )

        ExternalSalespersonMatchingService.sync()
        created_second_run, _unmatched = ExternalSalespersonMatchingService.sync()

        self.assertEqual(created_second_run, [])
        self.assertEqual(ExternalSalespersonMapping.objects.count(), 1)

    def test_sync_skips_node_already_mapped_to_a_different_name(self):
        vendedor = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva")
        ExternalSalespersonMapping.objects.create(external_name="NOME ANTIGO", hierarchy_node=vendedor)
        DistributionBaseline.objects.create(
            ano=2026, mes=7, salesperson_name="Fulano Da Silva", subgroup_name="X", total_quantity=100
        )

        created, _unmatched = ExternalSalespersonMatchingService.sync()

        self.assertEqual(created, [])
        mapping = ExternalSalespersonMapping.objects.get(hierarchy_node=vendedor)
        self.assertEqual(mapping.external_name, "NOME ANTIGO")


class FeristaCoverageTests(TestCase):
    """Decisão 13, revisão 2026-09-10: ferista é um Vendedor normal (`covering_node`) — pode
    cobrir mais de um titular no mesmo mês, mas um titular (`covered_node`) só é coberto por um
    ferista por mês."""

    def setUp(self):
        self.titular = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Titular")
        self.ferista = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Ferista")

    def test_links_covering_node_to_covered_node_and_month(self):
        coverage = FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=self.titular, ano=2026, mes=3
        )

        self.assertEqual(coverage.covering_node, self.ferista)
        self.assertEqual(coverage.covered_node, self.titular)

    def test_rejects_covered_node_that_is_not_vendedor(self):
        supervisor = HierarchyNode.objects.create(level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor")
        coverage = FeristaCoverage(covering_node=self.ferista, covered_node=supervisor, ano=2026, mes=3)

        with self.assertRaises(ValidationError):
            coverage.full_clean()

    def test_rejects_covering_node_that_is_not_vendedor(self):
        supervisor = HierarchyNode.objects.create(level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor")
        coverage = FeristaCoverage(covering_node=supervisor, covered_node=self.titular, ano=2026, mes=3)

        with self.assertRaises(ValidationError):
            coverage.full_clean()

    def test_rejects_ferista_covering_itself(self):
        coverage = FeristaCoverage(covering_node=self.ferista, covered_node=self.ferista, ano=2026, mes=3)

        with self.assertRaises(ValidationError):
            coverage.full_clean()

    def test_same_ferista_can_cover_two_titulares_in_the_same_month(self):
        other_titular = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Outro")
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2026, mes=3)

        second = FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=other_titular, ano=2026, mes=3
        )

        self.assertEqual(
            set(FeristaCoverage.objects.filter(covering_node=self.ferista, ano=2026, mes=3)),
            {FeristaCoverage.objects.get(covered_node=self.titular), second},
        )

    def test_same_titular_cannot_be_covered_twice_in_the_same_month(self):
        other_ferista = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Outro Ferista")
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2026, mes=3)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FeristaCoverage.objects.create(
                    covering_node=other_ferista, covered_node=self.titular, ano=2026, mes=3
                )

    def test_same_ferista_can_cover_different_people_in_different_months(self):
        other_titular = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Outro")
        FeristaCoverage.objects.create(covering_node=self.ferista, covered_node=self.titular, ano=2026, mes=3)

        coverage_abril = FeristaCoverage.objects.create(
            covering_node=self.ferista, covered_node=other_titular, ano=2026, mes=4
        )

        self.assertEqual(coverage_abril.covered_node, other_titular)
