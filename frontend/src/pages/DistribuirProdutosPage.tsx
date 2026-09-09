import { Boxes, Calendar } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ProductGroup } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ResetDistributionButton } from "../components/ResetDistributionButton";
import { SubgroupSplitForm } from "../components/SubgroupSplitForm";
import { Badge } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { Spinner } from "../components/ui/Spinner";
import { useCycleAllocationsData } from "./useCycleAllocationsData";

// Tela do Coordenador Local (o que importa é ter um nó LOCAL, não quem está logado): quebra
// cada meta GROUP recebida em metas por subgrupo. Depois de salva aqui, a distribuição por
// Supervisor de cada subgrupo acontece na tela "Meta Supervisor".
export function DistribuirProdutosPage() {
  const { user } = useAuth();
  const { cycles, selectedCycleId, setSelectedCycleId, allocations, loading, refresh } = useCycleAllocationsData();
  const [groupNameById, setGroupNameById] = useState<Record<number, string>>({});
  const [selectedAllocationId, setSelectedAllocationId] = useState<number | null>(null);

  // Catálogo é dado público pra qualquer usuário autenticado — resolve o nome do grupo sem
  // precisar de um endpoint novo (mesmo padrão usado em GroupCycleOverview).
  useEffect(() => {
    void api
      .get<ProductGroup[]>("/catalog/groups/")
      .then((groups) => setGroupNameById(Object.fromEntries(groups.map((g) => [g.id, g.nome]))));
  }, []);

  const myLocalNodeIds = useMemo(
    () => new Set(user?.hierarchy_nodes.filter((n) => n.level === "LOCAL").map((n) => n.id) ?? []),
    [user],
  );

  const pending = useMemo(
    () =>
      allocations
        .filter((a) => myLocalNodeIds.has(a.owner_node) && a.granularity === "GROUP" && !a.distributed)
        .sort((a, b) => groupNome(a.group).localeCompare(groupNome(b.group), "pt-BR", { sensitivity: "base" })),
    [allocations, myLocalNodeIds, groupNameById],
  );

  // Grupos já divididos por subgrupo — somem da lista de pendências acima, mas precisam continuar
  // visíveis em algum lugar pra dar acesso ao "Resetar distribuição" (corrigir um erro de divisão
  // sem precisar ir em outra tela).
  const done = useMemo(
    () =>
      allocations
        .filter((a) => myLocalNodeIds.has(a.owner_node) && a.granularity === "GROUP" && a.distributed)
        .sort((a, b) => groupNome(a.group).localeCompare(groupNome(b.group), "pt-BR", { sensitivity: "base" })),
    [allocations, myLocalNodeIds, groupNameById],
  );

  // Mantém a seleção atual entre refreshes (ex.: após salvar outro grupo); só cai pro primeiro
  // pendente quando a seleção some da lista (grupo concluído, troca de ciclo, primeiro carregamento).
  useEffect(() => {
    setSelectedAllocationId((current) =>
      current !== null && pending.some((a) => a.id === current) ? current : (pending[0]?.id ?? null),
    );
  }, [pending]);

  function groupNome(groupId: number | null): string {
    if (groupId === null) return "Grupo";
    return groupNameById[groupId] ?? `Grupo ${groupId}`;
  }

  function handleSplit() {
    refresh();
  }

  if (myLocalNodeIds.size === 0) {
    return <EmptyState>Esta tela é só para quem tem uma posição de Coordenador Local.</EmptyState>;
  }

  const selectedAllocation = pending.find((a) => a.id === selectedAllocationId) ?? null;

  return (
    <section className="dp-produtos-shell">
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

        {pending.length > 1 && (
          <div className="dp-filter-field">
            <label className="field-label" htmlFor="grupo-select">
              Grupo
            </label>
            <div className="dp-filter-input">
              <Boxes size={16} />
              <select
                id="grupo-select"
                value={selectedAllocation?.id ?? ""}
                onChange={(e) => setSelectedAllocationId(Number(e.target.value))}
              >
                {pending.map((a) => (
                  <option key={a.id} value={a.id}>
                    {groupNome(a.group)}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      {loading && <Spinner />}

      {!loading && pending.length === 0 && <EmptyState>Nada pendente para este ciclo.</EmptyState>}

      {selectedAllocation && (
        <SubgroupSplitForm key={selectedAllocation.id} allocation={selectedAllocation} onSplit={handleSplit} />
      )}

      {done.length > 0 && (
        <>
          <h2>Já dividido ({done.length})</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Grupo</th>
                  <th>Quantidade</th>
                  <th>Status</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {done.map((allocation) => (
                  <tr key={allocation.id}>
                    <td>{groupNome(allocation.group)}</td>
                    <td>{allocation.quantity_kg} kg</td>
                    <td>
                      <Badge variant="success">Dividido</Badge>
                    </td>
                    <td>
                      <ResetDistributionButton allocation={allocation} onReset={refresh} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
