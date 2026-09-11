from django.contrib import admin

from .models import AccumulatedSale, ClientPortfolioSnapshot, DistributionBaseline


class ReadOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccumulatedSale)
class AccumulatedSaleAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "sale_date",
        "nk_vendedor",
        "salesperson_name",
        "subgroup_name",
        "total_quantity",
        "total_value",
    )
    list_filter = ("subgroup_name", "nk_supervisor")
    search_fields = ("nk_vendedor", "salesperson_name", "client_name", "cnpj")


@admin.register(ClientPortfolioSnapshot)
class ClientPortfolioSnapshotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("client_name", "salesperson_name", "estado", "municipio")
    list_filter = ("estado",)
    search_fields = ("client_name", "salesperson_name", "cnpj")


@admin.register(DistributionBaseline)
class DistributionBaselineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("ano", "mes", "salesperson_name", "subgroup_name", "total_quantity")
    list_filter = ("ano", "mes", "subgroup_name")
    search_fields = ("salesperson_name",)
