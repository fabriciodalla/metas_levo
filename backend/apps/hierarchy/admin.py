from django.contrib import admin

from .models import ExternalSalespersonMapping, FeristaCoverage, HierarchyClosure, HierarchyNode


@admin.register(HierarchyNode)
class HierarchyNodeAdmin(admin.ModelAdmin):
    list_display = ("nome", "level", "parent", "ativo", "is_representante")
    list_filter = ("level", "ativo", "is_representante")
    search_fields = ("nome",)

    def save_model(self, request, obj, form, change):
        previous = HierarchyNode.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)

        # Import tardio: allocations importa hierarchy.models, evita ciclo com hierarchy.admin.
        from apps.allocations.services import HierarchyChangeReassignmentService

        HierarchyChangeReassignmentService.detect_and_reassign_if_needed(
            previous, obj, changed_by=request.user
        )


@admin.register(HierarchyClosure)
class HierarchyClosureAdmin(admin.ModelAdmin):
    list_display = ("ancestor", "descendant", "depth")
    list_filter = ("depth",)


@admin.register(ExternalSalespersonMapping)
class ExternalSalespersonMappingAdmin(admin.ModelAdmin):
    list_display = ("external_name", "hierarchy_node")
    search_fields = ("external_name", "hierarchy_node__nome")


@admin.register(FeristaCoverage)
class FeristaCoverageAdmin(admin.ModelAdmin):
    list_display = ("covering_node", "covered_node", "mes", "ano")
    list_filter = ("ano", "mes")
    search_fields = ("covering_node__nome", "covered_node__nome")
