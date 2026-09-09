from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import ExternalProductMapping, ProductGroup, ProductSubgroup
from apps.hierarchy.models import ExternalSalespersonMapping, HierarchyNode

from .models import DistributionBaseline

User = get_user_model()


class SyncDataViewTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.non_admin = User.objects.create_user(username="regular", password="x")

    def test_requires_authentication(self):
        response = self.client.post(reverse("sales-history-sync"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_rejects_non_admin(self):
        self.client.force_login(self.non_admin)

        response = self.client.post(reverse("sales-history-sync"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("apps.sales_history.views.DistributionBaselineService.rebuild")
    @patch("apps.sales_history.views.SalesHistorySyncService.sync_portfolio")
    @patch("apps.sales_history.views.SalesHistorySyncService.sync_accumulated")
    def test_admin_triggers_sync_and_rebuild(self, mock_accumulated, mock_portfolio, mock_rebuild):
        mock_accumulated.return_value = 10
        mock_portfolio.return_value = 5
        mock_rebuild.return_value = 3
        self.client.force_login(self.admin)

        response = self.client.post(reverse("sales-history-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["accumulated_count"], 10)
        self.assertEqual(response.data["portfolio_count"], 5)
        self.assertEqual(response.data["baseline_count"], 3)
        mock_accumulated.assert_called_once()
        mock_portfolio.assert_called_once()
        mock_rebuild.assert_called_once()

    @patch("apps.sales_history.views.first_day_n_months_ago")
    @patch("apps.sales_history.views.DistributionBaselineService.rebuild", return_value=0)
    @patch("apps.sales_history.views.SalesHistorySyncService.sync_portfolio", return_value=0)
    @patch("apps.sales_history.views.SalesHistorySyncService.sync_accumulated", return_value=0)
    def test_uses_twelve_month_window(self, mock_accumulated, mock_portfolio, mock_rebuild, mock_min_date):
        mock_min_date.return_value = date(2025, 8, 1)
        self.client.force_login(self.admin)

        self.client.post(reverse("sales-history-sync"))

        # months_back=12: âncora em mês atual - 1 e 12 meses completos pra trás daí — a mesma
        # conta que o CLI `sync_sales_history` faz por padrão (--months=12 → months_back=12). Ver
        # apps/sales_history/views.py. O mês corrente (ainda em andamento) nunca entra na janela,
        # senão o cálculo sazonal de P1 fica sem o mesmo mês do ano anterior (ver Decisão 6, revisão).
        mock_min_date.assert_called_once_with(date.today(), months_back=12)
        mock_accumulated.assert_called_once_with(date(2025, 8, 1))


class VendorGroupSummaryViewTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.non_admin = User.objects.create_user(username="regular", password="x")
        HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Joao", ativo=True)

    def test_requires_authentication(self):
        response = self.client.get(reverse("sales-history-vendor-group-summary"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_rejects_non_admin(self):
        self.client.force_login(self.non_admin)

        response = self.client.get(reverse("sales-history-vendor-group-summary"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_gets_vendor_rows_including_unmapped(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("sales-history-vendor-group-summary"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["vendedores"]), 1)
        self.assertEqual(response.data["vendedores"][0]["nome"], "Joao")
        self.assertFalse(response.data["vendedores"][0]["mapeado"])


class VendorSubgroupExportViewTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.non_admin = User.objects.create_user(username="regular", password="x")

        embutidos = ProductGroup.objects.create(nome="Embutidos")
        linguica = ProductSubgroup.objects.create(nome="Linguica", group=embutidos)
        ExternalProductMapping.objects.create(external_code="LINGUICA_COD", subgroup=linguica)

        joao = HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="Joao", ativo=True)
        ExternalSalespersonMapping.objects.create(external_name="Joao", hierarchy_node=joao)
        today = date.today()
        DistributionBaseline.objects.create(
            ano=today.year,
            mes=today.month,
            salesperson_name="Joao",
            subgroup_name="LINGUICA_COD",
            total_quantity=30,
        )

        # Sem nenhuma venda nos últimos 12 meses — não deve aparecer no CSV (pedido do usuário,
        # 2026-09-04: a base completa não traz média 12 meses <= 0).
        HierarchyNode.objects.create(level=HierarchyNode.Level.VENDEDOR, nome="SemVenda", ativo=True)

    def test_requires_authentication(self):
        response = self.client.get(reverse("sales-history-vendor-subgroup-export"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_rejects_non_admin(self):
        self.client.force_login(self.non_admin)

        response = self.client.get(reverse("sales-history-vendor-subgroup-export"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_downloads_csv_with_header_and_vendor_row(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("sales-history-vendor-subgroup-export"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.getvalue().decode("utf-8")
        lines = content.strip().splitlines()
        self.assertEqual(
            lines[0],
            "coordenador_regional,coordenador_local,vendedor,subgrupo,"
            "soma_3_meses_kg,soma_12_meses_kg,media_3_meses_kg,media_12_meses_kg",
        )
        self.assertIn("Joao", lines[1])
        self.assertNotIn("SemVenda", content)
