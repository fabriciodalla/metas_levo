from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.hierarchy.models import HierarchyNode
from apps.hierarchy.serializers import HierarchyNodeSerializer

from .models import User
from .services import deactivate_if_orphaned, sync_primary_position


def _validate_strong_password(value, user=None):
    """Aciona os `AUTH_PASSWORD_VALIDATORS` (settings.py) — sem isso, os 3 pontos de entrada de
    senha da API (cadastro, troca, redefinição) chamavam `set_password`/`create_user` direto e
    aceitavam qualquer coisa, inclusive senha "1" (achado A-02)."""
    try:
        validate_password(value, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages))
    return value


class HierarchyNodeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = HierarchyNode
        fields = ["id", "level", "nome"]


class UserSerializer(serializers.ModelSerializer):
    # ManyToManyField sem ordering explícito não garante nenhuma ordem estável em `.all()` — sem
    # isso, `hierarchy_nodes[0]` (a posição "principal", ver `_sync_position`) podia vir diferente
    # a cada request, fora de sincronia com o que o backend trata como principal.
    hierarchy_nodes = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "is_admin", "hierarchy_nodes"]

    def get_hierarchy_nodes(self, obj):
        return HierarchyNodeSummarySerializer(obj.hierarchy_nodes.by_seniority(), many=True).data


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        # O usuário só é decodificado depois da validação do serializer (a partir do uid), então
        # a checagem de similaridade com username/e-mail não roda aqui — os outros 3 validadores
        # (tamanho, senha comum, só números) seguem valendo.
        return _validate_strong_password(value)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        request = self.context.get("request")
        user = request.user if request else None
        return _validate_strong_password(value, user)


class AddUserPositionSerializer(serializers.Serializer):
    """Posição adicional pra um usuário que já existe — mesma resolução de nó de
    `resolve_or_create_node` (`apps.accounts.services`), sem mexer nas posições que a pessoa já
    tem (Decisão 10/O5
    revisada, 2026-07-21): ela pode acumular cargos em ramos diferentes da árvore (ex.: um
    Coordenador Local que também é Supervisor de outro ramo).

    A validação de "superior precisa ser do nível imediatamente acima" acontece só quando
    `resolve_or_create_node` precisa criar um nó novo (via `HierarchyNodeSerializer`) — quando
    reaproveita um nó já existente, a combinação level/parent dele já foi validada na criação
    original, não precisa repetir aqui.
    """

    level = serializers.ChoiceField(choices=HierarchyNode.Level.choices)
    parent_node_id = serializers.PrimaryKeyRelatedField(
        source="parent_node", queryset=HierarchyNode.objects.all(), required=False, allow_null=True
    )


class UserAccountSerializer(serializers.ModelSerializer):
    """CRUD de usuário para a tela de gestão do Administrador — separado de `UserSerializer`
    (usado só em `/auth/me`, sem senha nem escrita).

    `username` é o nome completo da pessoa (não um login técnico — login é por e-mail). A
    posição inicial na hierarquia é informada só como `level` (cargo) + `parent_node_id`
    (superior direto); o `HierarchyNode` correspondente é resolvido automaticamente por
    `_sync_position` — sem seletor manual de nó (Decisão 11 revisada, 2026-07-21): reaproveita
    um nó já cadastrado sem usuário com nome/cargo/superior batendo (ex.: hierarquia importada
    de planilha), ou cria um novo. Editar cargo/superior de quem já tem posição reparenta o
    mesmo nó em vez de criar outro, e mantém `nome` sincronizado com `username`.

    Múltiplos cargos pelo mesmo usuário (Decisão 10/O5 revisada) não passam por aqui — são
    `UserAccountViewSet.add_position`/`remove_position`, que só adicionam ou removem uma posição
    sem tocar nas demais; este serializer sempre trata a posição "principal" (a primeira).
    """

    # Mesmo motivo do SerializerMethodField em UserSerializer: M2M sem ordering explícito não tem
    # ordem estável — `hierarchy_nodes[0]` no frontend (`UserEditModal.formFromUser`) precisa
    # sempre bater com a mesma posição "principal" que `_sync_position` usa no backend.
    hierarchy_nodes = serializers.SerializerMethodField()
    level = serializers.ChoiceField(
        choices=HierarchyNode.Level.choices, required=False, allow_null=True, write_only=True
    )
    parent_node_id = serializers.PrimaryKeyRelatedField(
        source="parent_node",
        queryset=HierarchyNode.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "is_admin",
            "is_active",
            "hierarchy_nodes",
            "level",
            "parent_node_id",
            "password",
        ]

    def get_hierarchy_nodes(self, obj):
        return HierarchyNodeSerializer(obj.hierarchy_nodes.by_seniority(), many=True).data

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "Senha é obrigatória para criar um usuário."})
        return attrs

    def validate_password(self, value):
        # Em atualização, compara com os dados do próprio usuário sendo editado (similaridade
        # com username/e-mail); em criação, `self.instance` ainda não existe.
        return _validate_strong_password(value, self.instance)

    def create(self, validated_data):
        password = validated_data.pop("password")
        has_level = "level" in validated_data
        level = validated_data.pop("level", None)
        parent_node = validated_data.pop("parent_node", None)
        user = User.objects.create_user(password=password, **validated_data)
        if has_level:
            self._sync_position(user, level, parent_node)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        has_level = "level" in validated_data
        has_parent = "parent_node" in validated_data
        level = validated_data.pop("level", None)
        parent_node = validated_data.pop("parent_node", None)
        was_active = instance.is_active
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        if has_level or has_parent:
            # PATCH pode mandar só um dos dois campos (ex.: trocar só o superior, mantendo o
            # cargo) — o que não veio no payload preenche a partir da posição principal atual,
            # em vez de assumir `None` (que faria `_sync_position` tratar como "sem cargo"/"sem
            # superior" e ou desvincular a posição, ou falhar a validação de nível/pai). Bug real,
            # 2026-07-24: um PATCH só com `parent_node_id` (sem `level`) respondia 200 sem mover
            # o nó — a UI nunca aciona esse caminho (sempre reenvia os dois juntos), mas a API
            # aceitava silenciosamente sem efeito.
            existing = instance.hierarchy_nodes.by_seniority().first()
            if not has_level:
                level = existing.level if existing else None
            if not has_parent:
                parent_node = existing.parent if existing else None
            self._sync_position(instance, level, parent_node)

        # Desligar alguém (is_active True->False) não pode deixar a(s) posição(ões) dele soltas,
        # ativas na árvore, como se ainda tivesse gente ocupando (bug real, 2026-07-22: usuário
        # inativado, nó da posição continuava ativo). Mesma lógica de nó órfão de
        # `remove_position`, mas sem desvincular — preserva o histórico de quem ocupou o quê.
        # Readmitir (is_active False->True) é o inverso: traz de volta a(s) posição(ões) que
        # tinham sido desativadas nesse desligamento — senão a pessoa reaparece como usuário
        # ativo, mas continua sumida da árvore (mesmo bug, sentido contrário, 2026-07-22).
        if was_active and not instance.is_active:
            self._deactivate_orphaned_positions(instance)
        elif not was_active and instance.is_active:
            self._reactivate_positions(instance)

        return instance

    def _deactivate_orphaned_positions(self, user):
        changed_by = getattr(self.context.get("request"), "user", None)
        for node in user.hierarchy_nodes.filter(ativo=True):
            deactivate_if_orphaned(node, changed_by)

    def _reactivate_positions(self, user):
        for node in user.hierarchy_nodes.filter(ativo=False):
            node.ativo = True
            node.save(update_fields=["ativo", "updated_at"])

    def _sync_position(self, user, level, parent_node):
        """Delega pra `sync_primary_position` (`apps.accounts.services`) — a regra de qual
        posição é "principal", criação/reaproveitamento de nó e disparo de reassignment de
        hierarquia moram lá; aqui só resolve `changed_by` a partir do contexto da request."""
        changed_by = getattr(self.context.get("request"), "user", None)
        sync_primary_position(user, level, parent_node, changed_by)
