from rest_framework import serializers

from .models import GoalAllocation
from .services import SelfVendedorAutoDistributionService


class GoalAllocationSerializer(serializers.ModelSerializer):
    owner_node_level = serializers.CharField(source="owner_node.level", read_only=True)
    owner_node_nome = serializers.CharField(source="owner_node.nome", read_only=True)
    owner_node_usernames = serializers.SerializerMethodField()
    group_nome = serializers.SerializerMethodField()
    subgroup_nome = serializers.SerializerMethodField()
    has_further_distribution = serializers.SerializerMethodField()

    class Meta:
        model = GoalAllocation
        fields = [
            "id",
            "cycle",
            "owner_node",
            "owner_node_level",
            "owner_node_nome",
            "owner_node_usernames",
            "parent_allocation",
            "granularity",
            "group",
            "group_nome",
            "subgroup",
            "subgroup_nome",
            "product",
            "quantity_kg",
            "distributed",
            "has_further_distribution",
            "criado_por",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_owner_node_usernames(self, obj) -> list[str]:
        return [user.username for user in obj.owner_node.users.all()]

    def get_group_nome(self, obj) -> str | None:
        if obj.group_id:
            return obj.group.nome
        if obj.subgroup_id:
            return obj.subgroup.group.nome
        return None

    def get_subgroup_nome(self, obj) -> str | None:
        return obj.subgroup.nome if obj.subgroup_id else None

    def get_has_further_distribution(self, obj) -> bool:
        """Trava do botão "Resetar distribuição": algum filho direto já repassou adiante o que
        recebeu, com trabalho real por trás (revisão 2026-09-03) — não conta um filho com 0 kg,
        nem um repasse automático de autogestão (`SelfVendedorAutoDistributionService`, único alvo
        possível, não é decisão de ninguém), mesmo com quantidade > 0. `GoalAllocationViewSet.
        get_queryset` já anota isso via subquery (evita N+1 numa lista); quando a anotação não
        existe (resposta de `distribute`/`split-subgroups`/`reopen`, sobre um objeto recém-criado/
        recarregado fora daquele queryset), cai pra uma checagem direta nos poucos filhos desse
        objeto — só acontece em respostas de objeto único, sem custo de lista."""
        annotated = getattr(obj, "has_further_distribution", None)
        if annotated is not None:
            return bool(annotated)
        candidates = obj.children.filter(distributed=True, quantity_kg__gt=0).select_related("owner_node")
        return any(
            not SelfVendedorAutoDistributionService.is_self_managed_supervisor(child.owner_node)
            for child in candidates
        )


class AllocationOverviewSerializer(serializers.ModelSerializer):
    """Visão do Administrador: quem possui cada alocação do ciclo, não só o nó dono — usada para
    identificar quais usuários ainda não distribuíram (não é escopada por `visible_to`, é
    deliberadamente global; só é servida atrás de `IsAppAdmin`)."""

    owner_node_level = serializers.CharField(source="owner_node.level", read_only=True)
    owner_node_nome = serializers.CharField(source="owner_node.nome", read_only=True)
    owner_node_parent_id = serializers.IntegerField(source="owner_node.parent_id", read_only=True)
    owner_node_parent_nome = serializers.SerializerMethodField()
    owner_node_usernames = serializers.SerializerMethodField()
    criado_por_username = serializers.CharField(source="criado_por.username", read_only=True)
    group_nome = serializers.SerializerMethodField()
    subgroup_nome = serializers.SerializerMethodField()

    class Meta:
        model = GoalAllocation
        fields = [
            "id",
            "cycle",
            "owner_node",
            "owner_node_level",
            "owner_node_nome",
            "owner_node_parent_id",
            "owner_node_parent_nome",
            "owner_node_usernames",
            "parent_allocation",
            "granularity",
            "group_nome",
            "subgroup_nome",
            "quantity_kg",
            "distributed",
            "criado_por_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_owner_node_usernames(self, obj) -> list[str]:
        return [user.username for user in obj.owner_node.users.all()]

    def get_owner_node_parent_nome(self, obj) -> str | None:
        return obj.owner_node.parent.nome if obj.owner_node.parent_id else None

    def get_group_nome(self, obj) -> str | None:
        if obj.group_id:
            return obj.group.nome
        if obj.subgroup_id:
            return obj.subgroup.group.nome
        return None

    def get_subgroup_nome(self, obj) -> str | None:
        return obj.subgroup.nome if obj.subgroup_id else None


class ChildAllocationInputSerializer(serializers.Serializer):
    owner_node_id = serializers.IntegerField()
    quantity_kg = serializers.IntegerField(min_value=0)
    granularity = serializers.ChoiceField(choices=GoalAllocation.Granularity.choices)
    group_id = serializers.IntegerField(required=False, allow_null=True)
    subgroup_id = serializers.IntegerField(required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)


class DistributeRequestSerializer(serializers.Serializer):
    children = ChildAllocationInputSerializer(many=True)


class MonthlyPointSerializer(serializers.Serializer):
    ano = serializers.IntegerField()
    mes = serializers.IntegerField()
    quantity_kg = serializers.FloatField()


class GroupSuggestionSerializer(serializers.Serializer):
    """P1 — sugestão por grupo com o breakdown auditável (Decisão 6)."""

    group_id = serializers.IntegerField()
    group_nome = serializers.CharField()
    trend_kg = serializers.IntegerField()
    seasonal_index = serializers.FloatField()
    suggested_kg = serializers.IntegerField()
    has_gap = serializers.BooleanField()
    same_month_last_year_kg = serializers.FloatField(allow_null=True)
    history = MonthlyPointSerializer(many=True)
    already_created = serializers.BooleanField()


class ChildDistributionContextSerializer(serializers.Serializer):
    """Contexto histórico por alvo direto, exibido na tela de distribuição (Gerente→Local)
    — nunca substitui a decisão manual, só informa."""

    owner_node_id = serializers.IntegerField()
    history = MonthlyPointSerializer(many=True)
    same_month_last_year_kg = serializers.FloatField(allow_null=True)
    last_3_months_avg_kg = serializers.FloatField(allow_null=True)
    historical_share_pct = serializers.FloatField(allow_null=True)
    has_gap = serializers.BooleanField()
    suggested_kg = serializers.IntegerField(allow_null=True)


class SubgroupDistributionContextSerializer(serializers.Serializer):
    """Contexto histórico por subgrupo, exibido na tela "Distribuir Produtos" (grupo→subgrupo do
    Coordenador Local) — mesma forma de `ChildDistributionContextSerializer`, chaveada por
    subgrupo em vez de nó."""

    subgroup_id = serializers.IntegerField()
    subgroup_nome = serializers.CharField()
    history = MonthlyPointSerializer(many=True)
    same_month_last_year_kg = serializers.FloatField(allow_null=True)
    last_3_months_avg_kg = serializers.FloatField(allow_null=True)
    historical_share_pct = serializers.FloatField(allow_null=True)
    has_gap = serializers.BooleanField()
    suggested_kg = serializers.IntegerField(allow_null=True)


class SubgroupSplitInputSerializer(serializers.Serializer):
    subgroup_id = serializers.IntegerField()
    quantity_kg = serializers.IntegerField(min_value=0)


class SplitSubgroupsRequestSerializer(serializers.Serializer):
    """Corpo de `POST /allocations/{id}/split-subgroups/` — tela "Distribuir Produtos"."""

    subgroups = SubgroupSplitInputSerializer(many=True)


class CreateRootAllocationSerializer(serializers.Serializer):
    cycle_id = serializers.IntegerField()
    owner_node_id = serializers.IntegerField()
    granularity = serializers.ChoiceField(choices=GoalAllocation.Granularity.choices)
    quantity_kg = serializers.IntegerField(min_value=0)
    group_id = serializers.IntegerField(required=False, allow_null=True)
    subgroup_id = serializers.IntegerField(required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)


class ResetGroupRequestSerializer(serializers.Serializer):
    """Telas "Meta Supervisor"/"Meta Vendedor" (SubgroupCascadeWorkspace): reset em lote de todos
    os subgrupos já distribuídos de um grupo, pro nível que os possui neste ciclo."""

    cycle_id = serializers.IntegerField()
    owner_node_id = serializers.IntegerField()
    group_id = serializers.IntegerField()
