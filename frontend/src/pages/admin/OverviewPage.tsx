import { CalendarDays, ChevronDown, ChevronRight, Network, User, Users } from "lucide-react";
import { useState } from "react";
import type { AllocationOverview } from "../../api/types";
import { CycleSelect } from "./CycleSelect";
import { LEVEL_LABELS, type Level } from "./constants";
import { useCycleOverview } from "./useCycleOverview";
import { StatRow, StatTile } from "../../components/ui/StatTile";
import { Spinner } from "../../components/ui/Spinner";
import { EmptyState } from "../../components/ui/EmptyState";
import { Card } from "../../components/ui/Card";
import { AllocationStatusTable, type AllocationStatusItem } from "../../components/AllocationStatusTable";

interface AllocationTreeNodeData {
  nodeId: number;
  nome: string;
  level: string;
  parentId: number | null;
  allocations: AllocationOverview[];
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("pt-BR");
}

function formatKg(value: number): string {
  return `${value.toLocaleString("pt-BR", { maximumFractionDigits: 0 })} kg`;
}

interface AllocationGroupBucket {
  key: string;
  groupNome: string | null;
  rows: AllocationOverview[];
}

// Um Coordenador Local que já quebrou uma meta de grupo em subgrupos
// (SplitGroupIntoSubgroupsService) acumula, sob o mesmo nó, a alocação de grupo original (já
// superada) mais uma por subgrupo — todas resolvendo pro mesmo nome de grupo. Sem agrupar aqui, o
// nome do grupo aparece repetido uma vez por subgrupo na lista.
function groupAllocationsByGroup(allocations: AllocationOverview[]): AllocationGroupBucket[] {
  const buckets = new Map<string, AllocationGroupBucket>();
  for (const a of allocations) {
    const key = a.group_nome ?? `sem-grupo-${a.id}`;
    if (!buckets.has(key)) {
      buckets.set(key, { key, groupNome: a.group_nome, rows: [] });
    }
    buckets.get(key)!.rows.push(a);
  }
  return [...buckets.values()];
}

function AllocationTree({
  node,
  childrenByParent,
  depth,
  expanded,
  onToggle,
}: {
  node: AllocationTreeNodeData;
  childrenByParent: Map<number, AllocationTreeNodeData[]>;
  depth: number;
  expanded: Set<number>;
  onToggle: (id: number) => void;
}) {
  // Só desce até Coordenador Local — Supervisor/Vendedor ficam fora do drill-down desta visão.
  const children = node.level === "LOCAL" ? [] : (childrenByParent.get(node.nodeId) ?? []);
  const hasChildren = children.length > 0;
  const isOpen = expanded.has(node.nodeId);

  return (
    <li>
      <div className="tree-node-row" style={{ paddingLeft: depth * 20 }}>
        <button
          type="button"
          className="tree-toggle"
          onClick={() => onToggle(node.nodeId)}
          disabled={!hasChildren}
          aria-label={isOpen ? `Recolher ${node.nome}` : `Expandir ${node.nome}`}
        >
          {hasChildren && (isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
        </button>
        <span className="tree-node-avatar">
          <User size={14} />
        </span>
        <span className="tree-node-name">{node.nome}</span>
        <span className="tree-node-level">{LEVEL_LABELS[node.level as Level]}</span>
      </div>
      <div className="tree-node-allocations" style={{ paddingLeft: depth * 20 + 40 }}>
        {groupAllocationsByGroup(node.allocations).map((bucket) => {
          // Quando o grupo já foi quebrado em subgrupos (SplitGroupIntoSubgroupsService), a
          // alocação de grupo original só marca "já quebrado" — o valor real distribuído pra
          // frente vem de cada subgrupo, que fecha exatamente com ela (ClosureValidator).
          const groupRow = bucket.rows.find((r) => r.granularity === "GROUP") ?? null;
          const subgroupRows = bucket.rows.filter((r) => r.granularity !== "GROUP");
          const atribuido = groupRow
            ? groupRow.quantity_kg
            : subgroupRows.reduce((sum, r) => sum + r.quantity_kg, 0);
          const distribuidas = subgroupRows.filter((r) => r.distributed);
          const distribuido =
            subgroupRows.length > 0
              ? distribuidas.reduce((sum, r) => sum + r.quantity_kg, 0)
              : groupRow?.distributed
                ? groupRow.quantity_kg
                : 0;
          const lastDistributedAt = (subgroupRows.length > 0 ? distribuidas : groupRow?.distributed ? [groupRow] : [])
            .map((r) => r.updated_at)
            .reduce<string | null>((latest, updatedAt) => (!latest || updatedAt > latest ? updatedAt : latest), null);

          return (
            <div key={bucket.key} className="tree-allocation-row">
              <span>{bucket.groupNome ?? "—"}</span>
              <span>{formatKg(atribuido)} atribuídos</span>
              <span>{formatKg(distribuido)} distribuídos</span>
              <span>{lastDistributedAt ? `aplicado em ${formatDate(lastDistributedAt)}` : "—"}</span>
            </div>
          );
        })}
      </div>
      {isOpen && hasChildren && (
        <ul className="tree-children">
          {children.map((child) => (
            <AllocationTree
              key={child.nodeId}
              node={child}
              childrenByParent={childrenByParent}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function OverviewPage() {
  const { cycles, selectedCycleId, setSelectedCycleId, overview, loading } = useCycleOverview();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggle(nodeId: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  }

  const totalKg = overview
    .filter((a) => a.parent_allocation === null)
    .reduce((sum, a) => sum + a.quantity_kg, 0);
  const distribuidoDoGerente = overview
    .filter((a) => a.parent_allocation === null && a.distributed)
    .reduce((sum, a) => sum + a.quantity_kg, 0);
  const kgNoVendedor = overview
    .filter((a) => a.owner_node_level === "VENDEDOR")
    .reduce((sum, a) => sum + a.quantity_kg, 0);
  const percentualNaPonta = totalKg > 0 ? Math.round((kgNoVendedor / totalKg) * 100) : 0;

  const statusItems: AllocationStatusItem[] = overview
    .filter((a) => a.quantity_kg > 0 && a.owner_node_level !== "VENDEDOR")
    .map((a) => ({
      id: a.id,
      ownerNodeId: a.owner_node,
      ownerNodeUsernames: a.owner_node_usernames,
      ownerNodeLevel: a.owner_node_level,
      parentAllocationId: a.parent_allocation,
      groupNome: a.group_nome,
      subgroupNome: a.subgroup_nome,
      quantityKg: a.quantity_kg,
      distributed: a.distributed,
      receivedAt: a.created_at,
      distributedAt: a.updated_at,
    }));
  const pendentesCount = statusItems.filter((a) => !a.distributed).length;

  const nodesById = new Map<number, AllocationTreeNodeData>();
  for (const a of overview) {
    if (!nodesById.has(a.owner_node)) {
      nodesById.set(a.owner_node, {
        nodeId: a.owner_node,
        nome: a.owner_node_nome,
        level: a.owner_node_level,
        parentId: a.owner_node_parent_id,
        allocations: [],
      });
    }
    nodesById.get(a.owner_node)!.allocations.push(a);
  }
  const childrenByParent = new Map<number, AllocationTreeNodeData[]>();
  for (const node of nodesById.values()) {
    if (node.parentId === null) continue;
    if (!childrenByParent.has(node.parentId)) childrenByParent.set(node.parentId, []);
    childrenByParent.get(node.parentId)!.push(node);
  }
  const rootNodes = [...nodesById.values()].filter((n) => n.level === "GERENTE");

  return (
    <section className="nivel-overview-page admin-overview-page">
      <div className="nivel-overview-toolbar">
        <span className="nivel-overview-toolbar-icon" aria-hidden="true">
          <CalendarDays size={20} />
        </span>
        <CycleSelect cycles={cycles} value={selectedCycleId} onChange={setSelectedCycleId} />
      </div>

      {loading && <Spinner />}
      {!loading && overview.length === 0 && <EmptyState>Nenhuma alocação neste ciclo ainda.</EmptyState>}
      {!loading && overview.length > 0 && (
        <>
          <StatRow>
            <StatTile value={formatKg(totalKg)} label="Meta total atribuída pelo Gerente" />
            <StatTile value={formatKg(distribuidoDoGerente)} label="Já distribuído pelo Gerente" />
            <StatTile value={`${percentualNaPonta}%`} label="Já chegou ao Vendedor" />
            <StatTile value={pendentesCount} label="Pendências de distribuição" />
          </StatRow>

          <Card
            className="nivel-overview-distribution-card"
            title={
              <span className="nivel-overview-card-title">
                <Network size={20} aria-hidden="true" />
                Metas por nível (até Coordenador Local)
              </span>
            }
            subtitle="Expanda a estrutura para acompanhar as metas atribuídas em cada nível."
          >
            <div className="table-wrap tree-scroll">
              <ul className="tree-root">
                {rootNodes.map((node) => (
                  <AllocationTree
                    key={node.nodeId}
                    node={node}
                    childrenByParent={childrenByParent}
                    depth={0}
                    expanded={expanded}
                    onToggle={toggle}
                  />
                ))}
              </ul>
            </div>
          </Card>

          <Card
            className="nivel-overview-distribution-card"
            title={
              <span className="nivel-overview-card-title">
                <Users size={20} aria-hidden="true" />
                Status de distribuição por usuário
              </span>
            }
            subtitle="Acompanhe quem já concluiu a distribuição e quem ainda possui pendências."
          >
            {statusItems.length === 0 ? (
              <EmptyState>Nenhuma meta disponível neste ciclo.</EmptyState>
            ) : (
              <AllocationStatusTable items={statusItems} />
            )}
          </Card>
        </>
      )}
    </section>
  );
}
