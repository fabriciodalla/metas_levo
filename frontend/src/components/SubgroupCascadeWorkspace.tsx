import { Boxes, Calendar } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { GoalAllocation, ProductGroup, ProductSubgroup } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useCycleAllocationsData } from "../pages/useCycleAllocationsData";
import { ResetGroupDistributionButton } from "./ResetGroupDistributionButton";
import { SupervisorDistributionWorkspace, type SupervisorWorkspaceRow } from "./SupervisorDistributionWorkspace";
import { useGroupSupervisorDraft } from "./useGroupSupervisorDraft";
import { Alert } from "./ui/Alert";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";
import { SummaryCard } from "./ui/SummaryCard";

function formatKg(value: number): string {
  return `${Math.round(value).toLocaleString("pt-BR")} kg`;
}

function formatPct(value: number): string {
  return `${Math.round(value)}%`;
}

interface GroupOption {
  id: number;
  nome: string;
}

function SubgroupSidebar({
  subgroups,
  nomeOf,
  selectedId,
  onSelect,
  distribuidoFor,
}: {
  subgroups: GoalAllocation[];
  nomeOf: (allocation: GoalAllocation) => string;
  selectedId: number | null;
  onSelect: (id: number) => void;
  distribuidoFor: (allocation: GoalAllocation) => number;
}) {
  const totalMeta = subgroups.reduce((sum, a) => sum + a.quantity_kg, 0);
  const totalRestante = subgroups.reduce((sum, a) => sum + (a.quantity_kg - distribuidoFor(a)), 0);

  return (
    <div className="sv-sidebar">
      <div className="sv-sidebar-header">
        <h4>Subgrupos</h4>
      </div>
      <div className="sv-sidebar-columns">
        <span>Subgrupo</span>
        <span>Meta do subgrupo</span>
        <span>Restante</span>
      </div>
      <div className="sv-sidebar-list" role="listbox" aria-label="Subgrupos do grupo selecionado">
        {subgroups.length === 0 ? (
          <EmptyState>Nenhum subgrupo disponível para o grupo selecionado.</EmptyState>
        ) : (
          subgroups.map((allocation) => {
            const distribuido = distribuidoFor(allocation);
            const restante = allocation.quantity_kg - distribuido;
            const selected = allocation.id === selectedId;

            return (
              <button
                key={allocation.id}
                type="button"
                role="option"
                aria-selected={selected}
                className="sv-sidebar-item"
                onClick={() => onSelect(allocation.id)}
              >
                <span className="sv-sidebar-item-name">{nomeOf(allocation)}</span>
                <span className="sv-sidebar-item-meta">{formatKg(allocation.quantity_kg)}</span>
                <span
                  className={
                    restante === 0
                      ? "sv-sidebar-item-restante sv-sidebar-item-restante-done"
                      : "sv-sidebar-item-restante sv-sidebar-item-restante-pending"
                  }
                >
                  {formatKg(restante)}
                </span>
              </button>
            );
          })
        )}
      </div>
      <div className="sv-sidebar-footer">
        <div className="sv-sidebar-footer-row">
          <span className="sv-sidebar-footer-label">Total dos subgrupos</span>
          <span className="sv-sidebar-footer-value">{formatKg(totalMeta)}</span>
        </div>
        <div className="sv-sidebar-footer-row">
          <span className="sv-sidebar-footer-label">Restante total</span>
          <span
            className={
              totalRestante !== 0 ? "sv-sidebar-footer-value sv-sidebar-footer-value-warning" : "sv-sidebar-footer-value"
            }
          >
            {formatKg(totalRestante)}
          </span>
        </div>
      </div>
    </div>
  );
}

interface Props {
  /** Nível de quem está distribuindo (dono das alocações SUBGROUP): LOCAL em "Meta Supervisor",
   * SUPERVISOR em "Meta Vendedor". */
  ownerLevel: string;
  noAccessMessage: string;
  /** "Supervisores" / "Vendedores" — card do topo e título do painel de distribuição. */
  targetLabelPlural: string;
  /** "supervisor" / "vendedor" — usado em minúsculo dentro de frases. */
  targetLabelSingular: string;
}

// Motor comum das telas "Meta Supervisor" (Coordenador Local → Supervisor) e "Meta Vendedor"
// (Supervisor → Vendedor) — mesma estrutura, mesmos cálculos, só o nível de quem distribui e o
// rótulo do alvo mudam (ver MetaSupervisorPage.tsx / MetaVendedorPage.tsx). Ciclo + Grupo filtram
// a visão; a lateral esquerda lista os subgrupos do grupo selecionado e o carrossel à direita
// mostra, por alvo, só o subgrupo escolhido — nunca todos ao mesmo tempo. O rascunho de TODOS os
// subgrupos do grupo fica vivo ao navegar entre eles (useGroupSupervisorDraft) — só é descartado
// ao trocar de grupo/ciclo (com aviso) ou depois de salvo; "Salvar distribuição" grava de uma vez
// todo subgrupo do grupo, sem exigir um clique por subgrupo — mas só libera quando TODOS os
// subgrupos do grupo (não só os que a pessoa abriu) estão completos: todo alvo preenchido, soma =
// meta exata (revisão 2026-09-03, ver `canSaveGroup`) — mesma regra já exigida em
// Gerente→Local (`useDistributionRows`/`DistributionForm`), aplicada aqui ao
// grupo inteiro em vez de uma alocação por vez. Cada subgrupo continua persistido pelo mesmo
// endpoint POST /allocations/{id}/distribute/ já usado nessas telas, com a mesma invariante de
// fechamento exato — só a orquestração muda.
export function SubgroupCascadeWorkspace({ ownerLevel, noAccessMessage, targetLabelPlural, targetLabelSingular }: Props) {
  const { user } = useAuth();
  const { cycles, selectedCycleId, setSelectedCycleId, allocations, nodes, loading, refresh } =
    useCycleAllocationsData();

  const [groups, setGroups] = useState<ProductGroup[]>([]);
  const [subgroups, setSubgroups] = useState<ProductSubgroup[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [selectedSubgroupAllocationId, setSelectedSubgroupAllocationId] = useState<number | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  useEffect(() => {
    setCatalogLoading(true);
    void Promise.all([
      api.get<ProductGroup[]>("/catalog/groups/"),
      api.get<ProductSubgroup[]>("/catalog/subgroups/"),
    ])
      .then(([groupData, subgroupData]) => {
        setGroups(groupData);
        setSubgroups(subgroupData);
      })
      .finally(() => setCatalogLoading(false));
  }, []);

  useEffect(() => {
    if (!savedMessage) return;
    const timer = setTimeout(() => setSavedMessage(null), 5000);
    return () => clearTimeout(timer);
  }, [savedMessage]);

  const subgroupById = useMemo(() => new Map(subgroups.map((s) => [s.id, s])), [subgroups]);
  const groupById = useMemo(() => new Map(groups.map((g) => [g.id, g])), [groups]);

  function subgroupNomeOf(allocation: GoalAllocation): string {
    return allocation.subgroup !== null
      ? (subgroupById.get(allocation.subgroup)?.nome ?? `Subgrupo ${allocation.subgroup}`)
      : "Subgrupo";
  }

  const myOwnerNodeIds = useMemo(
    () => new Set(user?.hierarchy_nodes.filter((n) => n.level === ownerLevel).map((n) => n.id) ?? []),
    [user, ownerLevel],
  );

  // Subgrupos com meta zerada (quem distribui ainda não atribuiu nada a eles) não têm o que
  // repassar adiante — ficam fora da lista lateral, mas contam 0 nos totais de qualquer forma
  // (não afeta nenhuma soma, só o que é exibido).
  const mySubgroupAllocations = useMemo(
    () =>
      allocations.filter(
        (a) => myOwnerNodeIds.has(a.owner_node) && a.granularity === "SUBGROUP" && a.quantity_kg > 0,
      ),
    [allocations, myOwnerNodeIds],
  );

  const groupOptions = useMemo<GroupOption[]>(() => {
    const ids = new Set<number>();
    for (const allocation of mySubgroupAllocations) {
      const subgroup = allocation.subgroup !== null ? subgroupById.get(allocation.subgroup) : undefined;
      if (subgroup) ids.add(subgroup.group);
    }
    return Array.from(ids)
      .map((id) => ({ id, nome: groupById.get(id)?.nome ?? `Grupo ${id}` }))
      .sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR", { sensitivity: "base" }));
  }, [mySubgroupAllocations, subgroupById, groupById]);

  useEffect(() => {
    setSelectedGroupId((current) =>
      current !== null && groupOptions.some((g) => g.id === current) ? current : (groupOptions[0]?.id ?? null),
    );
  }, [groupOptions]);

  const subgroupsInGroup = useMemo(
    () =>
      mySubgroupAllocations
        .filter((a) => a.subgroup !== null && subgroupById.get(a.subgroup)?.group === selectedGroupId)
        .sort((a, b) => subgroupNomeOf(a).localeCompare(subgroupNomeOf(b), "pt-BR", { sensitivity: "base" })),
    [mySubgroupAllocations, subgroupById, selectedGroupId],
  );

  useEffect(() => {
    setSelectedSubgroupAllocationId((current) =>
      current !== null && subgroupsInGroup.some((a) => a.id === current)
        ? current
        : (subgroupsInGroup[0]?.id ?? null),
    );
  }, [subgroupsInGroup]);

  const selectedAllocation = subgroupsInGroup.find((a) => a.id === selectedSubgroupAllocationId) ?? null;

  // Todo subgrupo do grupo selecionado pertence ao mesmo nó (quem está distribuindo) — os alvos
  // são filhos diretos desse nó, então não dependem de qual subgrupo está aberto no momento, só do
  // grupo.
  const groupOwnerNodeId = subgroupsInGroup[0]?.owner_node ?? null;
  // `nodes` inclui nós inativados (o admin precisa vê-los na tela de Hierarquia) — sem o filtro
  // de `ativo`, um Supervisor/Vendedor removido/substituído (Decisão 10, O4) continuava aparecendo
  // como alvo de distribuição ao lado de quem ocupa a posição agora.
  const targets = useMemo(
    () => (groupOwnerNodeId !== null ? nodes.filter((n) => n.parent === groupOwnerNodeId && n.ativo) : []),
    [nodes, groupOwnerNodeId],
  );

  const groupDraft = useGroupSupervisorDraft(selectedGroupId, targets);

  useEffect(() => {
    if (!selectedAllocation) return;
    if (selectedAllocation.distributed) {
      // Só o contexto (pra "Média 3 meses"/"% crescimento" informativas no card) — sem criar
      // rascunho nenhum, já que este subgrupo não tem mais nada a distribuir.
      groupDraft.ensureContextLoaded(selectedAllocation.id);
    } else {
      groupDraft.ensureLoaded(selectedAllocation.id);
    }
  }, [selectedAllocation, groupDraft.ensureLoaded, groupDraft.ensureContextLoaded]);

  const persistedTotalsAllGroups = useMemo(() => {
    const totals = new Map<number, number>();
    for (const target of targets) {
      let sum = 0;
      for (const a of allocations) if (a.owner_node === target.id) sum += a.quantity_kg;
      totals.set(target.id, sum);
    }
    return totals;
  }, [allocations, targets]);

  const persistedTotalsInGroup = useMemo(() => {
    const totals = new Map<number, number>();
    for (const target of targets) {
      let sum = 0;
      for (const a of allocations) {
        if (a.owner_node !== target.id || a.subgroup === null) continue;
        if (subgroupById.get(a.subgroup)?.group !== selectedGroupId) continue;
        sum += a.quantity_kg;
      }
      totals.set(target.id, sum);
    }
    return totals;
  }, [allocations, targets, subgroupById, selectedGroupId]);

  const groupTotalKg = useMemo(() => subgroupsInGroup.reduce((sum, a) => sum + a.quantity_kg, 0), [subgroupsInGroup]);
  const distributedSubgroupsInGroup = useMemo(
    () => subgroupsInGroup.filter((a) => a.distributed),
    [subgroupsInGroup],
  );

  // Fonte única do que já foi "efetivamente distribuído" pra cada subgrupo — persistido de
  // verdade se já foi salvo, senão o rascunho ao vivo (ainda não salvo) desta sessão. Usado tanto
  // na lateral (todos os subgrupos, ao mesmo tempo) quanto nos cards do topo (soma do grupo).
  function distribuidoFor(allocation: GoalAllocation): number {
    if (allocation.distributed) {
      return allocations
        .filter((a) => a.parent_allocation === allocation.id)
        .reduce((sum, a) => sum + a.quantity_kg, 0);
    }
    return groupDraft.totalFor(allocation.id);
  }

  // Soma do rascunho ao vivo de um alvo em TODOS os subgrupos ainda não salvos do grupo
  // selecionado (não só o que está aberto no carrossel no momento).
  function liveDraftForTarget(targetId: number): number {
    let sum = 0;
    for (const allocation of subgroupsInGroup) {
      if (allocation.distributed) continue;
      const value = groupDraft.rowValue(allocation.id, targetId);
      if (typeof value === "number") sum += value;
    }
    return sum;
  }

  function confirmDiscard(): boolean {
    if (!groupDraft.hasAnyDraft(subgroupsInGroup)) return true;
    return window.confirm("Você tem alterações não salvas neste grupo. Deseja descartá-las?");
  }

  function handleSelectCycle(cycleId: number) {
    if (cycleId === selectedCycleId || !confirmDiscard()) return;
    setSelectedGroupId(null);
    setSelectedSubgroupAllocationId(null);
    setSelectedCycleId(cycleId);
  }

  function handleSelectGroup(groupId: number) {
    if (groupId === selectedGroupId || !confirmDiscard()) return;
    setSelectedSubgroupAllocationId(null);
    setSelectedGroupId(groupId);
  }

  function handleSelectSubgroup(allocationId: number) {
    setSelectedSubgroupAllocationId(allocationId);
  }

  function handleSaveGroup() {
    void groupDraft.saveGroup(subgroupsInGroup, subgroupNomeOf, (saved) => {
      refresh();
      const names = saved.map(subgroupNomeOf);
      setSavedMessage(
        names.length === 1
          ? `Distribuição de ${names[0]} salva com sucesso.`
          : `Distribuição salva com sucesso: ${names.join(", ")}.`,
      );
    });
  }

  // Pedido explícito do usuário (2026-09-03): resetar todos os subgrupos já distribuídos do grupo
  // de uma vez (ResetGroupDistributionButton), em vez de um por um — e já recarregar a sugestão
  // automática (`force=true`) em cada um, pronta pra revisar e salvar de novo (nunca se auto-aplica).
  function handleResetGroup(resetAllocationIds: number[]) {
    refresh();
    for (const allocationId of resetAllocationIds) {
      groupDraft.ensureLoaded(allocationId, true);
    }
    setSavedMessage(
      resetAllocationIds.length === 1
        ? "1 subgrupo resetado — sugestão automática recarregada."
        : `${resetAllocationIds.length} subgrupos resetados — sugestão automática recarregada.`,
    );
  }

  if (myOwnerNodeIds.size === 0) {
    return <EmptyState>{noAccessMessage}</EmptyState>;
  }

  const selectedSubgroupNome = selectedAllocation ? subgroupNomeOf(selectedAllocation) : "";
  const isLoading = loading || catalogLoading;
  const selectedGroupNome = groupOptions.find((g) => g.id === selectedGroupId)?.nome ?? "";

  // Cards do topo resumem o GRUPO inteiro (todos os subgrupos, com rascunho ao vivo incluído) —
  // mesmos números do rodapé do painel esquerdo, "Total dos subgrupos"/"Restante total".
  const distribuidoGrupo = subgroupsInGroup.reduce((sum, a) => sum + distribuidoFor(a), 0);
  const restanteGrupo = groupTotalKg - distribuidoGrupo;
  const percentDistribuido = groupTotalKg > 0 ? (distribuidoGrupo / groupTotalKg) * 100 : 0;
  const percentRestante = groupTotalKg > 0 ? (Math.abs(restanteGrupo) / groupTotalKg) * 100 : 0;
  const targetsAtivos = targets.filter((n) => n.ativo).length;

  const isSelectedDistributed = selectedAllocation?.distributed ?? false;

  const workspaceRows: SupervisorWorkspaceRow[] = !selectedAllocation
    ? []
    : isSelectedDistributed
      ? (() => {
          // Mesmo aqui (subgrupo aberto no momento já salvo, cards em modo leitura) precisa somar
          // `liveDraftForTarget` — senão "Meta no grupo"/"Total distribuído no grupo" cai de
          // repente ao navegar pra um subgrupo já fechado, ignorando o rascunho ainda não salvo
          // dos OUTROS subgrupos do mesmo grupo (bug real, 2026-08-07: a soma "sumia" só de
          // clicar entre subgrupos, sem nenhuma mudança de valor).
          const persistedChildren = allocations.filter((a) => a.parent_allocation === selectedAllocation.id);
          return targets.map((target) => {
            const child = persistedChildren.find((c) => c.owner_node === target.id);
            return {
              supervisor: target,
              quantityKg: child?.quantity_kg ?? 0,
              metaTotalSupervisorKg: (persistedTotalsAllGroups.get(target.id) ?? 0) + liveDraftForTarget(target.id),
              metaSupervisorGrupoKg: (persistedTotalsInGroup.get(target.id) ?? 0) + liveDraftForTarget(target.id),
              last3MonthsAvgKg:
                groupDraft.contextBySubgroup[selectedAllocation.id]?.[target.id]?.last_3_months_avg_kg ?? null,
            };
          });
        })()
      : targets.map((target) => ({
          supervisor: target,
          quantityKg: groupDraft.rowValue(selectedAllocation.id, target.id),
          onChange: (value: number | "") => groupDraft.updateCell(selectedAllocation.id, target.id, value),
          metaTotalSupervisorKg: (persistedTotalsAllGroups.get(target.id) ?? 0) + liveDraftForTarget(target.id),
          metaSupervisorGrupoKg: (persistedTotalsInGroup.get(target.id) ?? 0) + liveDraftForTarget(target.id),
          last3MonthsAvgKg:
            groupDraft.contextBySubgroup[selectedAllocation.id]?.[target.id]?.last_3_months_avg_kg ?? null,
        }));

  const workspaceTotal = selectedAllocation ? distribuidoFor(selectedAllocation) : 0;
  const workspaceDiff = selectedAllocation ? selectedAllocation.quantity_kg - workspaceTotal : 0;
  const groupHasDraft = groupDraft.hasAnyDraft(subgroupsInGroup);
  // Regra já estipulada nas outras telas de distribuição (Gerente→Local): só
  // libera "Salvar distribuição" quando TODO subgrupo do grupo (com meta > 0) tem todos os alvos
  // preenchidos e fecha exato com a meta — nem mais, nem menos, e nem só os que a pessoa abriu
  // (ver `canSaveGroup`); equivale a exigir "Restante total" (rodapé da lateral) zerado.
  const groupIsReadyToSave = groupDraft.canSaveGroup(subgroupsInGroup);

  return (
    <section className="sv-shell">
      <div className="dp-filters">
        <div className="dp-filter-field">
          <label className="field-label" htmlFor="cycle-select">
            Ciclo
          </label>
          <div className="dp-filter-input">
            <Calendar size={16} />
            <select
              id="cycle-select"
              value={selectedCycleId ?? ""}
              onChange={(e) => handleSelectCycle(Number(e.target.value))}
            >
              {cycles.map((cycle) => (
                <option key={cycle.id} value={cycle.id}>
                  {String(cycle.mes).padStart(2, "0")}/{cycle.ano} ({cycle.status})
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="dp-filter-field">
          <label className="field-label" htmlFor="grupo-select">
            Grupo
          </label>
          <div className="dp-filter-input">
            <Boxes size={16} />
            <select
              id="grupo-select"
              value={selectedGroupId ?? ""}
              disabled={groupOptions.length === 0}
              onChange={(e) => handleSelectGroup(Number(e.target.value))}
            >
              {groupOptions.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.nome}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {savedMessage && <Alert variant="success">{savedMessage}</Alert>}

      {isLoading ? (
        <>
          <div className="summary-row">
            <Skeleton className="skeleton-summary-card" />
            <Skeleton className="skeleton-summary-card" />
            <Skeleton className="skeleton-summary-card" />
            <Skeleton className="skeleton-summary-card" />
          </div>
          <div className="sv-body">
            <Skeleton className="sv-sidebar" />
            <Skeleton className="sv-workspace" />
          </div>
        </>
      ) : groupOptions.length === 0 ? (
        <EmptyState>Nenhum grupo disponível para este ciclo.</EmptyState>
      ) : (
        <>
          <div className="summary-row">
            <SummaryCard
              label="META DO GRUPO"
              value={formatKg(groupTotalKg)}
              caption={selectedGroupNome || undefined}
            />
            <SummaryCard
              label="DISTRIBUÍDO"
              value={formatKg(distribuidoGrupo)}
              progress={{ percent: percentDistribuido, variant: "success" }}
              progressLabel={
                <span className="summary-card-progress-positive">{formatPct(percentDistribuido)}</span>
              }
            />
            <SummaryCard
              label="RESTANTE"
              accent="warning"
              value={formatKg(Math.abs(restanteGrupo))}
              progress={{ percent: percentRestante, variant: restanteGrupo < 0 ? "danger" : "warning" }}
              progressLabel={formatPct(percentRestante)}
            />
            <SummaryCard
              label={targetLabelPlural.toUpperCase()}
              value={String(targets.length)}
              caption={`${targetsAtivos} ativos`}
            />
          </div>

          {groupOwnerNodeId !== null && selectedGroupId !== null && selectedCycleId !== null && (
            <div className="group-overview-actions">
              <ResetGroupDistributionButton
                ownerNodeId={groupOwnerNodeId}
                cycleId={selectedCycleId}
                groupId={selectedGroupId}
                groupNome={selectedGroupNome}
                distributedSubgroups={distributedSubgroupsInGroup}
                onReset={handleResetGroup}
              />
            </div>
          )}

          <div className="sv-body">
            <SubgroupSidebar
              subgroups={subgroupsInGroup}
              nomeOf={subgroupNomeOf}
              selectedId={selectedSubgroupAllocationId}
              onSelect={handleSelectSubgroup}
              distribuidoFor={distribuidoFor}
            />

            {!selectedAllocation ? (
              <div className="sv-workspace">
                <EmptyState>Nenhum subgrupo disponível para o grupo selecionado.</EmptyState>
              </div>
            ) : (
              <SupervisorDistributionWorkspace
                subgroupNome={selectedSubgroupNome}
                rows={workspaceRows}
                total={workspaceTotal}
                diff={workspaceDiff}
                editable={!isSelectedDistributed}
                submitting={groupDraft.submitting}
                canSave={groupIsReadyToSave && !groupDraft.submitting}
                hasDraft={groupHasDraft}
                error={groupDraft.error}
                info={groupDraft.info}
                onSave={handleSaveGroup}
                groupTotalKg={groupTotalKg}
                title={`Distribuição para ${targetLabelPlural}`}
                emptyRowsMessage={`Nenhum ${targetLabelSingular} disponível para distribuição.`}
                cardMetaLabel={`Meta do ${targetLabelSingular}`}
              />
            )}
          </div>

          <Alert variant="info">
            A soma das metas definidas deve ser igual à meta de cada subgrupo. Você pode navegar entre os
            subgrupos e grupos sem perder o que já preencheu — mas "Salvar distribuição" só libera quando
            TODOS os subgrupos do grupo (não só os que você abriu) tiverem todos os alvos preenchidos e
            fecharem exato com a meta (nem mais, nem menos) — passe por todos antes de salvar.
          </Alert>
        </>
      )}
    </section>
  );
}
