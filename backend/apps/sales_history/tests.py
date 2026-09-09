import re
import threading
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from apps.catalog.models import ExternalProductMapping, ProductGroup, ProductSubgroup
from apps.hierarchy.models import ExternalSalespersonMapping, FeristaCoverage, HierarchyNode

from .models import AccumulatedSale, ClientPortfolioSnapshot, DistributionBaseline
from .queries import ACUMULADO_SQL, CARTEIRA_SQL
from .services import (
    DistributionBaselineService,
    SalesHistorySyncService,
    VendorGroupSummaryService,
    VendorSubgroupExportService,
    sync_lock,
)


def _mock_cursor(mock_connections, columns, rows):
    cursor = MagicMock()
    cursor.description = [(name,) for name in columns]
    cursor.fetchall.return_value = rows
    mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor


class SalesHistorySyncServiceTests(TestCase):
    @patch("apps.sales_history.services.connections")
    def test_sync_accumulated_creates_local_rows_from_query_result(self, mock_connections):
        _mock_cursor(
            mock_connections,
            columns=[
                "nk_supervisor",
                "nk_vendedor",
                "nome_vendedor",
                "clifor",
                "cnpj",
                "nome_cliente",
                "dt_emissao",
                "ds_subgrupo",
                "total_ps_atendido",
                "total_vl_movtocontabil",
            ],
            rows=[
                (
                    "B.F.434",
                    "B.F.1401",
                    "Fulano",
                    123,
                    "12345678900",
                    "Cliente X",
                    date(2026, 6, 10),
                    "Linguicas",
                    100,
                    500,
                )
            ],
        )

        count = SalesHistorySyncService.sync_accumulated(date(2026, 6, 1))

        self.assertEqual(count, 1)
        entry = AccumulatedSale.objects.get()
        self.assertEqual(entry.nk_vendedor, "B.F.1401")
        self.assertEqual(entry.client_code, 123)
        self.assertEqual(entry.subgroup_name, "Linguicas")
        self.assertEqual(entry.total_quantity, 100)

    @patch("apps.sales_history.services.connections")
    def test_sync_only_touches_the_sales_history_alias(self, mock_connections):
        _mock_cursor(mock_connections, columns=["nk_supervisor"], rows=[])

        SalesHistorySyncService.sync_accumulated(date(2026, 6, 1))

        mock_connections.__getitem__.assert_called_once_with("sales_history")

    @patch("apps.sales_history.services.connections")
    def test_sync_accumulated_replaces_the_whole_table_not_just_the_synced_window(self, mock_connections):
        # Linha fora da janela sendo sincronizada (sale_date bem antes de min_date): precisa sumir
        # também. Deixar rows fora da janela oficial (H2 = 12 meses) sobrando indefinidamente é o
        # que fazia `DistributionBaselineService.rebuild()` — que lê a tabela sem filtro de data —
        # calcular sobre meses a mais do que o decidido.
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.2",
            salesperson_name="Antigo",
            client_code=1,
            sale_date=date(2025, 1, 5),
            subgroup_name="Linguicas",
            total_quantity=10,
            total_value=50,
        )
        _mock_cursor(mock_connections, columns=["nk_supervisor"], rows=[])

        SalesHistorySyncService.sync_accumulated(date(2026, 6, 1))

        self.assertFalse(AccumulatedSale.objects.exists())

    @patch("apps.sales_history.services.connections")
    def test_sync_portfolio_replaces_existing_rows(self, mock_connections):
        ClientPortfolioSnapshot.objects.create(
            client_code=999, client_name="Antigo", salesperson_name="X", nk_supervisor="B.F.1"
        )
        _mock_cursor(
            mock_connections,
            columns=[
                "clifor",
                "cnpj",
                "nome_cliente",
                "nome_vendedor",
                "nk_supervisor",
                "municipio",
                "estado",
                "cadastro",
                "alterado",
            ],
            rows=[
                (
                    321,
                    "98765432100",
                    "Cliente Y",
                    "Beltrano",
                    "B.F.229",
                    "Goiania",
                    "GOIAS",
                    date(2020, 1, 1),
                    date(2026, 5, 1),
                )
            ],
        )

        count = SalesHistorySyncService.sync_portfolio()

        self.assertEqual(count, 1)
        self.assertFalse(ClientPortfolioSnapshot.objects.filter(client_code=999).exists())
        self.assertTrue(ClientPortfolioSnapshot.objects.filter(client_code=321).exists())


class SyncLockTests(TransactionTestCase):
    """Prova, com duas conexões reais de banco em threads separadas, que `sync_lock` serializa
    execuções concorrentes — a causa raiz do incidente de 2026-09-02 (dois syncs sobrepostos
    duplicaram `AccumulatedSale` porque nenhum via o DELETE não-commitado do outro). Usa
    `TransactionTestCase` (não `TestCase`) porque o teste depende de commits reais visíveis entre
    conexões — `TestCase` embrulha cada teste numa transação nunca commitada."""

    def test_second_caller_blocks_until_first_releases_the_lock(self):
        events = []
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()

        def hold_lock():
            with sync_lock():
                events.append("first-acquired")
                first_acquired.set()
                release_first.wait(timeout=5)
                events.append("first-released")
            connections.close_all()

        def try_lock():
            first_acquired.wait(timeout=5)
            events.append("second-waiting")
            with sync_lock():
                events.append("second-acquired")
                second_acquired.set()
            connections.close_all()

        t1 = threading.Thread(target=hold_lock)
        t2 = threading.Thread(target=try_lock)
        t1.start()
        t2.start()

        # Dá tempo da segunda thread ficar de fato bloqueada em pg_advisory_xact_lock antes de
        # liberar a primeira — sem isso o teste passaria mesmo se o lock não bloqueasse nada.
        blocked_before_release = not second_acquired.wait(timeout=1)

        release_first.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertTrue(
            blocked_before_release, "segunda chamada não deveria adquirir o lock antes da primeira soltar"
        )
        self.assertEqual(
            events,
            ["first-acquired", "second-waiting", "first-released", "second-acquired"],
        )


class DistributionBaselineServiceTests(TestCase):
    def setUp(self):
        # Cliente A: vendido historicamente por "Vendedor Antigo", mas a carteira diz que hoje
        # quem responde por ele é "Vendedor Novo" — o histórico inteiro deve migrar pra ele.
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.ANTIGO",
            salesperson_name="Vendedor Antigo",
            client_code=1,
            sale_date=date(2026, 1, 15),
            subgroup_name="Linguica",
            total_quantity=Decimal("10"),
            total_value=Decimal("100"),
        )
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.ANTIGO",
            salesperson_name="Vendedor Antigo",
            client_code=1,
            sale_date=date(2026, 1, 20),
            subgroup_name="Salsicha",
            total_quantity=Decimal("5"),
            total_value=Decimal("50"),
        )
        # Cliente B: vendido pelo mesmo "Vendedor Novo" que já consta na carteira — some ao total.
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.NOVO",
            salesperson_name="Vendedor Novo",
            client_code=2,
            sale_date=date(2026, 1, 22),
            subgroup_name="Linguica",
            total_quantity=Decimal("20"),
            total_value=Decimal("200"),
        )
        # Cliente C: tem venda no acumulado, mas não está na carteira atual — vira uma linha
        # separada com salesperson_name=None (conta pra base do Gerente, mas não pra de ninguém
        # em específico).
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.FORA",
            salesperson_name="Vendedor Fora Da Carteira",
            client_code=3,
            sale_date=date(2026, 1, 5),
            subgroup_name="Linguica",
            total_quantity=Decimal("999"),
            total_value=Decimal("999"),
        )

        ClientPortfolioSnapshot.objects.create(
            client_code=1, client_name="Cliente A", salesperson_name="Vendedor Novo", nk_supervisor="B.F.1"
        )
        ClientPortfolioSnapshot.objects.create(
            client_code=2, client_name="Cliente B", salesperson_name="Vendedor Novo", nk_supervisor="B.F.1"
        )

    def test_rebuild_reassigns_history_to_current_portfolio_owner(self):
        count = DistributionBaselineService.rebuild()

        self.assertEqual(count, 3)
        self.assertFalse(DistributionBaseline.objects.filter(salesperson_name="Vendedor Antigo").exists())
        self.assertFalse(
            DistributionBaseline.objects.filter(salesperson_name="Vendedor Fora Da Carteira").exists()
        )

        linguica = DistributionBaseline.objects.get(
            subgroup_name="Linguica", salesperson_name="Vendedor Novo"
        )
        self.assertEqual(linguica.ano, 2026)
        self.assertEqual(linguica.mes, 1)
        self.assertEqual(linguica.total_quantity, Decimal("30"))  # 10 (cliente A) + 20 (cliente B)

        salsicha = DistributionBaseline.objects.get(subgroup_name="Salsicha")
        self.assertEqual(salsicha.salesperson_name, "Vendedor Novo")
        self.assertEqual(salsicha.total_quantity, Decimal("5"))

        # Cliente C (fora da carteira atual) some como vendedor específico, mas continua contando
        # como linha própria (salesperson_name=None) — é o que P1 (Gerente) precisa enxergar.
        orfao = DistributionBaseline.objects.get(subgroup_name="Linguica", salesperson_name__isnull=True)
        self.assertEqual(orfao.total_quantity, Decimal("999"))

    def test_rebuild_is_idempotent(self):
        DistributionBaselineService.rebuild()
        DistributionBaselineService.rebuild()

        self.assertEqual(DistributionBaseline.objects.count(), 3)
        linguica = DistributionBaseline.objects.get(
            subgroup_name="Linguica", salesperson_name="Vendedor Novo"
        )
        self.assertEqual(linguica.total_quantity, Decimal("30"))

    def test_rebuild_rounds_grouped_total_half_up_to_integer_kg(self):
        AccumulatedSale.objects.all().delete()
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.NOVO",
            salesperson_name="Vendedor Novo",
            client_code=1,
            sale_date=date(2026, 2, 1),
            subgroup_name="Linguica",
            total_quantity=Decimal("10.4"),
            total_value=Decimal("10"),
        )
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.NOVO",
            salesperson_name="Vendedor Novo",
            client_code=2,
            sale_date=date(2026, 2, 1),
            subgroup_name="Salsicha",
            total_quantity=Decimal("10.5"),
            total_value=Decimal("10"),
        )

        DistributionBaselineService.rebuild()

        self.assertEqual(
            DistributionBaseline.objects.get(subgroup_name="Linguica").total_quantity, Decimal("10")
        )
        self.assertEqual(
            DistributionBaseline.objects.get(subgroup_name="Salsicha").total_quantity, Decimal("11")
        )


class DistributionBaselineServiceFeristaCoverageTests(TestCase):
    """Extensão da Decisão 13 (2026-08-28): a reconstrução já entrega o volume do ferista
    redirecionado pro titular no mês corrente da reconstrução, sem depender do redirecionamento
    avulso de `SalesHistoryProvider.target_history` — necessário pra telas que leem
    `DistributionBaseline` direto, como `VendorGroupSummaryService`."""

    def setUp(self):
        self.titular = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Titular")
        ExternalSalespersonMapping.objects.create(
            external_name="Titular Externo", hierarchy_node=self.titular
        )

        ClientPortfolioSnapshot.objects.create(
            client_code=1, client_name="Cliente A", salesperson_name="Ferista Externo", nk_supervisor="B.F.1"
        )
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.FERISTA",
            salesperson_name="Ferista Externo",
            client_code=1,
            sale_date=date(2026, 8, 10),
            subgroup_name="Linguica",
            total_quantity=Decimal("40"),
            total_value=Decimal("400"),
        )

    def test_rebuild_redirects_current_month_ferista_volume_to_titular(self):
        FeristaCoverage.objects.create(
            external_name="Ferista Externo", covered_node=self.titular, ano=2026, mes=8
        )

        DistributionBaselineService.rebuild(today=date(2026, 8, 28))

        self.assertFalse(DistributionBaseline.objects.filter(salesperson_name="Ferista Externo").exists())
        linguica = DistributionBaseline.objects.get(subgroup_name="Linguica")
        self.assertEqual(linguica.salesperson_name, "Titular Externo")
        self.assertEqual(linguica.total_quantity, Decimal("40"))

    def test_rebuild_ignores_ferista_coverage_registered_for_other_months(self):
        # Cobertura cadastrada pra julho e setembro — o mês atual da reconstrução (agosto) não
        # tem cobertura própria, então o volume do ferista fica sem titular, igual qualquer nome
        # sem `ExternalSalespersonMapping`.
        FeristaCoverage.objects.create(
            external_name="Ferista Externo", covered_node=self.titular, ano=2026, mes=7
        )
        FeristaCoverage.objects.create(
            external_name="Ferista Externo", covered_node=self.titular, ano=2026, mes=9
        )

        DistributionBaselineService.rebuild(today=date(2026, 8, 28))

        self.assertTrue(DistributionBaseline.objects.filter(salesperson_name="Ferista Externo").exists())
        self.assertFalse(DistributionBaseline.objects.filter(salesperson_name="Titular Externo").exists())

    def test_rebuild_does_not_redirect_ferista_volume_outside_current_month(self):
        AccumulatedSale.objects.all().delete()
        AccumulatedSale.objects.create(
            nk_supervisor="B.F.1",
            nk_vendedor="B.F.FERISTA",
            salesperson_name="Ferista Externo",
            client_code=1,
            sale_date=date(2026, 7, 10),
            subgroup_name="Linguica",
            total_quantity=Decimal("40"),
            total_value=Decimal("400"),
        )
        FeristaCoverage.objects.create(
            external_name="Ferista Externo", covered_node=self.titular, ano=2026, mes=8
        )

        DistributionBaselineService.rebuild(today=date(2026, 8, 28))

        self.assertTrue(DistributionBaseline.objects.filter(salesperson_name="Ferista Externo").exists())
        self.assertFalse(DistributionBaseline.objects.filter(salesperson_name="Titular Externo").exists())


class VendorGroupSummaryServiceTests(TestCase):
    def setUp(self):
        self.embutidos = ProductGroup.objects.create(nome="Embutidos")
        linguica = ProductSubgroup.objects.create(nome="Linguica", group=self.embutidos)
        ExternalProductMapping.objects.create(external_code="LINGUICA_COD", subgroup=linguica)

        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        regional = HierarchyNode.objects.create(
            level=HierarchyNode.Level.REGIONAL, nome="Regional", parent=gerente
        )
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local Sul", parent=regional
        )
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A", parent=self.local
        )

        self.joao = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Joao", ativo=True, parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="Joao", hierarchy_node=self.joao)

        self.maria = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Maria", ativo=True
        )
        # Maria não tem ExternalSalespersonMapping — simula quem falta na sincronização.

        HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Inativo", ativo=False)

        # Dentro da janela dos últimos 3 meses (jun/jul/ago de 2026).
        for mes, qty in ((6, 30), (7, 60), (8, 90)):
            DistributionBaseline.objects.create(
                ano=2026, mes=mes, salesperson_name="Joao", subgroup_name="LINGUICA_COD", total_quantity=qty
            )
        # Dentro dos 12 meses, mas fora dos últimos 3.
        DistributionBaseline.objects.create(
            ano=2025, mes=9, salesperson_name="Joao", subgroup_name="LINGUICA_COD", total_quantity=120
        )
        # Fora da janela de 12 meses (deve ser ignorado).
        DistributionBaseline.objects.create(
            ano=2025, mes=1, salesperson_name="Joao", subgroup_name="LINGUICA_COD", total_quantity=999
        )
        # Subgrupo sem mapeamento pro catálogo interno (deve ser ignorado, igual ao resto do produto).
        DistributionBaseline.objects.create(
            ano=2026, mes=8, salesperson_name="Joao", subgroup_name="SEM_MAPEAMENTO", total_quantity=50
        )

    def test_computes_3_and_12_month_averages_per_vendor_and_group(self):
        result = VendorGroupSummaryService.summary(today=date(2026, 8, 15))

        self.assertEqual(result["grupos"], [{"id": self.embutidos.id, "nome": "Embutidos"}])

        joao_row = next(v for v in result["vendedores"] if v["id"] == self.joao.id)
        self.assertTrue(joao_row["mapeado"])
        self.assertEqual(
            joao_row["totals"],
            [{"grupo_id": self.embutidos.id, "avg_3_months_kg": 60.0, "avg_12_months_kg": 25.0}],
        )

    def test_includes_local_and_supervisor_ancestry(self):
        result = VendorGroupSummaryService.summary(today=date(2026, 8, 15))

        joao_row = next(v for v in result["vendedores"] if v["id"] == self.joao.id)
        self.assertEqual(joao_row["local_id"], self.local.id)
        self.assertEqual(joao_row["local_nome"], "Local Sul")
        self.assertEqual(joao_row["supervisor_id"], self.supervisor.id)
        self.assertEqual(joao_row["supervisor_nome"], "Supervisor A")

    def test_flags_active_vendor_without_external_mapping(self):
        result = VendorGroupSummaryService.summary(today=date(2026, 8, 15))

        maria_row = next(v for v in result["vendedores"] if v["id"] == self.maria.id)
        self.assertFalse(maria_row["mapeado"])
        self.assertEqual(
            maria_row["totals"],
            [{"grupo_id": self.embutidos.id, "avg_3_months_kg": 0.0, "avg_12_months_kg": 0.0}],
        )

    def test_excludes_inactive_vendedores(self):
        result = VendorGroupSummaryService.summary(today=date(2026, 8, 15))

        self.assertNotIn("Inativo", [v["nome"] for v in result["vendedores"]])


class VendorSubgroupExportServiceTests(TestCase):
    def setUp(self):
        self.embutidos = ProductGroup.objects.create(nome="Embutidos")
        linguica = ProductSubgroup.objects.create(nome="Linguica", group=self.embutidos)
        ExternalProductMapping.objects.create(external_code="LINGUICA_COD", subgroup=linguica)

        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.regional = HierarchyNode.objects.create(
            level=HierarchyNode.Level.REGIONAL, nome="Regional", parent=gerente
        )
        self.local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local Sul", parent=self.regional
        )
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A", parent=self.local
        )
        self.joao = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Joao", ativo=True, parent=supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="Joao", hierarchy_node=self.joao)

        self.maria = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Maria", ativo=True
        )
        # Maria não tem ExternalSalespersonMapping — simula quem falta na sincronização.

        # Dentro da janela dos últimos 3 meses (jun/jul/ago de 2026).
        for mes, qty in ((6, 30), (7, 60), (8, 90)):
            DistributionBaseline.objects.create(
                ano=2026, mes=mes, salesperson_name="Joao", subgroup_name="LINGUICA_COD", total_quantity=qty
            )
        # Dentro dos 12 meses, mas fora dos últimos 3.
        DistributionBaseline.objects.create(
            ano=2025, mes=9, salesperson_name="Joao", subgroup_name="LINGUICA_COD", total_quantity=120
        )

    def test_sums_and_averages_are_grouped_by_calendar_month_not_by_row_count(self):
        rows = VendorSubgroupExportService.rows(today=date(2026, 8, 15))

        joao_row = next(r for r in rows if r.vendedor_nome == "Joao")
        self.assertEqual(joao_row.regional_nome, "Regional")
        self.assertEqual(joao_row.local_nome, "Local Sul")
        self.assertEqual(joao_row.subgrupo_nome, "Linguica")
        # Soma dos 3 meses (30+60+90=180) / 3 meses — não / quantidade de linhas somadas.
        self.assertEqual(joao_row.sum_3_months_kg, 180)
        self.assertEqual(joao_row.avg_3_months_kg, 60.0)
        # Soma dos 12 meses (180+120=300) / 12 meses.
        self.assertEqual(joao_row.sum_12_months_kg, 300)
        self.assertEqual(joao_row.avg_12_months_kg, 25.0)

    def test_excludes_rows_without_any_sale_in_the_last_12_months(self):
        # Maria não tem ExternalSalespersonMapping (sem histórico algum) e por isso não deve
        # aparecer — pedido do usuário (2026-09-04): a base completa não traz combinação
        # vendedor/subgrupo com média 12 meses <= 0.
        rows = VendorSubgroupExportService.rows(today=date(2026, 8, 15))

        self.assertNotIn("Maria", [r.vendedor_nome for r in rows])

    def test_excludes_subgroup_with_zero_sales_in_the_last_12_months(self):
        DistributionBaseline.objects.create(
            ano=2026, mes=8, salesperson_name="Joao", subgroup_name="SEM_VENDA_12M", total_quantity=0
        )
        outro_subgrupo = ProductSubgroup.objects.create(nome="Outro", group=self.embutidos)
        ExternalProductMapping.objects.create(external_code="SEM_VENDA_12M", subgroup=outro_subgrupo)

        rows = VendorSubgroupExportService.rows(today=date(2026, 8, 15))

        self.assertNotIn("Outro", [r.subgrupo_nome for r in rows])


class SalesHistoryReadOnlyGuaranteeTests(SimpleTestCase):
    """Trava a regra do CLAUDE.md: acesso ao Postgres externo de histórico de vendas é
    somente leitura. Se alguém introduzir um DML nas queries verbatim, este teste quebra
    antes que o comando de sync rode contra o banco real.
    """

    WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT")

    def _assert_no_write_statements(self, sql, sql_name):
        upper_sql = sql.upper()
        for keyword in self.WRITE_KEYWORDS:
            self.assertIsNone(
                re.search(rf"\b{keyword}\b", upper_sql), f"{sql_name} contém a palavra {keyword}"
            )

    def test_acumulado_sql_has_no_write_statements(self):
        self._assert_no_write_statements(ACUMULADO_SQL, "ACUMULADO_SQL")

    def test_carteira_sql_has_no_write_statements(self):
        self._assert_no_write_statements(CARTEIRA_SQL, "CARTEIRA_SQL")


class SalesHistorySqlSyntaxTests(SimpleTestCase):
    """Bug real (2026-08-07): editar a lista de `nk_supervisor` verbatim sem vírgula entre dois
    literais adjacentes não quebra nada visível na edição — em Postgres, `'B.F.253''B.F.290'` é
    UM literal só (`''` escapa uma aspa dentro da string), não dois separados por vírgula
    faltando. Só ia acusar erro na hora de o sync rodar contra o banco real. Nenhuma string usada
    aqui precisa de aspa escapada, então `''` em qualquer lugar do SQL é sempre sinal desse bug.
    """

    def test_acumulado_sql_has_no_missing_comma_between_literals(self):
        self.assertNotIn("''", ACUMULADO_SQL)

    def test_carteira_sql_has_no_missing_comma_between_literals(self):
        self.assertNotIn("''", CARTEIRA_SQL)

    def test_acumulado_sql_includes_new_supervisors(self):
        for code in ("B.F.290", "B.F.214"):
            self.assertIn(f"'{code}'", ACUMULADO_SQL)

    def test_carteira_sql_ms_supervisor_list_has_no_duplicate_and_includes_new_supervisors(self):
        match = re.search(r"nk_supervisor IN \(([^)]+)\)\s*AND endereco\.sg_estado = 'MS'", CARTEIRA_SQL)
        self.assertIsNotNone(match, "não achou a lista de supervisores de MS em CARTEIRA_SQL")
        codes = [code.strip().strip("'") for code in match.group(1).split(",")]
        self.assertEqual(len(codes), len(set(codes)), f"lista de MS tem código duplicado: {codes}")
        self.assertIn("B.F.290", codes)
        self.assertIn("B.F.214", codes)
