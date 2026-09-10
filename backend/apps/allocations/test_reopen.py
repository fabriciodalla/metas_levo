from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.audit.models import AuditLogEntry
from apps.catalog.models import ProductGroup, ProductSubgroup
from apps.cycles.models import Cycle
from apps.cycles.services import CloseCycleService
from apps.hierarchy.models import HierarchyNode

from .models import GoalAllocation
from .services import (
    AllocationReopenError,
    AllocationScopeError,
    ChildAllocationSpec,
    CycleCompletenessChecker,
    DistributeGoalService,
    ReopenAllocationService,
)

User = get_user_model()


class ReopenAllocationServiceTests(TestCase):
    """H4: uma alocação já distribuída pode ser reaberta pelo nível que a distribuiu, enquanto
    o ciclo está aberto, invalidando em cascata a sub-árvore de filhas."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=7)

        self.gerente = HierarchyNode.objects.create(level=HierarchyNode.Level.GERENTE, nome="Gerente")

        self.local_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local A", parent=self.gerente
        )
        self.supervisor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor A", parent=self.local_a
        )
        self.vendedor_a = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor A", parent=self.supervisor_a
        )

        self.local_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.LOCAL, nome="Local B", parent=self.gerente
        )
        self.supervisor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor B", parent=self.local_b
        )
        self.vendedor_b = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor B", parent=self.supervisor_b
        )

        self.gerente_user = User.objects.create_user(
            username="gerente", password="x", hierarchy_node=self.gerente
        )
        self.local_a_user = User.objects.create_user(
            username="local_a", password="x", hierarchy_node=self.local_a
        )
        self.supervisor_a_user = User.objects.create_user(
            username="supervisor_a", password="x", hierarchy_node=self.supervisor_a
        )
        self.local_b_user = User.objects.create_user(
            username="local_b", password="x", hierarchy_node=self.local_b
        )
        self.supervisor_b_user = User.objects.create_user(
            username="supervisor_b", password="x", hierarchy_node=self.supervisor_b
        )

        self.gerente_allocation = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.gerente,
            granularity=GoalAllocation.Granularity.GROUP,
            group=self.group,
            quantity_kg=1000,
            criado_por=self.gerente_user,
        )

    def _distribute_gerente_to_both_locais(self):
        return DistributeGoalService.distribute(
            self.gerente_allocation,
            [
                ChildAllocationSpec(
                    owner_node_id=self.local_a.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.local_b.id,
                    quantity_kg=400,
                    granularity=GoalAllocation.Granularity.GROUP,
                    group_id=self.group.id,
                ),
            ],
            criado_por=self.gerente_user,
        )

    def _distribute_branch_down(self, local_alloc, supervisor, vendedor, users, qty):
        (supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor.id,
                    quantity_kg=qty,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=users[0],
        )
        (vendedor_alloc,) = DistributeGoalService.distribute(
            supervisor_alloc,
            [
                ChildAllocationSpec(
                    owner_node_id=vendedor.id,
                    quantity_kg=qty,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=users[1],
        )
        return supervisor_alloc, vendedor_alloc

    def test_reopen_cascades_delete_and_resets_distributed_flag(self):
        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        (supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc_a,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor_a.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_a_user,
        )

        ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_a_user)

        local_alloc_a.refresh_from_db()
        self.assertFalse(local_alloc_a.distributed)
        self.assertFalse(GoalAllocation.objects.filter(id=supervisor_alloc.id).exists())

    def test_reopen_for_hierarchy_change_cascades_delete_regardless_of_depth(self):
        # Caminho automático da O4 (mudança de hierarquia) não tem a trava de "filho já avançou" —
        # precisa resolver o galho inteiro de uma vez, não dá pra esperar um reset manual em cada
        # nível abaixo (Decisão 10).
        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        supervisor_alloc, vendedor_alloc = self._distribute_branch_down(
            local_alloc_a,
            self.supervisor_a,
            self.vendedor_a,
            [self.local_a_user, self.supervisor_a_user],
            qty=600,
        )

        ReopenAllocationService.reopen_for_hierarchy_change(
            self.gerente_allocation, changed_by=self.gerente_user, affected_node=self.local_a
        )

        self.gerente_allocation.refresh_from_db()
        self.assertFalse(self.gerente_allocation.distributed)
        self.assertFalse(GoalAllocation.objects.filter(id=local_alloc_a.id).exists())
        self.assertFalse(GoalAllocation.objects.filter(id=supervisor_alloc.id).exists())
        self.assertFalse(GoalAllocation.objects.filter(id=vendedor_alloc.id).exists())

    def test_reopen_creates_audit_log_entry(self):
        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        (supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc_a,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor_a.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_a_user,
        )

        ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_a_user)

        entry = AuditLogEntry.objects.get(content_type__model="goalallocation", object_id=local_alloc_a.id)
        self.assertEqual(entry.action, AuditLogEntry.Action.REABERTURA)
        self.assertEqual(entry.changed_by, self.local_a_user)
        invalidated_ids = {item["id"] for item in entry.changes["filhas_invalidadas"]}
        self.assertEqual(invalidated_ids, {supervisor_alloc.id})

    def test_reopen_is_scoped_to_the_branch_only(self):
        local_alloc_a, local_alloc_b = self._distribute_gerente_to_both_locais()
        supervisor_alloc_a, _vendedor_alloc_a = self._distribute_branch_down(
            local_alloc_a,
            self.supervisor_a,
            self.vendedor_a,
            [self.local_a_user, self.supervisor_a_user],
            qty=600,
        )
        supervisor_alloc_b, vendedor_alloc_b = self._distribute_branch_down(
            local_alloc_b,
            self.supervisor_b,
            self.vendedor_b,
            [self.local_b_user, self.supervisor_b_user],
            qty=400,
        )

        # Supervisor A já repassou pro Vendedor A (trabalho real) — reopen() em Local A sozinho
        # seria bloqueado (trava 2026-08-04); reseta de baixo pra cima primeiro.
        ReopenAllocationService.reopen(supervisor_alloc_a, criado_por=self.supervisor_a_user)
        ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_a_user)

        # O ramo B, irmão não tocado, permanece intacto.
        self.assertTrue(GoalAllocation.objects.filter(id=supervisor_alloc_b.id, distributed=True).exists())
        self.assertTrue(GoalAllocation.objects.filter(id=vendedor_alloc_b.id).exists())
        local_alloc_b.refresh_from_db()
        self.assertTrue(local_alloc_b.distributed)

    def test_reopen_blocked_when_direct_child_already_distributed_further(self):
        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        DistributeGoalService.distribute(
            local_alloc_a,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor_a.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_a_user,
        )

        with self.assertRaises(AllocationReopenError) as ctx:
            ReopenAllocationService.reopen(self.gerente_allocation, criado_por=self.gerente_user)

        self.assertIn("Local A", str(ctx.exception))
        self.gerente_allocation.refresh_from_db()
        self.assertTrue(self.gerente_allocation.distributed)
        self.assertTrue(GoalAllocation.objects.filter(id=local_alloc_a.id).exists())

    def test_reopen_allowed_after_blocking_child_resets_itself_first(self):
        # A mesma situação bloqueada do teste acima, mas resolvida da forma pedida: quem está
        # travando (Local A, já tinha repassado pro Supervisor) reseta a própria distribuição
        # primeiro — só então o nível acima (Gerente) libera o reset dele.
        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        (supervisor_alloc,) = DistributeGoalService.distribute(
            local_alloc_a,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor_a.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_a_user,
        )

        with self.assertRaises(AllocationReopenError):
            ReopenAllocationService.reopen(self.gerente_allocation, criado_por=self.gerente_user)

        ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_a_user)
        self.assertFalse(GoalAllocation.objects.filter(id=supervisor_alloc.id).exists())

        ReopenAllocationService.reopen(self.gerente_allocation, criado_por=self.gerente_user)

        self.gerente_allocation.refresh_from_db()
        self.assertFalse(self.gerente_allocation.distributed)
        self.assertFalse(GoalAllocation.objects.filter(id=local_alloc_a.id).exists())

    def test_reopen_not_blocked_by_a_self_managed_cascade_even_with_real_quantity(self):
        """Achado real (2026-09-03): um repasse automático de autogestão
        (`SelfVendedorAutoDistributionService`) marca o filho Supervisor como `distributed=True`
        assim que a distribuição pro Supervisor é salva — mas isso não é decisão de ninguém (só
        existe 1 alvo possível), então não deve bloquear o reset do pai, mesmo com quantidade
        real. Antes desta correção, `reopen()` ainda usava a checagem antiga (sem essa exceção),
        então o botão aparecia na tela mas a ação falhava."""
        supervisor_self = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Fulano Da Silva", parent=self.local_a
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva", parent=supervisor_self
        )

        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        DistributeGoalService.distribute(
            local_alloc_a,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor_self.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup.id,
                )
            ],
            criado_por=self.local_a_user,
        )

        # Não levanta AllocationReopenError: o único filho distribuído (Fulano Da Silva,
        # Supervisor) é autogestão, então não conta como bloqueio, mesmo com 600 kg reais.
        ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_a_user)

        local_alloc_a.refresh_from_db()
        self.assertFalse(local_alloc_a.distributed)

    def test_reopen_rejects_when_caller_does_not_own_allocation(self):
        local_alloc_a, _local_alloc_b = self._distribute_gerente_to_both_locais()
        self._distribute_branch_down(
            local_alloc_a,
            self.supervisor_a,
            self.vendedor_a,
            [self.local_a_user, self.supervisor_a_user],
            qty=600,
        )

        with self.assertRaises(AllocationScopeError):
            ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_b_user)

        local_alloc_a.refresh_from_db()
        self.assertTrue(local_alloc_a.distributed)

    def test_reopen_rejects_when_not_yet_distributed(self):
        with self.assertRaises(AllocationReopenError):
            ReopenAllocationService.reopen(self.gerente_allocation, criado_por=self.gerente_user)

    def test_reopen_rejects_when_cycle_is_closed(self):
        local_alloc_a, local_alloc_b = self._distribute_gerente_to_both_locais()
        self._distribute_branch_down(
            local_alloc_a,
            self.supervisor_a,
            self.vendedor_a,
            [self.local_a_user, self.supervisor_a_user],
            qty=600,
        )
        self._distribute_branch_down(
            local_alloc_b,
            self.supervisor_b,
            self.vendedor_b,
            [self.local_b_user, self.supervisor_b_user],
            qty=400,
        )
        CloseCycleService.close(self.cycle)

        with self.assertRaises(AllocationReopenError):
            ReopenAllocationService.reopen(local_alloc_b, criado_por=self.local_b_user)

    def test_reopen_makes_cycle_incomplete_again_and_redistribute_closes_it_back(self):
        local_alloc_a, local_alloc_b = self._distribute_gerente_to_both_locais()
        supervisor_alloc_a, _vendedor_alloc_a = self._distribute_branch_down(
            local_alloc_a,
            self.supervisor_a,
            self.vendedor_a,
            [self.local_a_user, self.supervisor_a_user],
            qty=600,
        )
        self._distribute_branch_down(
            local_alloc_b,
            self.supervisor_b,
            self.vendedor_b,
            [self.local_b_user, self.supervisor_b_user],
            qty=400,
        )
        self.assertTrue(CycleCompletenessChecker.is_complete(self.cycle))

        # Supervisor A já repassou pro Vendedor A — reset de baixo pra cima (trava 2026-08-04).
        ReopenAllocationService.reopen(supervisor_alloc_a, criado_por=self.supervisor_a_user)
        ReopenAllocationService.reopen(local_alloc_a, criado_por=self.local_a_user)
        self.assertFalse(CycleCompletenessChecker.is_complete(self.cycle))

        # A mesma DistributeGoalService, sem nenhuma mudança, refaz o repasse normalmente.
        self._distribute_branch_down(
            local_alloc_a,
            self.supervisor_a,
            self.vendedor_a,
            [self.local_a_user, self.supervisor_a_user],
            qty=600,
        )

        self.assertTrue(CycleCompletenessChecker.is_complete(self.cycle))
        CloseCycleService.close(self.cycle)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.FECHADO)


class ReopenGroupServiceTests(TestCase):
    """`ReopenAllocationService.reopen_group` — pedido explícito do usuário (2026-09-03): reset em
    lote de todos os subgrupos já distribuídos de um grupo, pro nível que os possui, pra quando o
    nível errou a distribuição do grupo inteiro (telas "Meta Supervisor"/"Meta Vendedor")."""

    def setUp(self):
        self.group = ProductGroup.objects.create(nome="Embutidos")
        self.subgroup_x = ProductSubgroup.objects.create(nome="Linguiça", group=self.group)
        self.subgroup_y = ProductSubgroup.objects.create(nome="Salsicha", group=self.group)
        self.cycle = Cycle.objects.create(ano=2026, mes=7)

        self.local = HierarchyNode.objects.create(level=HierarchyNode.Level.LOCAL, nome="Local")
        self.local_user = User.objects.create_user(username="local", password="x", hierarchy_node=self.local)

        # Supervisor com 2 Vendedores (nomes diferentes) — nunca é autogestão, então qualquer
        # repasse manual dele pra baixo conta como trabalho real de verdade nos testes de bloqueio.
        self.supervisor = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Supervisor", parent=self.local
        )
        self.supervisor_user = User.objects.create_user(
            username="supervisor", password="x", hierarchy_node=self.supervisor
        )
        self.vendedor_1 = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 1", parent=self.supervisor
        )
        self.vendedor_2 = HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Vendedor 2", parent=self.supervisor
        )

    def _distribute_both_subgroups_to_supervisor(self):
        alloc_x = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_x,
            quantity_kg=1000,
            criado_por=self.local_user,
        )
        DistributeGoalService.distribute(
            alloc_x,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=1000,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                )
            ],
            criado_por=self.local_user,
        )
        alloc_y = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_y,
            quantity_kg=500,
            criado_por=self.local_user,
        )
        DistributeGoalService.distribute(
            alloc_y,
            [
                ChildAllocationSpec(
                    owner_node_id=self.supervisor.id,
                    quantity_kg=500,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_y.id,
                )
            ],
            criado_por=self.local_user,
        )
        return alloc_x, alloc_y

    def test_resets_all_subgroups_of_the_group_at_once(self):
        alloc_x, alloc_y = self._distribute_both_subgroups_to_supervisor()

        reset = ReopenAllocationService.reopen_group(
            owner_node=self.local, cycle=self.cycle, group_id=self.group.id, criado_por=self.local_user
        )

        self.assertEqual({a.id for a in reset}, {alloc_x.id, alloc_y.id})
        alloc_x.refresh_from_db()
        alloc_y.refresh_from_db()
        self.assertFalse(alloc_x.distributed)
        self.assertFalse(alloc_y.distributed)
        self.assertFalse(GoalAllocation.objects.filter(owner_node=self.supervisor).exists())

    def test_blocks_the_whole_group_when_any_subgroup_has_real_downstream_work(self):
        alloc_x, alloc_y = self._distribute_both_subgroups_to_supervisor()
        # Supervisor repassa X de verdade pros Vendedores dele (decisão manual, não autogestão) —
        # isso deve travar o reset do grupo INTEIRO, inclusive Y, que nem foi tocado pelo Supervisor.
        supervisor_alloc_x = GoalAllocation.objects.get(owner_node=self.supervisor, subgroup=self.subgroup_x)
        DistributeGoalService.distribute(
            supervisor_alloc_x,
            [
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_1.id,
                    quantity_kg=600,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                ),
                ChildAllocationSpec(
                    owner_node_id=self.vendedor_2.id,
                    quantity_kg=400,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                ),
            ],
            criado_por=self.supervisor_user,
        )

        with self.assertRaises(AllocationReopenError) as ctx:
            ReopenAllocationService.reopen_group(
                owner_node=self.local, cycle=self.cycle, group_id=self.group.id, criado_por=self.local_user
            )

        self.assertIn("Supervisor", str(ctx.exception))
        alloc_x.refresh_from_db()
        alloc_y.refresh_from_db()
        self.assertTrue(alloc_x.distributed)
        self.assertTrue(alloc_y.distributed)

    def test_ignores_self_managed_cascade_even_with_real_quantity(self):
        supervisor_self = HierarchyNode.objects.create(
            level=HierarchyNode.Level.SUPERVISOR, nome="Fulano Da Silva", parent=self.local
        )
        HierarchyNode.objects.create(
            level=HierarchyNode.Level.VENDEDOR, nome="Fulano Da Silva", parent=supervisor_self
        )
        alloc_x = GoalAllocation.objects.create(
            cycle=self.cycle,
            owner_node=self.local,
            granularity=GoalAllocation.Granularity.SUBGROUP,
            subgroup=self.subgroup_x,
            quantity_kg=1000,
            criado_por=self.local_user,
        )
        DistributeGoalService.distribute(
            alloc_x,
            [
                ChildAllocationSpec(
                    owner_node_id=supervisor_self.id,
                    quantity_kg=1000,
                    granularity=GoalAllocation.Granularity.SUBGROUP,
                    subgroup_id=self.subgroup_x.id,
                )
            ],
            criado_por=self.local_user,
        )

        reset = ReopenAllocationService.reopen_group(
            owner_node=self.local, cycle=self.cycle, group_id=self.group.id, criado_por=self.local_user
        )

        self.assertEqual({a.id for a in reset}, {alloc_x.id})
        alloc_x.refresh_from_db()
        self.assertFalse(alloc_x.distributed)

    def test_rejects_when_caller_does_not_own_the_node(self):
        self._distribute_both_subgroups_to_supervisor()
        other_user = User.objects.create_user(username="other", password="x")

        with self.assertRaises(AllocationScopeError):
            ReopenAllocationService.reopen_group(
                owner_node=self.local, cycle=self.cycle, group_id=self.group.id, criado_por=other_user
            )

    def test_raises_when_nothing_to_reset(self):
        with self.assertRaises(AllocationReopenError):
            ReopenAllocationService.reopen_group(
                owner_node=self.local, cycle=self.cycle, group_id=self.group.id, criado_por=self.local_user
            )
