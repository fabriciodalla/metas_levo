from rest_framework import serializers


class ClientInactivePurchaseItemSerializer(serializers.Serializer):
    subgroup_name = serializers.CharField()
    peso_kg = serializers.FloatField()


class ClientInactiveRowSerializer(serializers.Serializer):
    client_code = serializers.IntegerField()
    client_name = serializers.CharField()
    ultima_compra_ano = serializers.IntegerField(allow_null=True)
    ultima_compra_mes = serializers.IntegerField(allow_null=True)
    itens_ultima_compra = ClientInactivePurchaseItemSerializer(many=True)
    peso_ultima_compra_kg = serializers.FloatField()


class ClientGroupTicketMedioSerializer(serializers.Serializer):
    grupo_id = serializers.IntegerField()
    grupo_nome = serializers.CharField()
    clientes_ativos = serializers.IntegerField()
    ticket_medio_kg = serializers.FloatField(allow_null=True)


class ClientAccumuladoResultSerializer(serializers.Serializer):
    node_id = serializers.IntegerField()
    node_nome = serializers.CharField()
    ano = serializers.IntegerField()
    mes = serializers.IntegerField()
    carteira_total = serializers.IntegerField()
    clientes_ativos = serializers.IntegerField()
    clientes_ativos_mes_anterior = serializers.IntegerField()
    positivacao_pct = serializers.FloatField(allow_null=True)
    positivacao_meta_pct = serializers.FloatField()
    captacao = serializers.IntegerField()
    captacao_meta = serializers.IntegerField()
    base_clientes_meta = serializers.IntegerField()
    clientes_ativos_meta = serializers.IntegerField()
    clientes_sem_compra_count = serializers.IntegerField()
    ticket_medio_kg = serializers.FloatField(allow_null=True)
    ticket_medio_por_grupo = ClientGroupTicketMedioSerializer(many=True)
    clientes_sem_compra = ClientInactiveRowSerializer(many=True)
