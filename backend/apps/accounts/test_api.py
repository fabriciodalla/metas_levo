from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from apps.hierarchy.models import HierarchyNode

User = get_user_model()


class AuthApiTests(APITestCase):
    def setUp(self):
        self.node = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.user = User.objects.create_user(
            username="gerente",
            email="gerente@levo.local",
            password="senha-forte",
            hierarchy_node=self.node,
        )

    def test_login_with_valid_credentials_returns_user_data(self):
        response = self.client.post(
            reverse("auth-login"),
            {"email": "gerente@levo.local", "password": "senha-forte"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "gerente")
        self.assertEqual(response.data["hierarchy_nodes"][0]["id"], self.node.id)

    def test_login_is_case_insensitive_on_email(self):
        response = self.client.post(
            reverse("auth-login"),
            {"email": "GERENTE@LEVO.LOCAL", "password": "senha-forte"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_with_invalid_credentials_is_rejected(self):
        response = self.client.post(
            reverse("auth-login"),
            {"email": "gerente@levo.local", "password": "errada"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_with_unknown_email_is_rejected(self):
        response = self.client.post(
            reverse("auth-login"),
            {"email": "ninguem@levo.local", "password": "qualquer"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        response = self.client.get(reverse("auth-me"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_me_returns_current_user_when_authenticated(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("auth-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "gerente")

    def test_logout_clears_session(self):
        self.client.force_login(self.user)
        self.client.post(reverse("auth-logout"))

        response = self.client.get(reverse("auth-me"))

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class UserAccountApiTests(APITestCase):
    """CRUD de usuário pela tela de gestão do Administrador (`is_admin`, não confundir com
    `is_staff`/Django Admin)."""

    def setUp(self):
        self.node = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")
        self.admin = User.objects.create_user(username="admin", password="x", is_admin=True)
        self.plain_user = User.objects.create_user(username="plain", password="x")

    def test_non_admin_cannot_list_users(self):
        self.client.force_login(self.plain_user)

        response = self.client.get(reverse("user-account-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_user_with_password_and_cargo(self):
        self.client.force_login(self.admin)
        local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="placeholder", parent=self.node
        )

        response = self.client.post(
            reverse("user-account-list"),
            {
                "username": "Novo Coordenador",
                "email": "novo@levo.local",
                "password": "senha-forte-123",
                "level": HierarchyNode.Level.LOCAL,
                "parent_node_id": self.node.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username="Novo Coordenador")
        self.assertTrue(created.check_password("senha-forte-123"))
        node = created.hierarchy_nodes.get()
        self.assertEqual(node.level, HierarchyNode.Level.LOCAL)
        self.assertEqual(node.parent_id, self.node.id)
        self.assertEqual(node.nome, "Novo Coordenador")
        # nome não bate com o nó pré-existente ("placeholder") — não é reaproveitado, ganha o seu.
        self.assertNotEqual(node.id, local.id)

    def test_admin_editing_cargo_updates_existing_node_in_place(self):
        self.client.force_login(self.admin)
        local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.node
        )
        local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.node
        )
        target = User.objects.create_user(username="Alvo Original", password="x", hierarchy_node=local_a)
        original_node_id = target.hierarchy_nodes.get().id

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {"level": HierarchyNode.Level.SUPERVISOR, "parent_node_id": local_b.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node = target.hierarchy_nodes.get()
        self.assertEqual(node.id, original_node_id)
        self.assertEqual(node.level, HierarchyNode.Level.SUPERVISOR)
        self.assertEqual(node.parent_id, local_b.id)

    def test_editing_primary_position_reparents_oldest_node_regardless_of_m2m_add_order(self):
        """Bug real (2026-07-22): editar cargo/superior criou um nó novo em vez de reaproveitar o
        existente. Causa: `ManyToManyField` sem ordering explícito não garante ordem em `.first()`
        — dependendo da ordem física de inserção no M2M, a posição "principal" podia vir
        diferente do esperado. `.by_seniority()` decide isso hoje (2026-08-07: por senioridade de
        cargo, não por `id`, ver `test_promoting_someone_...`), mas aqui os dois nós têm níveis
        diferentes E ordem de criação alinhada com a senioridade (LOCAL é mais antigo e mais
        sênior que SUPERVISOR) — então este teste continua cobrindo especificamente o bug original do
        M2M sem ordering, com `id` como critério de desempate."""
        self.client.force_login(self.admin)
        older_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Alvo", parent=self.node
        )
        newer_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Alvo", parent=older_node
        )
        target = User.objects.create_user(username="Alvo", password="x")
        # Adiciona fora de ordem de criação, de propósito — sem ordering explícito no backend,
        # `.first()` podia devolver `newer_node` aqui (ordem física de inserção no M2M).
        target.hierarchy_nodes.add(newer_node, older_node)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {"level": HierarchyNode.Level.LOCAL, "parent_node_id": self.node.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(target.hierarchy_nodes.count(), 2)  # não criou um terceiro nó
        older_node.refresh_from_db()
        self.assertEqual(older_node.level, HierarchyNode.Level.LOCAL)
        self.assertEqual(older_node.parent_id, self.node.id)

    def test_promoting_someone_to_a_more_senior_position_they_already_hold_as_extra_reuses_it(self):
        """Cenário real do Fabiano (2026-08-07): tinha Supervisor como principal (criado
        primeiro), ganhou Coordenador Local como posição extra (adicionada depois, via "Outros
        cargos" — a promoção), e depois disso editar o "cargo principal" pra Coordenador Local/
        mesmo superior da posição extra precisa reconhecer que a pessoa JÁ tem esse cargo, em vez
        de reparentar o nó do Supervisor (mais antigo, porém menos sênior) por cima dele —
        `.by_seniority()` resolve a posição mais sênior como "principal", não a mais antiga, então
        esse PATCH vira um no-op sobre o nó que já é Coordenador Local, e o Supervisor sobra livre
        pra ser removido via "Outros cargos" (o que a promoção de verdade pedia)."""
        self.client.force_login(self.admin)
        local_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Alvo", parent=self.node
        )
        supervisor_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Alvo", parent=local_node
        )
        target = User.objects.create_user(username="Alvo", password="x", hierarchy_node=supervisor_node)
        target.hierarchy_nodes.add(local_node)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {"level": HierarchyNode.Level.LOCAL, "parent_node_id": self.node.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(target.hierarchy_nodes.count(), 2)  # não criou (nem apagou) nó nenhum
        supervisor_node.refresh_from_db()
        local_node.refresh_from_db()
        self.assertEqual(supervisor_node.level, HierarchyNode.Level.SUPERVISOR)  # intocado
        self.assertEqual(supervisor_node.parent_id, local_node.id)
        self.assertEqual(local_node.level, HierarchyNode.Level.LOCAL)  # já era isso, no-op
        # a posição mais sênior (Coordenador Local) agora é a "principal" pro backend
        self.assertEqual(target.hierarchy_nodes.by_seniority().first().id, local_node.id)

    def test_editing_primary_position_rejects_demotion_that_would_collide_with_a_junior_extra_position(self):
        """Trava simétrica à de `test_add_position_rejects_exact_duplicate`: `.by_seniority()`
        só resolve sozinha o caso de promoção (o teste acima) — rebaixar o cargo principal pra
        um nível/superior que colide com uma posição extra JÚNIOR ainda reparentaria o nó
        principal (mais sênior) por cima da extra, então essa trava continua necessária."""
        self.client.force_login(self.admin)
        local_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Alvo", parent=self.node
        )
        target = User.objects.create_user(username="Alvo", password="x", hierarchy_node=local_node)
        supervisor_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Alvo", parent=local_node
        )
        target.hierarchy_nodes.add(supervisor_node)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {"level": HierarchyNode.Level.SUPERVISOR, "parent_node_id": local_node.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # nem a posição principal nem a extra foram tocadas
        local_node.refresh_from_db()
        supervisor_node.refresh_from_db()
        self.assertEqual(local_node.level, HierarchyNode.Level.LOCAL)
        self.assertEqual(local_node.parent_id, self.node.id)
        self.assertEqual(supervisor_node.level, HierarchyNode.Level.SUPERVISOR)
        self.assertTrue(supervisor_node.ativo)

    def test_clearing_cargo_deactivates_the_orphaned_position(self):
        """Terceiro caminho pro mesmo bug (2026-07-22): limpar o campo Cargo (`level=None`) pra
        deixar o usuário "sem posição" desvinculava o nó mas nunca desativava — mesma falha de
        `remove_position` e de desligar usuário, só que via esse terceiro código-caminho."""
        self.client.force_login(self.admin)
        node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Sem Cargo Agora", parent=self.node
        )
        target = User.objects.create_user(username="Sem Cargo Agora", password="x", hierarchy_node=node)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"level": None}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(target.hierarchy_nodes.count(), 0)
        node.refresh_from_db()
        self.assertFalse(node.ativo)

    def test_admin_creating_user_reuses_existing_unoccupied_node_with_matching_name(self):
        """Hierarquia importada de planilha (nome real, sem usuário) precisa ser reaproveitada
        em vez de gerar um nó duplicado — casamento automático por cargo+superior+nome, sem
        seletor manual de nó (Decisão 11 revisada)."""
        self.client.force_login(self.admin)
        imported = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Fabio Shaen", parent=self.node
        )

        response = self.client.post(
            reverse("user-account-list"),
            {
                "username": "Fabio Shaen",
                "email": "fabio@levo.local",
                "password": "senha-forte-123",
                "level": HierarchyNode.Level.LOCAL,
                "parent_node_id": self.node.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username="Fabio Shaen")
        node = created.hierarchy_nodes.get()
        self.assertEqual(node.id, imported.id)

    def test_admin_renaming_user_updates_linked_node_nome(self):
        """A transição de trocar quem ocupa uma posição (ex.: substituir o titular de um cargo)
        é só editar nome completo/login da pessoa — sem mexer em cargo/superior."""
        self.client.force_login(self.admin)
        local = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Nome Antigo", parent=self.node
        )
        target = User.objects.create_user(username="Nome Antigo", password="x", hierarchy_node=local)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {
                "username": "Nome Novo",
                "level": HierarchyNode.Level.LOCAL,
                "parent_node_id": self.node.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node = target.hierarchy_nodes.get()
        self.assertEqual(node.id, local.id)
        self.assertEqual(node.nome, "Nome Novo")

    def test_patching_only_parent_node_id_reparents_without_touching_level(self):
        """Bug real (2026-07-24): PATCH só com `parent_node_id` (sem `level`) respondia 200 mas
        não movia o nó, porque `_sync_position` só era chamado quando `level` vinha no payload.
        A tela de edição sempre reenvia os dois campos juntos, então o caminho nunca era
        acionado pela UI, mas a API aceitava silenciosamente sem efeito."""
        self.client.force_login(self.admin)
        other_gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Outro Gerente")
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.node)
        target = User.objects.create_user(username="Alvo", password="x", hierarchy_node=local)
        original_node_id = target.hierarchy_nodes.get().id

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {"parent_node_id": other_gerente.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node = target.hierarchy_nodes.get()
        self.assertEqual(node.id, original_node_id)
        self.assertEqual(node.level, HierarchyNode.Level.LOCAL)
        self.assertEqual(node.parent_id, other_gerente.id)

    def test_patching_only_level_with_same_value_keeps_existing_parent(self):
        """Simétrico ao anterior: reenviar só `level` (mesmo valor, sem `parent_node_id`) não pode
        assumir `parent=None` e derrubar a posição — precisa manter o superior atual. Trocar de
        função de verdade (nível diferente) sempre exige mandar o novo superior junto, já que o
        nível de pai esperado muda; esse teste cobre só o caso de resubmissão sem mudança real."""
        self.client.force_login(self.admin)
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.node)
        target = User.objects.create_user(username="Alvo", password="x", hierarchy_node=local)
        original_node_id = target.hierarchy_nodes.get().id

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]),
            {"level": HierarchyNode.Level.LOCAL},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node = target.hierarchy_nodes.get()
        self.assertEqual(node.id, original_node_id)
        self.assertEqual(node.level, HierarchyNode.Level.LOCAL)
        self.assertEqual(node.parent_id, self.node.id)

    def test_create_without_password_is_rejected(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("user-account-list"), {"username": "sem-senha", "email": "sem-senha@levo.local"}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactivating_user_deactivates_their_orphaned_position(self):
        """Bug real (2026-07-22): desligar alguém (is_active=False) não desativava o nó da
        posição dele — ele continuava contando como vendedor/cargo ativo na árvore."""
        self.client.force_login(self.admin)
        node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Desligado", parent=self.node
        )
        target = User.objects.create_user(username="Desligado", password="x", hierarchy_node=node)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node.refresh_from_db()
        self.assertFalse(node.ativo)

    def test_inactivating_user_does_not_deactivate_position_still_held_by_another_active_user(self):
        self.client.force_login(self.admin)
        shared_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Compartilhado", parent=self.node
        )
        target = User.objects.create_user(username="Alvo", password="x")
        other_user = User.objects.create_user(username="Outro", password="x")
        target.hierarchy_nodes.add(shared_node)
        other_user.hierarchy_nodes.add(shared_node)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        shared_node.refresh_from_db()
        self.assertTrue(shared_node.ativo)

    def test_inactivating_user_reopens_open_cycle_allocation_of_orphaned_node(self):
        from apps.allocations.models import GoalAllocation
        from apps.allocations.services import ChildAllocationSpec, DistributeGoalService
        from apps.catalog.models import ProductGroup
        from apps.cycles.models import Cycle

        self.client.force_login(self.admin)
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.node)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=local
        )
        group = ProductGroup.objects.create(nome="Embutidos")
        cycle = Cycle.objects.create(ano=2026, mes=7)
        gerente_user = User.objects.create_user(username="gerente3", password="x", hierarchy_node=self.node)
        local_user = User.objects.create_user(username="local3", password="x", hierarchy_node=local)
        target = User.objects.create_user(username="Alvo Supervisor", password="x", hierarchy_node=supervisor)

        gerente_alloc = GoalAllocation.objects.create(
            cycle=cycle,
            owner_node=self.node,
            granularity=GoalAllocation.Granularity.GROUP,
            group=group,
            quantity_kg=100,
            criado_por=gerente_user,
        )
        (local_alloc,) = DistributeGoalService.distribute(
            gerente_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=local.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=group.id,
                )
            ],
            criado_por=gerente_user,
        )
        DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=group.id,
                )
            ],
            criado_por=local_user,
        )

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        supervisor.refresh_from_db()
        self.assertFalse(supervisor.ativo)
        local_alloc.refresh_from_db()
        self.assertFalse(local_alloc.distributed)

    def test_reactivating_user_reactivates_their_deactivated_position(self):
        """Bug real (2026-07-22): readmitir alguém (is_active=False->True) não trazia o nó da
        posição dele de volta pra árvore ativa — ele reaparecia como usuário ativo, mas sumido."""
        self.client.force_login(self.admin)
        node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Readmitido", parent=self.node, ativo=False
        )
        target = User.objects.create_user(
            username="Readmitido", password="x", is_active=False, hierarchy_node=node
        )

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"is_active": True}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node.refresh_from_db()
        self.assertTrue(node.ativo)

    def test_reactivating_user_does_not_touch_positions_that_were_already_active(self):
        self.client.force_login(self.admin)
        node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Sempre Ativo", parent=self.node
        )
        target = User.objects.create_user(
            username="Sempre Ativo", password="x", is_active=False, hierarchy_node=node
        )

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"is_active": True}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node.refresh_from_db()
        self.assertTrue(node.ativo)

    def test_admin_can_inactivate_user_without_touching_password(self):
        target = User.objects.create_user(username="alvo", password="senha-original")
        self.client.force_login(self.admin)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(target.check_password("senha-original"))

    def test_admin_can_reset_password(self):
        target = User.objects.create_user(username="alvo", password="senha-antiga")
        self.client.force_login(self.admin)

        response = self.client.patch(
            reverse("user-account-detail", args=[target.id]), {"password": "senha-nova-123"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertTrue(target.check_password("senha-nova-123"))

    def test_admin_can_add_second_position_to_same_user(self):
        """Decisão 10/O5 revisada: a mesma pessoa pode acumular mais de um cargo (ex.: um
        Coordenador Local que também é Supervisor de um dos ramos abaixo dele)."""
        self.client.force_login(self.admin)
        local_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Marcelo Rodrigues Cireli", parent=self.node
        )
        target = User.objects.create_user(
            username="Marcelo Rodrigues Cireli", password="x", hierarchy_node=local_node
        )

        response = self.client.post(
            reverse("user-account-add-position", args=[target.id]),
            {"level": HierarchyNode.Level.SUPERVISOR, "parent_node_id": local_node.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(target.hierarchy_nodes.count(), 2)
        supervisor_node = target.hierarchy_nodes.exclude(pk=local_node.pk).get()
        self.assertEqual(supervisor_node.level, HierarchyNode.Level.SUPERVISOR)
        self.assertEqual(supervisor_node.parent_id, local_node.id)
        self.assertEqual(supervisor_node.nome, "Marcelo Rodrigues Cireli")
        # a posição original não foi tocada
        local_node.refresh_from_db()
        self.assertEqual(local_node.level, HierarchyNode.Level.LOCAL)
        self.assertEqual(local_node.parent_id, self.node.id)

    def test_add_position_rejects_exact_duplicate(self):
        self.client.force_login(self.admin)
        local_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Alvo", parent=self.node
        )
        target = User.objects.create_user(username="Alvo", password="x", hierarchy_node=local_node)

        response = self.client.post(
            reverse("user-account-add-position", args=[target.id]),
            {"level": HierarchyNode.Level.LOCAL, "parent_node_id": self.node.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(target.hierarchy_nodes.count(), 1)

    def test_admin_can_remove_one_of_multiple_positions_without_deleting_the_node(self):
        self.client.force_login(self.admin)
        local_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Alvo", parent=self.node
        )
        supervisor_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Alvo", parent=local_node
        )
        target = User.objects.create_user(username="Alvo", password="x")
        target.hierarchy_nodes.add(local_node, supervisor_node)

        response = self.client.delete(
            reverse("user-account-remove-position", args=[target.id, supervisor_node.id])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(list(target.hierarchy_nodes.all()), [local_node])
        # o nó em si continua existindo, só desvinculado
        supervisor_node.refresh_from_db()
        self.assertTrue(HierarchyNode.objects.filter(pk=supervisor_node.pk).exists())
        # ninguém mais ocupa o nó removido -> some da árvore ativa (bug real, 2026-07-22)
        self.assertFalse(supervisor_node.ativo)
        # a posição que sobrou pro usuário não foi tocada
        local_node.refresh_from_db()
        self.assertTrue(local_node.ativo)

    def test_removing_position_still_shared_by_another_user_does_not_deactivate_it(self):
        """Um nó só desativa quando fica realmente órfão — se outro usuário ainda ocupa ele,
        remover a posição de um não pode apagar a posição do outro."""
        self.client.force_login(self.admin)
        shared_node = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Compartilhado", parent=self.node
        )
        target = User.objects.create_user(username="Alvo", password="x")
        other_user = User.objects.create_user(username="Outro", password="x")
        target.hierarchy_nodes.add(shared_node)
        other_user.hierarchy_nodes.add(shared_node)

        response = self.client.delete(
            reverse("user-account-remove-position", args=[target.id, shared_node.id])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        shared_node.refresh_from_db()
        self.assertTrue(shared_node.ativo)
        self.assertIn(other_user, shared_node.users.all())

    def test_removing_position_reopens_open_cycle_allocation_of_orphaned_node(self):
        """Desativar o nó ao remover a última posição precisa disparar a mesma reatribuição
        automática (O4/Decisão 10) que qualquer outra desativação de nó já dispara."""
        from apps.allocations.models import GoalAllocation
        from apps.allocations.services import ChildAllocationSpec, DistributeGoalService
        from apps.catalog.models import ProductGroup
        from apps.cycles.models import Cycle

        self.client.force_login(self.admin)
        local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local", parent=self.node)
        supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=local
        )
        group = ProductGroup.objects.create(nome="Embutidos")
        cycle = Cycle.objects.create(ano=2026, mes=7)
        gerente_user = User.objects.create_user(username="gerente2", password="x", hierarchy_node=self.node)
        local_user = User.objects.create_user(username="local2", password="x", hierarchy_node=local)
        target = User.objects.create_user(username="Alvo Supervisor", password="x")
        target.hierarchy_nodes.add(supervisor)

        gerente_alloc = GoalAllocation.objects.create(
            cycle=cycle,
            owner_node=self.node,
            granularity=GoalAllocation.Granularity.GROUP,
            group=group,
            quantity_kg=100,
            criado_por=gerente_user,
        )
        (local_alloc,) = DistributeGoalService.distribute(
            gerente_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=local.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=group.id,
                )
            ],
            criado_por=gerente_user,
        )
        DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=100,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=group.id,
                )
            ],
            criado_por=local_user,
        )

        response = self.client.delete(
            reverse("user-account-remove-position", args=[target.id, supervisor.id])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        supervisor.refresh_from_db()
        self.assertFalse(supervisor.ativo)
        local_alloc.refresh_from_db()
        self.assertFalse(local_alloc.distributed)


class PasswordResetApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gerente", email="gerente@levo.local", password="senha-antiga"
        )

    def test_request_with_known_email_sends_email_and_returns_generic_detail(self):
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "gerente@levo.local"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.username, mail.outbox[0].body)
        self.assertIn("/redefinir-senha/", mail.outbox[0].body)

    def test_request_with_unknown_email_returns_same_generic_detail_and_sends_nothing(self):
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "ninguem@levo.local"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_with_valid_token_sets_new_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = PasswordResetTokenGenerator().make_token(self.user)

        response = self.client.post(
            reverse("auth-password-reset-confirm"),
            {"uid": uid, "token": token, "new_password": "senha-nova-123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("senha-nova-123"))

    def test_confirm_with_invalid_token_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))

        response = self.client.post(
            reverse("auth-password-reset-confirm"),
            {"uid": uid, "token": "token-invalido", "new_password": "senha-nova-123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("senha-antiga"))


class PasswordChangeApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gerente", email="gerente@levo.local", password="senha-antiga"
        )

    def test_requires_authentication(self):
        response = self.client.post(
            reverse("auth-password-change"),
            {"current_password": "senha-antiga", "new_password": "senha-nova-123"},
            format="json",
        )

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_changes_password_when_current_password_is_correct(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("auth-password-change"),
            {"current_password": "senha-antiga", "new_password": "senha-nova-123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("senha-nova-123"))

    def test_keeps_session_valid_after_changing_own_password(self):
        self.client.force_login(self.user)

        self.client.post(
            reverse("auth-password-change"),
            {"current_password": "senha-antiga", "new_password": "senha-nova-123"},
            format="json",
        )
        response = self.client.get(reverse("auth-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_rejects_wrong_current_password_without_changing_it(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("auth-password-change"),
            {"current_password": "senha-errada", "new_password": "senha-nova-123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("senha-antiga"))
