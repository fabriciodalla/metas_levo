from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import ExternalProductMapping, ProductGroup, ProductSubgroup
from apps.hierarchy.models import ExternalSalespersonMapping, HierarchyNode

from .client_results import ClientResultsService
from .models import AccumulatedSale, ClientPortfolioSnapshot

User = get_user_model()


def _sale(
    ano: int, mes: int, dia: int, client_code: int, client_name: str, subgroup: str, qty: float
) -> None:
    AccumulatedSale.objects.create(
        nk_supervisor="SUP1",
        nk_vendedor="VEND1",
        salesperson_name="Vendedor A",
        client_code=client_code,
        client_name=client_name,
        sale_date=date(ano, mes, dia),
        subgroup_name=subgroup,
        total_quantity=qty,
        total_value=qty * 10,
    )


class ClientResultsServiceTests(TestCase):
    def setUp(self):
        self.ano = 2026
        self.mes = 3
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor"
        )
        self.vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor A", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="Vendedor A", hierarchy_node=self.vendedor)

        ClientPortfolioSnapshot.objects.create(
            client_code=1, client_name="Cliente Ativo", salesperson_name="Vendedor A"
        )
        ClientPortfolioSnapshot.objects.create(
            client_code=2, client_name="Cliente Novo", salesperson_name="Vendedor A"
        )
        ClientPortfolioSnapshot.objects.create(
            client_code=3, client_name="Cliente Sumido", salesperson_name="Vendedor A"
        )
        ClientPortfolioSnapshot.objects.create(
            client_code=4, client_name="Cliente Nunca Comprou", salesperson_name="Vendedor A"
        )

    def test_positivacao_e_ativos_do_ciclo_e_mes_anterior(self):
        # Numerador (ciclo, março/2026): vem do histórico de vendas -> clientes 1 e 2 ativos.
        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 120)
        _sale(2026, 3, 20, 2, "Cliente Novo", "COXA", 30)
        # Denominador ("mês anterior"): vem da CARTEIRA via `last_changed_at`, ACUMULADO até o fim
        # de fevereiro/2026 (<=), não uma janela fechada só de fevereiro -> clientes 1 e 3 contam
        # (alterados em janeiro e fevereiro, ambos <= fim de fevereiro); cliente 4 (nunca alterado,
        # `last_changed_at=None`) não conta.
        ClientPortfolioSnapshot.objects.filter(client_code=1).update(last_changed_at=date(2026, 1, 5))
        ClientPortfolioSnapshot.objects.filter(client_code=3).update(last_changed_at=date(2026, 2, 15))

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.carteira_total, 4)
        self.assertEqual(result.clientes_ativos, 2)
        self.assertEqual(result.clientes_ativos_mes_anterior, 2)
        self.assertAlmostEqual(result.positivacao_pct, 1.0)

    def test_positivacao_e_none_sem_base_no_mes_anterior(self):
        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 100)
        # Nenhum cliente da carteira com `last_changed_at` preenchido -> denominador zero.

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.clientes_ativos_mes_anterior, 0)
        self.assertIsNone(result.positivacao_pct)

    def test_positivacao_mes_anterior_ignora_vendas_e_usa_so_a_carteira(self):
        # Cliente 3 comprou em fevereiro (venda), mas isso não deve contar pro denominador — só
        # quem tem `last_changed_at` preenchido na carteira conta.
        _sale(2026, 2, 10, 3, "Cliente Sumido", "PEITO", 50)
        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 100)
        ClientPortfolioSnapshot.objects.filter(client_code=1).update(last_changed_at=date(2026, 2, 20))

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.clientes_ativos_mes_anterior, 1)

    def test_positivacao_mes_anterior_e_acumulado_nao_janela_fechada(self):
        # `last_changed_at` bem antigo (não só do mês de fevereiro) ainda conta pro denominador —
        # é "até o fim do mês anterior", não uma janela fechada só daquele mês.
        ClientPortfolioSnapshot.objects.filter(client_code=1).update(last_changed_at=date(2020, 1, 1))
        # `last_changed_at` dentro do próprio ciclo (março) não deve contar pro mês anterior.
        ClientPortfolioSnapshot.objects.filter(client_code=3).update(last_changed_at=date(2026, 3, 1))

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.clientes_ativos_mes_anterior, 1)

    def test_captacao_conta_pela_ultima_alteracao_cadastral_na_carteira(self):
        # Cliente 2 teve a carteira alterada (entrou/foi atualizado) dentro do ciclo -> captação.
        ClientPortfolioSnapshot.objects.filter(client_code=2).update(last_changed_at=date(2026, 3, 10))
        # Cliente 1 já era cliente antigo (alterado antes do ciclo) -> não conta, mesmo comprando.
        ClientPortfolioSnapshot.objects.filter(client_code=1).update(last_changed_at=date(2025, 1, 5))
        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 100)
        _sale(2026, 3, 20, 2, "Cliente Novo", "COXA", 30)

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.captacao, 1)

    def test_captacao_nao_depende_de_ter_comprado_no_ciclo(self):
        # Alterado dentro do ciclo mas sem nenhuma venda registrada -> ainda conta como captação,
        # porque a fonte é a carteira, não o histórico de vendas.
        ClientPortfolioSnapshot.objects.filter(client_code=2).update(last_changed_at=date(2026, 3, 1))

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.captacao, 1)

    def test_ticket_medio_usa_peso_total_dos_ativos_do_ciclo(self):
        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 100)  # 100 kg
        _sale(2026, 3, 20, 2, "Cliente Novo", "COXA", 50)  # 50 kg

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        # (100 + 50) kg / 2 clientes ativos = 75 kg por cliente.
        self.assertAlmostEqual(result.ticket_medio_kg, 75.0)

    def test_positivacao_meta_pct_e_fixa_em_65_por_cento(self):
        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertAlmostEqual(result.positivacao_meta_pct, 0.65)

    def test_captacao_meta_e_2_vezes_vendedores_ativos_no_escopo(self):
        # 1 vendedor ativo no escopo do supervisor (self.vendedor, do setUp) -> meta = 2.
        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.captacao_meta, 2)

    def test_captacao_meta_ignora_vendedor_inativo(self):
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor Inativo", parent=self.supervisor, ativo=False
        )

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.captacao_meta, 2)

    def test_captacao_meta_soma_pela_hierarquia(self):
        # Gerente -> Local -> [Supervisor A (2 vendedores), Supervisor B (1 vendedor)].
        gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=gerente)
        supervisor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A", parent=local
        )
        supervisor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor B", parent=local
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 1", parent=supervisor_a
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 2", parent=supervisor_a
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 3", parent=supervisor_b
        )

        # Supervisor = 2 * nº de vendedores dele.
        self.assertEqual(ClientResultsService.build(supervisor_a, self.ano, self.mes).captacao_meta, 4)
        self.assertEqual(ClientResultsService.build(supervisor_b, self.ano, self.mes).captacao_meta, 2)
        # Coordenador Local = soma da meta dos supervisores dele.
        self.assertEqual(ClientResultsService.build(local, self.ano, self.mes).captacao_meta, 6)
        # Gerente = soma da meta dos coordenadores dele.
        self.assertEqual(ClientResultsService.build(gerente, self.ano, self.mes).captacao_meta, 6)

    def test_base_clientes_meta_soma_carteira_mes_anterior_e_captacao_meta(self):
        # clientes_ativos_mes_anterior = 2 (clientes 1 e 3, alterados até fim de fevereiro) +
        # captacao_meta = 2 (1 vendedor ativo no escopo) = 4.
        ClientPortfolioSnapshot.objects.filter(client_code=1).update(last_changed_at=date(2026, 1, 5))
        ClientPortfolioSnapshot.objects.filter(client_code=3).update(last_changed_at=date(2026, 2, 15))

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.clientes_ativos_mes_anterior, 2)
        self.assertEqual(result.captacao_meta, 2)
        self.assertEqual(result.base_clientes_meta, 4)

    def test_clientes_ativos_meta_arredonda_65_por_cento_meio_pra_cima(self):
        # 10 clientes na carteira acumulada até o mês anterior -> 10 * 65% = 6,5, que deve
        # arredondar pra 7 (>= 0,5 sobe, mesma convenção de arredondamento do resto do produto).
        for code in range(100, 110):
            ClientPortfolioSnapshot.objects.create(
                client_code=code,
                client_name=f"Cliente {code}",
                salesperson_name="Vendedor A",
                last_changed_at=date(2026, 1, 1),
            )

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.clientes_ativos_mes_anterior, 10)
        self.assertEqual(result.clientes_ativos_meta, 7)

    def test_ticket_medio_por_grupo_separa_frangos_de_revenda(self):
        frangos = ProductGroup.objects.create(nome="FRANGOS")
        revenda = ProductGroup.objects.create(nome="REVENDA")
        peito = ProductSubgroup.objects.create(nome="Peito", group=frangos)
        embutido = ProductSubgroup.objects.create(nome="Embutido", group=revenda)
        ExternalProductMapping.objects.create(external_code="PEITO", subgroup=peito)
        ExternalProductMapping.objects.create(external_code="COXA", subgroup=peito)
        ExternalProductMapping.objects.create(external_code="EMBUTIDO", subgroup=embutido)

        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 100)  # cliente 1 só compra Frangos
        _sale(2026, 3, 20, 2, "Cliente Novo", "COXA", 50)  # cliente 2 também só Frangos
        _sale(2026, 3, 20, 2, "Cliente Novo", "EMBUTIDO", 20)  # cliente 2 também compra Revenda

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        por_grupo = {g.grupo_nome: g for g in result.ticket_medio_por_grupo}
        # Frangos: (100 + 50) kg / 2 clientes que compraram do grupo.
        self.assertEqual(por_grupo["FRANGOS"].clientes_ativos, 2)
        self.assertAlmostEqual(por_grupo["FRANGOS"].ticket_medio_kg, 75.0)
        # Revenda: 20 kg / 1 cliente.
        self.assertEqual(por_grupo["REVENDA"].clientes_ativos, 1)
        self.assertAlmostEqual(por_grupo["REVENDA"].ticket_medio_kg, 20.0)

    def test_clientes_sem_compra_traz_ultima_venda_itens_por_subgrupo_e_peso(self):
        _sale(2026, 3, 5, 1, "Cliente Ativo", "PEITO", 100)  # ativo -> não entra na lista
        _sale(2026, 1, 10, 3, "Cliente Sumido", "PEITO", 40)
        _sale(2026, 1, 10, 3, "Cliente Sumido", "COXA", 10)
        _sale(2025, 12, 1, 3, "Cliente Sumido", "ASA", 999)  # venda mais antiga, não deve contar

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        self.assertEqual(result.clientes_sem_compra_count, 3)
        sumido = next(row for row in result.clientes_sem_compra if row.client_code == 3)
        self.assertEqual((sumido.ultima_compra_ano, sumido.ultima_compra_mes), (2026, 1))
        itens = {item.subgroup_name: item.peso_kg for item in sumido.itens_ultima_compra}
        self.assertEqual(itens, {"COXA": 10.0, "PEITO": 40.0})
        self.assertAlmostEqual(sumido.peso_ultima_compra_kg, 50.0)

        nunca_comprou = next(row for row in result.clientes_sem_compra if row.client_code == 4)
        self.assertIsNone(nunca_comprou.ultima_compra_ano)
        self.assertEqual(nunca_comprou.itens_ultima_compra, [])
        self.assertEqual(nunca_comprou.peso_ultima_compra_kg, 0.0)

    def test_clientes_sem_compra_soma_por_subgrupo_quando_ha_linhas_duplicadas(self):
        # `AccumulatedSale` pode ter mais de uma linha pro mesmo subgrupo/data (empresas
        # diferentes na mesma venda) — o item do subgrupo tem que somar as duas, não sobrescrever.
        AccumulatedSale.objects.create(
            nk_supervisor="SUP1",
            nk_vendedor="VEND1",
            salesperson_name="Vendedor A",
            client_code=3,
            client_name="Cliente Sumido",
            sale_date=date(2026, 1, 10),
            subgroup_name="PEITO",
            total_quantity=40,
            total_value=400,
        )
        AccumulatedSale.objects.create(
            nk_supervisor="SUP2",
            nk_vendedor="VEND1",
            salesperson_name="Vendedor A",
            client_code=3,
            client_name="Cliente Sumido",
            sale_date=date(2026, 1, 10),
            subgroup_name="PEITO",
            total_quantity=15,
            total_value=150,
        )

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        sumido = next(row for row in result.clientes_sem_compra if row.client_code == 3)
        self.assertEqual(len(sumido.itens_ultima_compra), 1)
        self.assertAlmostEqual(sumido.itens_ultima_compra[0].peso_kg, 55.0)

    def test_clientes_sem_compra_ordenados_por_maior_peso_primeiro(self):
        _sale(2026, 1, 10, 3, "Cliente Sumido", "PEITO", 10)
        # cliente fora da carteira dos 4 criados no setUp, adicionado só pra este teste de ordenação.
        ClientPortfolioSnapshot.objects.create(
            client_code=5, client_name="Cliente Grande Sumido", salesperson_name="Vendedor A"
        )
        _sale(2026, 1, 15, 5, "Cliente Grande Sumido", "PEITO", 500)

        result = ClientResultsService.build(self.supervisor, self.ano, self.mes)

        codes_sem_compra = [row.client_code for row in result.clientes_sem_compra]
        self.assertLess(codes_sem_compra.index(5), codes_sem_compra.index(3))


class ClientAccumuladoViewTests(APITestCase):
    def setUp(self):
        self.ano = 2026
        self.mes = 3
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )
        self.user_a = User.objects.create_user(username="user_a", password="x", hierarchy_node=self.local_a)
        self.client.force_login(self.user_a)

    def test_requires_ano_mes_and_node_query_params(self):
        response = self.client.get(reverse("sales-history-acumulado-clientes"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_mes_out_of_range(self):
        response = self.client.get(
            reverse("sales-history-acumulado-clientes"),
            {"ano": self.ano, "mes": 13, "node": self.local_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_non_numeric_ano_mes(self):
        response = self.client.get(
            reverse("sales-history-acumulado-clientes"),
            {"ano": "abc", "mes": self.mes, "node": self.local_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_cannot_view_a_node_outside_their_branch(self):
        response = self.client.get(
            reverse("sales-history-acumulado-clientes"),
            {"ano": self.ano, "mes": self.mes, "node": self.local_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_response_shape(self):
        response = self.client.get(
            reverse("sales-history-acumulado-clientes"),
            {"ano": self.ano, "mes": self.mes, "node": self.local_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["ano"], self.ano)
        self.assertEqual(response.data["mes"], self.mes)
        self.assertIn("positivacao_pct", response.data)
        self.assertIn("positivacao_meta_pct", response.data)
        self.assertIn("captacao", response.data)
        self.assertIn("captacao_meta", response.data)
        self.assertIn("base_clientes_meta", response.data)
        self.assertIn("clientes_ativos_meta", response.data)
        self.assertIn("ticket_medio_por_grupo", response.data)
        self.assertIn("clientes_sem_compra", response.data)


class ClientesSemCompraExportViewTests(APITestCase):
    def setUp(self):
        self.ano = 2026
        self.mes = 3
        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local_a
        )
        self.vendedor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor A", parent=self.supervisor
        )
        ExternalSalespersonMapping.objects.create(external_name="Vendedor A", hierarchy_node=self.vendedor)
        ClientPortfolioSnapshot.objects.create(
            client_code=1, client_name="cliente sumido", salesperson_name="Vendedor A"
        )
        _sale(2026, 1, 10, 1, "cliente sumido", "PEITO", 40)
        _sale(2026, 1, 10, 1, "cliente sumido", "COXA", 10)

        self.user_a = User.objects.create_user(username="user_a", password="x", hierarchy_node=self.local_a)
        self.client.force_login(self.user_a)

    def test_requires_ano_mes_and_node_query_params(self):
        response = self.client.get(reverse("sales-history-clientes-sem-compra-export"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_cannot_export_a_node_outside_their_branch(self):
        response = self.client.get(
            reverse("sales-history-clientes-sem-compra-export"),
            {"ano": self.ano, "mes": self.mes, "node": self.local_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_csv_tem_uma_linha_por_cliente_e_subgrupo_com_cliente_em_maiuscula(self):
        response = self.client.get(
            reverse("sales-history-clientes-sem-compra-export"),
            {"ano": self.ano, "mes": self.mes, "node": self.local_a.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        lines = response.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(lines[0], "clifor,cliente,ultima_compra,subgrupo,peso_kg")
        # Desagrupado: uma linha por subgrupo da última compra, não uma linha por cliente.
        self.assertEqual(len(lines), 3)
        self.assertIn("1,CLIENTE SUMIDO,01/2026,COXA,10.00", lines)
        self.assertIn("1,CLIENTE SUMIDO,01/2026,PEITO,40.00", lines)
