from django.urls import path

from .views import SyncDataView, VendorGroupSummaryView, VendorSubgroupExportView

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
]
