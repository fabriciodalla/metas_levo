import { AlertTriangle, ChevronDown, ChevronUp, Filter } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type { GoalAllocation, GroupSuggestion, HierarchyNode, ProductGroup } from "../api/types";
import { GroupChildDistributionTable } from "./GroupChildDistributionTable";
import { ResetDistributionButton } from "./ResetDistributionButton";
import { Sparkline } from "./Sparkline";
import { useDistributionRows, type DistributionRow } from "./useDistributionRows";
import { Alert } from "./ui/Alert";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { EmptyState } from "./ui/EmptyState";
import { MetricChip } from "./ui/MetricChip";
import { NumericKgInput } from "./ui/NumericKgInput";
import { ProgressBar } from "./ui/ProgressBar";
import { Skeleton } from "./ui/Skeleton";
import { SummaryCard } from "./ui/SummaryCard";

interface Props {
  cycleId: number;
  ownerNodeId: number;
  // Só o Gerente cria metas raiz a partir da sugestão P1 (grupo sem meta ainda) — é o único
  // nível que usa esta visão hoje (ver DistributionPage).
  canCreateGoals: boolean;
  childLevelLabel: string;
  nodes: HierarchyNode[];
  myAllocations: GoalAllocation[];
  allAllocations: GoalAllocation[];
  statusFilter: StatusFilter;
  onChanged: () => void;
}

export type StatusFilter = "all" | "suggestion" | "pending" | "distributed";

export const STATUS_FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "Todos os grupos" },
  { value: "suggestion", label: "Não iniciados" },
  { value: "pending", label: "Em andamento" },
  { value: "distributed", label: "Concluídos" },
];

function formatKg(value: number): string {
  return `${Math.round(value).toLocaleString("pt-BR")} kg`;
}

function formatPct(value: number): string {
  return `${Math.round(value)}%`;
}

type RowStatus = "suggestion" | "pending" | "distributed";

interface GroupRow {
  groupId: number;
  groupNome: string;
  suggestion: GroupSuggestion | null;
  allocation?: GoalAllocation;
  status: RowStatus;
}

function formatSeasonalIndex(index: number): string {
  const pct = Math.round((index - 1) * 100);
  if (pct === 0) return "na média dos últimos 12 meses";
  return pct > 0 ? `${pct}% acima da média dos últimos 12 meses` : `${Math.abs(pct)}% abaixo da média dos últimos 12 meses`;
}

function StatusBadge({ status }: { status: RowStatus }) {
  if (status === "suggestion") return <Badge variant="neutral">Não iniciado</Badge>;
  if (status === "pending") return <Badge variant="warning">Em andamento</Badge>;
  return <Badge variant="success">Concluído</Badge>;
}

interface CycleSummary {
  metaTotalKg: number;
  distribuidoKg: number;
  restanteKg: number;
  pendenciasCount: number;
}

function CycleSummaryCards({ summary }: { summary: CycleSummary }) {
  const percentDistributed = summary.metaTotalKg > 0 ? (summary.distribuidoKg / summary.metaTotalKg) * 100 : 0;
  const percentRemaining = summary.metaTotalKg > 0 ? (summary.restanteKg / summary.metaTotalKg) * 100 : 0;

  return (
    <div className="summary-row">
      <SummaryCard
        label="META TOTAL DO CICLO"
        value={formatKg(summary.metaTotalKg)}
        caption="Soma de todos os grupos"
        tooltip="Inclui metas já criadas e, para grupos ainda não iniciados, a sugestão automática."
      />
      <SummaryCard
        label="DISTRIBUÍDO"
        value={formatKg(summary.distribuidoKg)}
        progress={{ percent: percentDistributed, variant: "success" }}
        progressLabel={<span className="summary-card-progress-positive">{formatPct(percentDistributed)}</span>}
      />
      <div className="summary-card summary-card-split">
        <div className="summary-card-split-half">
          <div className="summary-card-top">
            <div className="summary-card-body">
              <span className="summary-card-label">RESTANTE</span>
              <span className="summary-card-value">{formatKg(Math.abs(summary.restanteKg))}</span>
            </div>
          </div>
          <div className="summary-card-progress">
            <ProgressBar percent={percentRemaining} variant={summary.restanteKg < 0 ? "danger" : "warning"} />
            <span className="summary-card-progress-label">{formatPct(percentRemaining)}</span>
          </div>
        </div>
        <div className="summary-card-divider" />
        <div className="summary-card-split-half">
          <span className="summary-card-label">PENDÊNCIAS</span>
          <span className="summary-card-value">{summary.pendenciasCount}</span>
          <span className="summary-card-caption">grupos pendentes</span>
        </div>
      </div>
    </div>
  );
}

// Soma, por alvo direto (Coordenador Local), o que já foi efetivamente aplicado
// (filhas reais de GoalAllocation já salvas) nos grupos concluídos, mais o rascunho ao vivo (não
// salvo ainda) das caixas de edição nos grupos em andamento — acompanha a digitação em tempo
// real, sem esperar o grupo ser concluído.
function CoordinatorTotalsCards({
  directChildren,
  rows,
  liveRowsByGroup,
  allAllocations,
}: {
  directChildren: HierarchyNode[];
  rows: GroupRow[];
  liveRowsByGroup: Record<number, DistributionRow[]>;
  allAllocations: GoalAllocation[];
}) {
  const totals = useMemo(() => {
    const kgByNode = new Map<number, number>();
    for (const row of rows) {
      if (row.status === "distributed" && row.allocation) {
        for (const child of allAllocations) {
          if (child.parent_allocation !== row.allocation.id) continue;
          kgByNode.set(child.owner_node, (kgByNode.get(child.owner_node) ?? 0) + child.quantity_kg);
        }
      } else if (row.status === "pending") {
        for (const draft of liveRowsByGroup[row.groupId] ?? []) {
          if (draft.ownerNodeId === "" || draft.quantityKg === "") continue;
          kgByNode.set(draft.ownerNodeId, (kgByNode.get(draft.ownerNodeId) ?? 0) + draft.quantityKg);
        }
      }
    }
    return directChildren.map((node) => ({ node, kg: kgByNode.get(node.id) ?? 0 }));
  }, [rows, liveRowsByGroup, allAllocations, directChildren]);

  if (totals.length === 0) return null;

  return (
    <div className="summary-row summary-row-coordinators">
      {totals.map(({ node, kg }) => (
        <SummaryCard
          key={node.id}
          label={node.nome}
          value={formatKg(kg)}
          caption="Total nos grupos do ciclo"
        />
      ))}
    </div>
  );
}

// Mantém o formulário de distribuição montado mesmo com o grupo recolhido, pra que o cabeçalho
// do card continue mostrando o progresso ao vivo (kg/% ainda não salvos) sem precisar reabrir —
// só a tabela em si (GroupChildDistributionTable) fica condicionada a `expanded`.
function PendingGroupDistribution({
  allocation,
  directChildren,
  expanded,
  onProgress,
  onChanged,
}: {
  allocation: GoalAllocation;
  directChildren: HierarchyNode[];
  expanded: boolean;
  onProgress: (rows: DistributionRow[]) => void;
  onChanged: () => void;
}) {
  const bag = useDistributionRows(allocation, directChildren, onChanged);

  useEffect(() => {
    onProgress(bag.rows);
  }, [bag.rows, onProgress]);

  if (!expanded) return null;
  return <GroupChildDistributionTable allocation={allocation} directChildren={directChildren} bag={bag} />;
}

function GroupRowCard({
  row,
  cycleId,
  ownerNodeId,
  directChildren,
  allAllocations,
  distributedKg,
  onProgress,
  onChanged,
}: {
  row: GroupRow;
  cycleId: number;
  ownerNodeId: number;
  directChildren: HierarchyNode[];
  allAllocations: GoalAllocation[];
  distributedKg: number;
  onProgress: (rows: DistributionRow[]) => void;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [quantityKg, setQuantityKg] = useState<number | "">(row.suggestion?.suggested_kg ?? "");
  const [breakdownOpen, setBreakdownOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    setError(null);
    if (quantityKg === "" || !Number.isInteger(quantityKg) || quantityKg < 0) {
      setError("Informe uma quantidade inteira válida.");
      return;
    }

    setSubmitting(true);
    try {
      await api.post("/allocations/root/", {
        cycle_id: cycleId,
        owner_node_id: ownerNodeId,
        granularity: "GROUP",
        group_id: row.groupId,
        quantity_kg: quantityKg,
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao criar meta.");
    } finally {
      setSubmitting(false);
    }
  }

  const groupMetaKg = row.allocation ? row.allocation.quantity_kg : (row.suggestion?.suggested_kg ?? 0);
  const remainingKg = groupMetaKg - distributedKg;
  const percent = groupMetaKg > 0 ? (distributedKg / groupMetaKg) * 100 : 0;
  const isOver = remainingKg < 0;

  return (
    <Card className="pg-card">
      <button
        type="button"
        className="pg-header"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="pg-header-identity">
          <span className="pg-header-name-row">
            <strong>{row.groupNome}</strong>
            <StatusBadge status={row.status} />
            {row.suggestion?.has_gap && (
              <Badge variant="warning">
                <AlertTriangle size={12} /> histórico incompleto
              </Badge>
            )}
          </span>
          <span className="pg-header-meta">Meta do grupo: {formatKg(groupMetaKg)}</span>
        </span>
        <span className="pg-header-progress">
          <span className="pg-header-progress-label">
            {row.status === "suggestion" ? "—" : formatPct(percent)}
          </span>
          <ProgressBar percent={percent} variant={isOver ? "danger" : row.status === "distributed" ? "success" : "warning"} size="sm" />
        </span>
        <MetricChip className="pg-header-kg" label="Distribuído" value={formatKg(distributedKg)} />
        <MetricChip
          className="pg-header-kg"
          label="Restante"
          value={formatKg(Math.abs(remainingKg))}
          tone={isOver ? "danger" : remainingKg === 0 ? "success" : "warning"}
        />
        <span className="pg-header-toggle">{expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}</span>
      </button>

      {expanded && row.status === "suggestion" && row.suggestion && (
        <div className="pg-body">
          <div className="suggestion-form-row">
            <div>
              <label className="field-label" htmlFor={`quantity-${row.groupId}`}>
                Meta do ciclo (kg)
              </label>
              <NumericKgInput
                id={`quantity-${row.groupId}`}
                value={quantityKg}
                onChange={setQuantityKg}
              />
            </div>
            <Button onClick={handleCreate} disabled={submitting}>
              {submitting ? "Criando…" : "Criar meta"}
            </Button>
          </div>

          <button type="button" className="suggestion-toggle" onClick={() => setBreakdownOpen((v) => !v)}>
            {breakdownOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            Por que esse número?
          </button>

          {breakdownOpen && (
            <div className="suggestion-breakdown">
              <p>
                Tendência dos últimos 12 meses: <strong>{row.suggestion.trend_kg} kg</strong>
              </p>
              <p>Sazonalidade deste mês: {formatSeasonalIndex(row.suggestion.seasonal_index)}</p>
              {row.suggestion.same_month_last_year_kg !== null && (
                <p>
                  Mesmo mês do ano passado: <strong>{Math.round(row.suggestion.same_month_last_year_kg)} kg</strong>
                </p>
              )}
              {row.suggestion.has_gap && (
                <Alert variant="warning">
                  Pelo menos um mês da janela de histórico não tem dado sincronizado — a sugestão pode
                  estar distorcida.
                </Alert>
              )}
              <Sparkline history={row.suggestion.history} />
            </div>
          )}
          {error && <Alert variant="danger">{error}</Alert>}
        </div>
      )}

      {row.status === "pending" && row.allocation && (
        <div className="pg-body">
          <PendingGroupDistribution
            allocation={row.allocation}
            directChildren={directChildren}
            expanded={expanded}
            onProgress={onProgress}
            onChanged={onChanged}
          />
        </div>
      )}

      {expanded && row.status === "distributed" && row.allocation && (
        <div className="pg-body">
          <div className="pg-distributed-header">
            <Alert variant="success">Distribuição de {row.groupNome} salva com sucesso.</Alert>
            <ResetDistributionButton allocation={row.allocation} onReset={onChanged} />
          </div>
          <div className="pg-distributed-breakdown">
            {allAllocations
              .filter((a) => a.parent_allocation === row.allocation!.id)
              .sort((a, b) => b.quantity_kg - a.quantity_kg)
              .map((child) => {
                const pct = groupMetaKg > 0 ? (child.quantity_kg / groupMetaKg) * 100 : 0;
                return (
                  <div className="pg-distributed-row" key={child.id}>
                    <span className="pg-distributed-name">{child.owner_node_nome}</span>
                    <span className="pg-distributed-value">
                      {formatKg(child.quantity_kg)}
                      <span className="pg-distributed-pct">{formatPct(pct)}</span>
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      )}
    </Card>
  );
}

export function GroupCycleOverview({
  cycleId,
  ownerNodeId,
  canCreateGoals,
  childLevelLabel,
  nodes,
  myAllocations,
  allAllocations,
  statusFilter,
  onChanged,
}: Props) {
  const [suggestions, setSuggestions] = useState<GroupSuggestion[]>([]);
  const [groupNameById, setGroupNameById] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [bulkSubmitting, setBulkSubmitting] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [liveRowsByGroup, setLiveRowsByGroup] = useState<Record<number, DistributionRow[]>>({});

  function refreshSuggestions() {
    if (!canCreateGoals) return;
    setLoading(true);
    void api
      .get<GroupSuggestion[]>(`/allocations/suggestions/?cycle=${cycleId}&owner_node=${ownerNodeId}`)
      .then(setSuggestions)
      .finally(() => setLoading(false));
  }

  useEffect(refreshSuggestions, [cycleId, ownerNodeId, canCreateGoals]);

  // Quem não pode criar meta (não passa pela sugestão P1, que já traz group_nome) precisa
  // resolver o nome do grupo por conta própria — catálogo é dado público pra qualquer usuário
  // autenticado, não exige nenhum endpoint novo.
  useEffect(() => {
    if (canCreateGoals) return;
    setLoading(true);
    void api
      .get<ProductGroup[]>("/catalog/groups/")
      .then((groups) => setGroupNameById(Object.fromEntries(groups.map((g) => [g.id, g.nome]))))
      .finally(() => setLoading(false));
  }, [canCreateGoals]);

  // `nodes` inclui nós inativados (o admin precisa vê-los na tela de Hierarquia) — sem o filtro
  // de `ativo`, um Coordenador removido/substituído (Decisão 10, O4) continuava aparecendo como
  // alvo de distribuição ao lado de quem ocupa a posição agora.
  const directChildren = useMemo(
    () => nodes.filter((n) => n.parent === ownerNodeId && n.ativo),
    [nodes, ownerNodeId],
  );

  const rows = useMemo<GroupRow[]>(() => {
    const statusRank: Record<RowStatus, number> = { suggestion: 0, pending: 1, distributed: 2 };

    if (canCreateGoals) {
      return suggestions
        .map((suggestion) => {
          const allocation = myAllocations.find((a) => a.group === suggestion.group_id);
          const status: RowStatus = !allocation ? "suggestion" : allocation.distributed ? "distributed" : "pending";
          return {
            groupId: suggestion.group_id,
            groupNome: suggestion.group_nome,
            suggestion,
            allocation,
            status,
          };
        })
        .sort((a, b) => {
          if (statusRank[a.status] !== statusRank[b.status]) return statusRank[a.status] - statusRank[b.status];
          return (b.suggestion?.suggested_kg ?? 0) - (a.suggestion?.suggested_kg ?? 0);
        });
    }

    // Sem sugestão P1: cada linha vem de uma meta já recebida de quem está acima — nunca "não
    // iniciado" (não há o que criar aqui, só distribuir o que já chegou).
    return myAllocations
      .filter((a) => a.group !== null)
      .map((allocation) => ({
        groupId: allocation.group as number,
        groupNome: groupNameById[allocation.group as number] ?? `Grupo ${allocation.group}`,
        suggestion: null,
        allocation,
        status: (allocation.distributed ? "distributed" : "pending") as RowStatus,
      }))
      .sort((a, b) => {
        if (statusRank[a.status] !== statusRank[b.status]) return statusRank[a.status] - statusRank[b.status];
        return (b.allocation?.quantity_kg ?? 0) - (a.allocation?.quantity_kg ?? 0);
      });
  }, [canCreateGoals, suggestions, myAllocations, groupNameById]);

  const visibleRows = useMemo(
    () => (statusFilter === "all" ? rows : rows.filter((r) => r.status === statusFilter)),
    [rows, statusFilter],
  );

  const updateLiveRows = useCallback((groupId: number, groupRows: DistributionRow[]) => {
    setLiveRowsByGroup((prev) => (prev[groupId] === groupRows ? prev : { ...prev, [groupId]: groupRows }));
  }, []);

  const liveTotals = useMemo(() => {
    const totals: Record<number, number> = {};
    for (const [groupId, groupRows] of Object.entries(liveRowsByGroup)) {
      totals[Number(groupId)] = groupRows.reduce(
        (sum, row) => sum + (typeof row.quantityKg === "number" ? row.quantityKg : 0),
        0,
      );
    }
    return totals;
  }, [liveRowsByGroup]);

  const summary = useMemo(() => {
    let metaTotalKg = 0;
    let distribuidoKg = 0;
    for (const row of rows) {
      const groupMetaKg = row.allocation ? row.allocation.quantity_kg : (row.suggestion?.suggested_kg ?? 0);
      metaTotalKg += groupMetaKg;
      if (row.status === "distributed") distribuidoKg += row.allocation?.quantity_kg ?? 0;
      else if (row.status === "pending") distribuidoKg += liveTotals[row.groupId] ?? 0;
    }
    const restanteKg = metaTotalKg - distribuidoKg;
    const pendenciasCount = rows.filter((r) => r.status !== "distributed").length;
    return { metaTotalKg, distribuidoKg, restanteKg, pendenciasCount };
  }, [rows, liveTotals]);

  async function acceptAllSuggestions() {
    setBulkError(null);
    const targets = rows.filter((r) => r.status === "suggestion");
    if (targets.length === 0) return;

    setBulkSubmitting(true);
    const failures: string[] = [];
    for (const row of targets) {
      try {
        await api.post("/allocations/root/", {
          cycle_id: cycleId,
          owner_node_id: ownerNodeId,
          granularity: "GROUP",
          group_id: row.groupId,
          quantity_kg: row.suggestion?.suggested_kg ?? 0,
        });
      } catch {
        failures.push(row.groupNome);
      }
    }
    setBulkSubmitting(false);
    if (failures.length > 0) {
      setBulkError(`Não foi possível criar a meta de: ${failures.join(", ")}.`);
    }
    refreshSuggestions();
    onChanged();
  }

  if (loading && rows.length === 0) {
    return (
      <div>
        <div className="summary-row">
          <Skeleton className="skeleton-summary-card" />
          <Skeleton className="skeleton-summary-card" />
          <Skeleton className="skeleton-summary-card" />
        </div>
        <Skeleton className="skeleton-group-card" />
        <Skeleton className="skeleton-group-card" />
      </div>
    );
  }
  if (rows.length === 0) {
    return <EmptyState>Nenhum grupo ativo encontrado.</EmptyState>;
  }

  const pendingSuggestionsCount = rows.filter((r) => r.status === "suggestion").length;

  return (
    <div>
      <CycleSummaryCards summary={summary} />

      <div className="group-overview-actions">
        {pendingSuggestionsCount > 1 && (
          <Button variant="outline" size="sm" onClick={acceptAllSuggestions} disabled={bulkSubmitting}>
            {bulkSubmitting ? "Criando metas…" : `Aceitar ${pendingSuggestionsCount} sugestões automáticas`}
          </Button>
        )}
      </div>
      {bulkError && <Alert variant="danger">{bulkError}</Alert>}

      {visibleRows.length === 0 ? (
        <EmptyState icon={<Filter size={28} strokeWidth={1.5} />}>
          Nenhum grupo corresponde ao filtro selecionado.
        </EmptyState>
      ) : (
        <div className="suggestion-panel">
          {visibleRows.map((row) => (
            <GroupRowCard
              key={row.groupId}
              row={row}
              cycleId={cycleId}
              ownerNodeId={ownerNodeId}
              directChildren={directChildren}
              allAllocations={allAllocations}
              distributedKg={
                row.status === "distributed"
                  ? (row.allocation?.quantity_kg ?? 0)
                  : row.status === "pending"
                    ? (liveTotals[row.groupId] ?? 0)
                    : 0
              }
              onProgress={(rows) => updateLiveRows(row.groupId, rows)}
              onChanged={() => {
                refreshSuggestions();
                onChanged();
              }}
            />
          ))}
        </div>
      )}

      <h3 className="pg-recap-title">Total por {childLevelLabel}</h3>
      <CoordinatorTotalsCards
        directChildren={directChildren}
        rows={rows}
        liveRowsByGroup={liveRowsByGroup}
        allAllocations={allAllocations}
      />
    </div>
  );
}
