from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

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

        # months_back=11 (não 12): first_day_n_months_ago já volta a partir do mês atual, então
        # 11 meses pra trás + o mês atual = janela de 12 meses — a mesma conta que o CLI
        # `sync_sales_history` faz por padrão (--months=12 → months_back = 12 - 1). Ver
        # apps/sales_history/views.py — antes desse teste travava um bug (months_back=12 dava
        # 13 meses, diferente da janela que o CLI sincronizava).
        mock_min_date.assert_called_once_with(date.today(), months_back=11)
        mock_accumulated.assert_called_once_with(date(2025, 8, 1))
