import { Calendar, ChevronDown, ChevronUp, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import type { GoalAllocation } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { DistributionForm } from "../components/DistributionForm";
import { GroupCycleOverview, STATUS_FILTER_OPTIONS, type StatusFilter } from "../components/GroupCycleOverview";
import { ResetDistributionButton } from "../components/ResetDistributionButton";
import { Card } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { Spinner } from "../components/ui/Spinner";
import { EmptyState } from "../components/ui/EmptyState";
import { useCycleAllocationsData } from "./useCycleAllocationsData";

// Alocações do Coordenador Local (GROUP a quebrar por subgrupo, SUBGROUP a distribuir pra
// Supervisor) e do Supervisor (SUBGROUP a distribuir pro Vendedor) têm telas próprias —
// "Distribuir Produtos", "Meta Supervisor" e "Meta Vendedor" — e não aparecem mais nas listas
// genéricas desta tela, pra não duplicar o mesmo item em dois lugares.
function belongsToDedicatedSplitFlow(allocation: GoalAllocation): boolean {
  if (allocation.owner_node_level === "LOCAL") {
    return allocation.granularity === "GROUP" || allocation.granularity === "SUBGROUP";
  }
  return allocation.owner_node_level === "SUPERVISOR" && allocation.granularity === "SUBGROUP";
}

export function DistributionPage() {
  const { user } = useAuth();
  const { cycles, selectedCycleId, setSelectedCycleId, allocations, nodes, loading, refresh } =
    useCycleAllocationsData();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const myNodeIds = useMemo(() => new Set(user?.hierarchy_nodes.map((n) => n.id) ?? []), [user]);
  const gerenteNode = useMemo(() => user?.hierarchy_nodes.find((n) => n.level === "GERENTE"), [user]);
  const supervisorNode = useMemo(() => user?.hierarchy_nodes.find((n) => n.level === "SUPERVISOR"), [user]);
  const localNode = useMemo(() => user?.hierarchy_nodes.find((n) => n.level === "LOCAL"), [user]);

  // Gerente→Local (P1: sugestão -> criação -> distribuição) ganha a visão unificada
  // GroupCycleOverview logo abaixo; as listas genéricas só cobrem o que sobra (outros níveis, ou
  // um usuário com mais de um nó vinculado) para não duplicar o mesmo item nas duas telas.
  const overviewNode = gerenteNode;
  const canCreateGoals = !!gerenteNode;

  const pending = useMemo(
    () =>
      allocations.filter(
        (a) =>
          myNodeIds.has(a.owner_node) &&
          !a.distributed &&
          a.owner_node !== overviewNode?.id &&
          !belongsToDedicatedSplitFlow(a),
      ),
    [allocations, myNodeIds, overviewNode],
  );
  const done = useMemo(
    () =>
      allocations.filter(
        (a) =>
          myNodeIds.has(a.owner_node) &&
          a.distributed &&
          a.owner_node !== overviewNode?.id &&
          !belongsToDedicatedSplitFlow(a),
      ),
    [allocations, myNodeIds, overviewNode],
  );
  const myAllocations = useMemo(
    () =>
      allocations.filter(
        (a) => a.owner_node === overviewNode?.id && (canCreateGoals ? a.parent_allocation === null : true),
      ),
    [allocations, overviewNode, canCreateGoals],
  );
  // Fica oculta pra quem só tem Gerente (GroupCycleOverview já cobre tudo, lista vazia
  // aqui) e aparece pra quem também tem outro nó com pendência fora do fluxo de subgrupo do Local.
  const showGenericLists = !overviewNode || pending.length > 0 || done.length > 0;

  function handleChanged() {
    refresh();
    setExpandedId(null);
  }

  // Coordenador Local puro (sem Gerente/Supervisor) não tem mais uso pra esta tela — o
  // fluxo dele inteiro já vive em "Distribuir Produtos"/"Meta Supervisor".
  if (!gerenteNode && !supervisorNode && localNode) {
    return <Navigate to="/distribuicao/distribuir-produtos" replace />;
  }
  // Supervisor puro (sem Gerente/Local) não tem mais uso pra esta tela — o fluxo dele
  // inteiro já vive em "Meta Vendedor".
  if (!gerenteNode && !localNode && supervisorNode) {
    return <Navigate to="/distribuicao/meta-vendedor" replace />;
  }

  return (
    <section>
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
              onChange={(e) => setSelectedCycleId(Number(e.target.value))}
            >
              {cycles.map((cycle) => (
                <option key={cycle.id} value={cycle.id}>
                  {String(cycle.mes).padStart(2, "0")}/{cycle.ano} ({cycle.status})
                </option>
              ))}
            </select>
          </div>
        </div>

        {overviewNode && (
          <div className="dp-filter-field">
            <label className="field-label" htmlFor="status-filter">
              Exibir
            </label>
            <div className="dp-filter-input">
              <SlidersHorizontal size={16} />
              <select
                id="status-filter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              >
                {STATUS_FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      {loading && !overviewNode && <Spinner />}

      {overviewNode && selectedCycleId !== null && (
        <GroupCycleOverview
          cycleId={selectedCycleId}
          ownerNodeId={overviewNode.id}
          canCreateGoals={canCreateGoals}
          childLevelLabel="coordenador local"
          nodes={nodes}
          myAllocations={myAllocations}
          allAllocations={allocations}
          statusFilter={statusFilter}
          onChanged={handleChanged}
        />
      )}

      {showGenericLists && (
        <>
          <h2>Para distribuir ({pending.length})</h2>
          {pending.length === 0 && !loading && (
            <EmptyState>Nada pendente no seu nível para este ciclo.</EmptyState>
          )}
          {pending.map((allocation) => {
            // `nodes` inclui nós inativados (o admin precisa vê-los na tela de Hierarquia) — sem
            // o filtro de `ativo`, um Coordenador removido/substituído (Decisão 10, O4) continuava
            // aparecendo como alvo de distribuição ao lado de quem ocupa a posição agora.
            const directChildren = nodes.filter((n) => n.parent === allocation.owner_node && n.ativo);
            const isExpanded = expandedId === allocation.id;
            return (
              <Card key={allocation.id}>
                <button
                  type="button"
                  className="card-interactive"
                  onClick={() => setExpandedId(isExpanded ? null : allocation.id)}
                >
                  <span>
                    <strong>{allocation.quantity_kg} kg</strong> — {allocation.granularity}
                  </span>
                  {isExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                </button>
                {isExpanded && (
                  <DistributionForm
                    allocation={allocation}
                    directChildren={directChildren}
                    onDistributed={handleChanged}
                  />
                )}
              </Card>
            );
          })}

          <h2>Já distribuído ({done.length})</h2>
          {done.length === 0 && !loading && <EmptyState>Nada distribuído ainda neste ciclo.</EmptyState>}
          {done.length > 0 && (
            <Card>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Quantidade</th>
                      <th>Granularidade</th>
                      <th>Status</th>
                      <th>Ações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {done.map((allocation) => (
                      <tr key={allocation.id}>
                        <td>{allocation.quantity_kg} kg</td>
                        <td>{allocation.granularity}</td>
                        <td>
                          <Badge variant="success">Distribuído</Badge>
                        </td>
                        <td>
                          <ResetDistributionButton allocation={allocation} onReset={handleChanged} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}
    </section>
  );
}
