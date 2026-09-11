from rest_framework import serializers

from .models import FeristaCoverage, HierarchyNode

LEVEL_ORDER = [choice[0] for choice in HierarchyNode.Level.choices]


class HierarchyNodeSerializer(serializers.ModelSerializer):
    level_display = serializers.CharField(source="get_level_display", read_only=True)

    class Meta:
        model = HierarchyNode
        fields = ["id", "level", "level_display", "parent", "nome", "ativo", "is_representante"]

    def validate(self, attrs):
        is_representante = attrs.get("is_representante", getattr(self.instance, "is_representante", False))
        level_for_representante = attrs.get("level", getattr(self.instance, "level", None))
        if is_representante and level_for_representante != HierarchyNode.Level.VENDEDOR:
            raise serializers.ValidationError(
                {"is_representante": "Só um nó Vendedor pode ser Representante."}
            )
        if is_representante and self.instance is not None and self.instance.users.exists():
            raise serializers.ValidationError(
                {"is_representante": "Esse nó já tem usuário vinculado — não pode virar Representante."}
            )

        # Só reexamina a relação nível/pai quando um dos dois está de fato mudando — um PATCH que
        # só mexe em `ativo` (ex.: desativar um nó órfão) não pode ser bloqueado por uma
        # inconsistência herdada de antes, que não tem a ver com o que está sendo salvo agora.
        if "level" not in attrs and "parent" not in attrs:
            return attrs

        level = attrs.get("level", getattr(self.instance, "level", None))
        if "parent" in attrs:
            parent = attrs["parent"]
        elif self.instance is not None:
            parent = self.instance.parent
        else:
            parent = None

        # Mesma checagem que `HierarchyNode.clean()` já faz pro caminho do Django Admin — esse
        # serializer (caminho da API/SPA) nunca chamava `full_clean()`, então um nó podia virar
        # pai de si mesmo sem barrar aqui (achado investigando um bug real de duplicação de nó,
        # 2026-07-22).
        if self.instance is not None and parent is not None and parent.id == self.instance.id:
            raise serializers.ValidationError({"parent": "Um nó não pode ser seu próprio superior."})

        level_index = LEVEL_ORDER.index(level)
        if level_index == 0:
            if parent is not None:
                raise serializers.ValidationError({"parent": "Gerente não pode ter nó pai."})
        else:
            if parent is None:
                raise serializers.ValidationError({"parent": "Esse nível exige um nó pai."})
            expected_parent_level = LEVEL_ORDER[level_index - 1]
            if parent.level != expected_parent_level:
                raise serializers.ValidationError(
                    {
                        "parent": (
                            f"Pai precisa ser do nível {expected_parent_level}, "
                            f"mas o nó escolhido é {parent.level}."
                        )
                    }
                )
        return attrs


class FeristaCoverageSerializer(serializers.ModelSerializer):
    covering_node_nome = serializers.CharField(source="covering_node.nome", read_only=True)
    covered_node_nome = serializers.CharField(source="covered_node.nome", read_only=True)

    class Meta:
        model = FeristaCoverage
        fields = [
            "id",
            "covering_node",
            "covering_node_nome",
            "covered_node",
            "covered_node_nome",
            "ano",
            "mes",
        ]

    def validate_covering_node(self, value):
        if value.level != HierarchyNode.Level.VENDEDOR:
            raise serializers.ValidationError("O ferista precisa ser um Vendedor.")
        return value

    def validate_covered_node(self, value):
        if value.level != HierarchyNode.Level.VENDEDOR:
            raise serializers.ValidationError("O nó coberto precisa ser um Vendedor.")
        return value

    def validate_mes(self, value):
        if not 1 <= value <= 12:
            raise serializers.ValidationError("Mês precisa estar entre 1 e 12.")
        return value

    def validate(self, attrs):
        covering_node = attrs.get("covering_node", getattr(self.instance, "covering_node", None))
        covered_node = attrs.get("covered_node", getattr(self.instance, "covered_node", None))
        ano = attrs.get("ano", getattr(self.instance, "ano", None))
        mes = attrs.get("mes", getattr(self.instance, "mes", None))

        if covering_node is not None and covering_node == covered_node:
            raise serializers.ValidationError({"covering_node": "O ferista não pode cobrir a si mesmo."})

        # Só o titular tem restrição de unicidade por mês (não dá pra fatiar a rota entre dois
        # feristas) — o mesmo ferista pode cobrir vários titulares no mesmo mês sem problema.
        covered_conflict = FeristaCoverage.objects.filter(covered_node=covered_node, ano=ano, mes=mes)
        if self.instance is not None:
            covered_conflict = covered_conflict.exclude(pk=self.instance.pk)
        if covered_conflict.exists():
            raise serializers.ValidationError(
                {"mes": f"{covered_node.nome} já tem cobertura cadastrada em {mes:02d}/{ano}."}
            )
        return attrs
