import { ChevronDown, ChevronUp } from "lucide-react";
import { Fragment, useState } from "react";
import { Badge } from "./ui/Badge";
import { EmptyState } from "./ui/EmptyState";

export interface AllocationStatusItem {
  id: number;
  ownerNodeId: number;
  ownerNodeUsernames: string[];
  ownerNodeLevel: string;
  parentAllocationId: number | null;
  groupNome: string | null;
  subgroupNome: string | null;
  quantityKg: number;
  distributed: boolean;
  receivedAt: string;
  distributedAt: string;
}

interface GroupRow {
  groupNome: string;
  totalKg: number;
  receivedAt: string;
  distributedAt: string | null;
  distributedCount: number;
  totalCount: number;
}

interface OwnerRow {
  ownerNodeId: number;
  ownerNodeUsernames: string[];
  ownerNodeLevel: string;
  totalKg: number;
  distributedCount: number;
  totalCount: number;
  receivedAt: string;
  distributedAt: string | null;
  groupRows: GroupRow[];
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("pt-BR");
}

function formatKg(value: number): string {
  return `${value.toLocaleString("pt-BR")} kg`;
}

function earliest(items: AllocationStatusItem[]): string {
  return items.reduce((min, item) => (item.receivedAt < min ? item.receivedAt : min), items[0].receivedAt);
}

function latestDistributedAt(distributedItems: AllocationStatusItem[]): string {
  return distributedItems.reduce(
    (max, item) => (item.distributedAt > max ? item.distributedAt : max),
    distributedItems[0].distributedAt,
  );
}

// Quando um nó quebra a própria meta em subgrupos (Coordenador Local: grupo -> subgrupos, sem
// trocar de dono), a alocação de grupo original vira só um passo intermediário — o que importa
// mostrar daqui pra frente é o item resultante (subgrupo), não os dois somados. Alocação recebida
// de outro nó (troca de dono de verdade) nunca é excluída — é ali que mora o "distribuiu ou não".
function excludeSupersededBySameOwnerSplit(items: AllocationStatusItem[]): AllocationStatusItem[] {
  const byId = new Map(items.map((item) => [item.id, item]));
  const supersededIds = new Set(
    items
      .filter((child) => child.parentAllocationId !== null)
      .filter((child) => byId.get(child.parentAllocationId as number)?.ownerNodeId === child.ownerNodeId)
      .map((child) => child.parentAllocationId as number),
  );
  return items.filter((item) => !supersededIds.has(item.id));
}

function aggregateByGroup(items: AllocationStatusItem[]): GroupRow[] {
  const byGroup = new Map<string, AllocationStatusItem[]>();
  for (const item of items) {
    const key = item.groupNome ?? "Sem grupo";
    if (!byGroup.has(key)) byGroup.set(key, []);
    byGroup.get(key)!.push(item);
  }
  return [...byGroup.entries()]
    .map(([groupNome, groupItems]) => {
      const distributedItems = groupItems.filter((item) => item.distributed);
      return {
        groupNome,
        totalKg: groupItems.reduce((sum, item) => sum + item.quantityKg, 0),
        receivedAt: earliest(groupItems),
        distributedAt: distributedItems.length === groupItems.length ? latestDistributedAt(distributedItems) : null,
        distributedCount: distributedItems.length,
        totalCount: groupItems.length,
      };
    })
    .sort((a, b) => b.totalKg - a.totalKg);
}

function groupByOwner(items: AllocationStatusItem[]): OwnerRow[] {
  const cleaned = excludeSupersededBySameOwnerSplit(items);
  const byOwner = new Map<number, { usernames: string[]; level: string; items: AllocationStatusItem[] }>();
  for (const item of cleaned) {
    let owner = byOwner.get(item.ownerNodeId);
    if (!owner) {
      owner = { usernames: item.ownerNodeUsernames, level: item.ownerNodeLevel, items: [] };
      byOwner.set(item.ownerNodeId, owner);
    }
    owner.items.push(item);
  }
  return [...byOwner.entries()]
    .map(([ownerNodeId, owner]) => {
      const distributedItems = owner.items.filter((item) => item.distributed);
      return {
        ownerNodeId,
        ownerNodeUsernames: owner.usernames,
        ownerNodeLevel: owner.level,
        totalKg: owner.items.reduce((sum, item) => sum + item.quantityKg, 0),
        distributedCount: distributedItems.length,
        totalCount: owner.items.length,
        receivedAt: earliest(owner.items),
        distributedAt: distributedItems.length === owner.items.length ? latestDistributedAt(distributedItems) : null,
        groupRows: aggregateByGroup(owner.items),
      };
    })
    .sort((a, b) => {
      const aPending = a.distributedCount < a.totalCount;
      const bPending = b.distributedCount < b.totalCount;
      if (aPending !== bPending) return aPending ? -1 : 1;
      return b.totalKg - a.totalKg;
    });
}

function StatusBadge({ distributedCount, totalCount }: { distributedCount: number; totalCount: number }) {
  if (distributedCount === totalCount) return <Badge variant="success">Distribuído</Badge>;
  if (distributedCount === 0) return <Badge variant="danger">Pendente</Badge>;
  return <Badge variant="warning">{`${distributedCount}/${totalCount} distribuídos`}</Badge>;
}

type LevelFilter = "ALL" | "REGIONAL" | "LOCAL" | "SUPERVISOR";
type StatusFilter = "ALL" | "DISTRIBUTED" | "PARTIAL" | "PENDING";

const LEVEL_FILTER_LABELS: Record<LevelFilter, string> = {
  ALL: "Todos os níveis",
  REGIONAL: "Coordenador Regional",
  LOCAL: "Coordenador Local",
  SUPERVISOR: "Supervisor",
};

const STATUS_FILTER_LABELS: Record<StatusFilter, string> = {
  ALL: "Todos os status",
  DISTRIBUTED: "Distribuído",
  PARTIAL: "Parcial",
  PENDING: "Pendente",
};

function ownerStatus(owner: OwnerRow): StatusFilter {
  if (owner.distributedCount === owner.totalCount) return "DISTRIBUTED";
  if (owner.distributedCount === 0) return "PENDING";
  return "PARTIAL";
}

// Uma linha por usuário/nó, verde/vermelho (ou parcial) conforme já distribuiu ou não pros
// subordinados dele, com a data em que recebeu a meta e a data em que efetuou a distribuição —
// mesmo mecanismo de agrupamento por dono/grupo do PendingAllocationsTable, mas cobrindo todo
// mundo que tem meta disponível no ciclo, não só quem está pendente.
export function AllocationStatusTable({ items }: { items: AllocationStatusItem[] }) {
  const owners = groupByOwner(items);
  const [openOwners, setOpenOwners] = useState<Set<number>>(new Set());
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("ALL");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("ALL");

  const filteredOwners = owners.filter((owner) => {
    if (levelFilter !== "ALL" && owner.ownerNodeLevel !== levelFilter) return false;
    if (statusFilter !== "ALL" && ownerStatus(owner) !== statusFilter) return false;
    return true;
  });

  function toggle(ownerNodeId: number) {
    setOpenOwners((prev) => {
      const next = new Set(prev);
      if (next.has(ownerNodeId)) next.delete(ownerNodeId);
      else next.add(ownerNodeId);
      return next;
    });
  }

  return (
    <div>
      <div className="filter-bar allocation-status-filters">
        <div className="field-inline">
          <label className="field-label" htmlFor="allocation-status-level-filter">
            Nível
          </label>
          <select
            id="allocation-status-level-filter"
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value as LevelFilter)}
          >
            {(Object.keys(LEVEL_FILTER_LABELS) as LevelFilter[]).map((key) => (
              <option key={key} value={key}>
                {LEVEL_FILTER_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
        <div className="field-inline">
          <label className="field-label" htmlFor="allocation-status-status-filter">
            Status
          </label>
          <select
            id="allocation-status-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          >
            {(Object.keys(STATUS_FILTER_LABELS) as StatusFilter[]).map((key) => (
              <option key={key} value={key}>
                {STATUS_FILTER_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {filteredOwners.length === 0 ? (
        <EmptyState>Nenhum resultado para os filtros selecionados.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table pending-table">
            <thead>
              <tr>
                <th aria-hidden="true" />
                <th>Usuário(s)</th>
                <th>Nível</th>
                <th>Status</th>
                <th>Meta total</th>
                <th>Recebido em</th>
                <th>Distribuído em</th>
              </tr>
            </thead>
            <tbody>
              {filteredOwners.map((owner) => {
                const isOpen = openOwners.has(owner.ownerNodeId);
                return (
                  <Fragment key={owner.ownerNodeId}>
                    <tr className="pending-row-summary" onClick={() => toggle(owner.ownerNodeId)}>
                      <td className="pending-row-toggle">
                        {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                      </td>
                      <td>
                        {owner.ownerNodeUsernames.length > 0 ? (
                          owner.ownerNodeUsernames.join(", ")
                        ) : (
                          <Badge variant="neutral">sem usuário vinculado</Badge>
                        )}
                      </td>
                      <td>{owner.ownerNodeLevel}</td>
                      <td>
                        <StatusBadge distributedCount={owner.distributedCount} totalCount={owner.totalCount} />
                      </td>
                      <td>{formatKg(owner.totalKg)}</td>
                      <td>{formatDate(owner.receivedAt)}</td>
                      <td>{owner.distributedAt ? formatDate(owner.distributedAt) : "—"}</td>
                    </tr>
                    {isOpen && (
                      <tr className="pending-row-detail">
                        <td />
                        <td colSpan={6}>
                          <table className="pending-detail-table">
                            <thead>
                              <tr>
                                <th>Grupo</th>
                                <th>Status</th>
                                <th>Meta (kg)</th>
                                <th>Recebido em</th>
                                <th>Distribuído em</th>
                              </tr>
                            </thead>
                            <tbody>
                              {owner.groupRows.map((row) => (
                                <tr key={row.groupNome}>
                                  <td>
                                    {row.groupNome}
                                    {row.totalCount > 1 && (
                                      <Badge variant="neutral"> {row.totalCount} subgrupos</Badge>
                                    )}
                                  </td>
                                  <td>
                                    <StatusBadge
                                      distributedCount={row.distributedCount}
                                      totalCount={row.totalCount}
                                    />
                                  </td>
                                  <td>{formatKg(row.totalKg)}</td>
                                  <td>{formatDate(row.receivedAt)}</td>
                                  <td>{row.distributedAt ? formatDate(row.distributedAt) : "—"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
