from django.urls import path

from .views import (
    ClientAccumuladoView,
    ClientesSemCompraExportView,
    SyncDataView,
    VendorGroupSummaryView,
    VendorSubgroupExportView,
)

urlpatterns = [
    path("sales-history/sync/", SyncDataView.as_view(), name="sales-history-sync"),
    path(
        "sales-history/vendor-group-summary/",
        VendorGroupSummaryView.as_view(),
        name="sales-history-vendor-group-summary",
    ),
    path(
        "sales-history/vendor-subgroup-export/",
        VendorSubgroupExportView.as_view(),
        name="sales-history-vendor-subgroup-export",
    ),
    path(
        "sales-history/results/acumulado-clientes/",
        ClientAccumuladoView.as_view(),
        name="sales-history-acumulado-clientes",
    ),
    path(
        "sales-history/results/acumulado-clientes/clientes-sem-compra-export/",
        ClientesSemCompraExportView.as_view(),
        name="sales-history-clientes-sem-compra-export",
    ),
]
