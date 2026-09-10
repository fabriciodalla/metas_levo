from rest_framework import serializers

from .models import Cycle


class CycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cycle
        fields = ["id", "ano", "mes", "status", "created_at", "closed_at"]
        read_only_fields = ["status", "created_at", "closed_at"]
        # Duplicidade de (ano, mes) é responsabilidade do OpenCycleService (mensagem de erro
        # consistente com o resto da API); sem isso o UniqueTogetherValidator automático do DRF
        # dispararia antes do service rodar.
        validators = []


class StuckAllocationSerializer(serializers.Serializer):
    allocation_id = serializers.IntegerField()
    owner_node_id = serializers.IntegerField()
    owner_node_level = serializers.CharField()
    quantity_kg = serializers.IntegerField()
