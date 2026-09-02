import re
import threading
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from .models import AccumulatedSale, ClientPortfolioSnapshot, DistributionBaseline
from .queries import ACUMULADO_SQL, CARTEIRA_SQL
from .services import DistributionBaselineService, SalesHistorySyncService, sync_lock


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
