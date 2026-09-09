"""Regra de negócio de vínculo pessoa↔posição na hierarquia — extraído de `serializers.py`
(2026-08-07) pra seguir a mesma separação já usada em `allocations/services.py` (CLAUDE.md: regra
de negócio pertence a services de domínio, não a serializers/views). `UserAccountSerializer`
continua só validando os campos do payload e delegando pra cá.
"""

from rest_framework import serializers

from apps.hierarchy.models import HierarchyNode
from apps.hierarchy.serializers import HierarchyNodeSerializer

from .models import User


def deactivate_if_orphaned(node: HierarchyNode, changed_by) -> None:
    """Se ninguém mais ocupa `node` (nenhum usuário ativo vinculado), desativa e dispara a mesma
    reatribuição automática de meta em ciclo aberto que qualquer outra desativação de nó já
    dispara (O4/Decisão 10). Ponto único pra essa regra — antes duplicada (e uma cópia
    incompleta) em três lugares: `remove_position`, `_sync_position` com `level=None`, e
    desligar/readmitir usuário. Bug real, 2026-07-22: um nó desvinculado (posição removida, cargo
    limpo, ou usuário desligado) continuava `ativo=True`, contando como ocupado sem ninguém lá.
    """
    if not node.ativo or node.users.filter(is_active=True).exists():
        return

    # Import tardio: allocations importa hierarchy.models, evita ciclo com accounts.
    from apps.allocations.services import HierarchyChangeReassignmentService

    previous = HierarchyNode.objects.get(pk=node.pk)
    node.ativo = False
    node.save(update_fields=["ativo", "updated_at"])
    HierarchyChangeReassignmentService.detect_and_reassign_if_needed(previous, node, changed_by=changed_by)


def resolve_or_create_node(user: User, level: str, parent_node: HierarchyNode | None) -> HierarchyNode:
    """Encontra o `HierarchyNode` certo pra uma posição nova de `user` em `level`/`parent_node`,
    ou cria um — nunca reaproveita um nó já ocupado por outro usuário.

    Compartilhado entre `sync_primary_position` (posição inicial, no create/update) e
    `UserAccountViewSet.add_position` (posições adicionais — múltiplos cargos pelo mesmo
    usuário, Decisão 10/O5 revisada, ver docs/decisions.md).
    """
    reusable = HierarchyNode.objects.filter(
        level=level, parent=parent_node, nome__iexact=user.username, users__isnull=True
    ).first()
    if reusable is not None:
        if not reusable.ativo:
            # Reaproveitar um nó desativado (ex.: reset de hierarquia) precisa reativá-lo —
            # senão o cadastro "funciona" mas a pessoa some da árvore ativa.
            reusable.ativo = True
            reusable.save(update_fields=["ativo", "updated_at"])
        user.hierarchy_nodes.add(reusable)
        return reusable

    node_data = {"level": level, "parent": parent_node.id if parent_node else None, "nome": user.username}
    node_serializer = HierarchyNodeSerializer(data={**node_data, "ativo": True})
    node_serializer.is_valid(raise_exception=True)
    node = node_serializer.save()
    user.hierarchy_nodes.add(node)
    return node


def sync_primary_position(
    user: User, level: str | None, parent_node: HierarchyNode | None, changed_by
) -> None:
    """Cria/atualiza em lugar a posição "principal" deste usuário (a mais sênior, se ele tiver
    mais de uma — Decisão 10/O5 revisada, posições extras são `UserAccountViewSet.
    add_position`/`remove_position`, não passam por aqui). `level=None` desvincula essa posição
    principal (Administrador sem posição, ou quem só tinha uma e perdeu ela), sem apagar o nó,
    pra não perder o histórico de `parent` referenciado por alocações/closure passadas.

    Chamado por `UserAccountSerializer._sync_position`, que só resolve `changed_by` a partir do
    contexto da request e repassa pra cá — nenhuma regra de negócio fica no serializer.
    """
    # `.by_seniority()` é o que garante que "a posição principal" seja sempre a mesma entre esta
    # chamada e o que a API devolve pro frontend (`UserAccountSerializer.get_hierarchy_nodes`) —
    # e que uma promoção (cargo mais sênior registrado depois, como posição extra) automaticamente
    # passa a ser tratada como principal, sem precisar mexer em qual nó tem `id` menor. Bug real,
    # 2026-08-07: ordenar por `id` (ordem de criação) em vez de senioridade fazia editar o "cargo
    # principal" continuar mexendo no cargo antigo/mais baixo mesmo depois de a pessoa já ter um
    # cargo mais alto cadastrado como extra — e como a posição extra já tinha o nível/superior que
    # se queria dar ao principal, isso criava um cargo duplicado em vez de reconhecer o cargo que
    # a pessoa já tinha.
    existing = user.hierarchy_nodes.by_seniority().first()

    if level is None:
        if existing is not None:
            user.hierarchy_nodes.remove(existing)
            deactivate_if_orphaned(existing, changed_by)
        return

    node_data = {"level": level, "parent": parent_node.id if parent_node else None, "nome": user.username}

    if existing is None:
        resolve_or_create_node(user, level, parent_node)
        return

    # Trava irmã da que `add_position` já tinha (`UserAccountViewSet.add_position`). Com
    # `.by_seniority()` acima, promover alguém pra um cargo mais sênior que já existe como
    # posição extra já resolve `existing` pro nó certo (o próprio cargo extra) — mas o inverso
    # (editar o principal pra um cargo/superior que colide com uma posição extra MENOS sênior,
    # ex.: rebaixar) ainda reparentaria o nó principal em cima da extra, duplicando. Sem essa
    # trava, os dois nós viram indistinguíveis na árvore, mas a meta do ciclo aberto já
    # distribuída pra a posição extra fica presa nela, órfã de usuário, enquanto a tela passa a
    # mostrar a posição principal (agora idêntica, porém vazia) como se fosse a mesma coisa. Bug
    # real, 2026-08-07: foi assim que Fabiano "perdeu" o vínculo da meta com o Coordenador
    # Regional dele — ~1,2 milhão de kg já repassados por baixo da posição extra ficaram presos
    # nela quando a principal virou uma cópia vazia.
    if user.hierarchy_nodes.exclude(pk=existing.pk).filter(level=level, parent=parent_node).exists():
        raise serializers.ValidationError(
            "Este usuário já ocupa uma posição com este mesmo cargo e superior — gerencie "
            'posições existentes em "Outros cargos" em vez de editar o cargo principal.'
        )

    # partial=True mas inclui `nome`: cargo/superior podem mudar (reparenta o mesmo nó em vez de
    # criar outro) e o nome do nó sempre acompanha o `username` — trocar a pessoa numa posição
    # existente é só editar nome/login dela, sem mexer em cargo/superior.
    previous = HierarchyNode.objects.get(pk=existing.pk)
    node_serializer = HierarchyNodeSerializer(instance=existing, data=node_data, partial=True)
    node_serializer.is_valid(raise_exception=True)
    node = node_serializer.save()

    # Import tardio: allocations importa hierarchy.models, evita ciclo com accounts.
    from apps.allocations.services import HierarchyChangeReassignmentService

    HierarchyChangeReassignmentService.detect_and_reassign_if_needed(previous, node, changed_by=changed_by)
