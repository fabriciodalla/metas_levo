import { useMemo } from "react";
import { CalendarDays, Users } from "lucide-react";
import { CycleSelect } from "../admin/CycleSelect";
import { useAuth } from "../../auth/AuthContext";
import { useCycleAllocations } from "./useCycleAllocations";
import { StatRow, StatTile } from "../../components/ui/StatTile";
import { Spinner } from "../../components/ui/Spinner";
import { EmptyState } from "../../components/ui/EmptyState";
import { Card } from "../../components/ui/Card";
import { AllocationStatusTable, type AllocationStatusItem } from "../../components/AllocationStatusTable";

const kgFormatter = new Intl.NumberFormat("pt-BR", {
  maximumFractionDigits: 0,
});

export function NivelOverviewPage() {
  const { user } = useAuth();
  const { cycles, selectedCycleId, setSelectedCycleId, allocations, loading } = useCycleAllocations();

  const myNodeIds = useMemo(() => new Set(user?.hierarchy_nodes.map((n) => n.id) ?? []), [user]);

  // Quando o próprio nó quebra uma alocação GROUP em SUBGROUP (tela "Distribuir Produtos"), o
  // dono continua o mesmo nos dois — sem excluir a linha-pai, ela e as linhas-filhas somariam o
  // mesmo kg duas vezes em "Sua meta neste ciclo".
  const selfSplitParentIds = useMemo(() => {
    const byId = new Map(allocations.map((a) => [a.id, a]));
    const ids = new Set<number>();
    for (const a of allocations) {
      if (a.parent_allocation == null || !myNodeIds.has(a.owner_node)) continue;
      const parent = byId.get(a.parent_allocation);
      if (parent && myNodeIds.has(parent.owner_node)) {
        ids.add(parent.id);
      }
    }
    return ids;
  }, [allocations, myNodeIds]);

  const totalKg = allocations
    .filter((a) => myNodeIds.has(a.owner_node) && !selfSplitParentIds.has(a.id))
    .reduce((sum, a) => sum + a.quantity_kg, 0);
  const kgNoVendedor = allocations
    .filter((a) => a.owner_node_level === "VENDEDOR")
    .reduce((sum, a) => sum + a.quantity_kg, 0);
  const percentualNaPonta = totalKg > 0 ? Math.round((kgNoVendedor / totalKg) * 100) : 0;

  const subordinados: AllocationStatusItem[] = allocations
    .filter((a) => a.quantity_kg > 0 && a.owner_node_level !== "VENDEDOR" && !myNodeIds.has(a.owner_node))
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

  const subordinadosPendentesCount = subordinados.filter((a) => !a.distributed).length;

  return (
    <section className="nivel-overview-page">
      <div className="nivel-overview-toolbar">
        <span className="nivel-overview-toolbar-icon" aria-hidden="true">
          <CalendarDays size={20} />
        </span>
        <CycleSelect cycles={cycles} value={selectedCycleId} onChange={setSelectedCycleId} />
      </div>

      {loading && <Spinner />}
      {!loading && allocations.length === 0 && (
        <EmptyState>Nenhuma alocação no seu ramo neste ciclo.</EmptyState>
      )}
      {!loading && allocations.length > 0 && (
        <>
          <StatRow>
            <StatTile
              value={`${kgFormatter.format(totalKg)} kg`}
              label="Sua meta neste ciclo"
            />
            <StatTile
              value={`${percentualNaPonta}%`}
              label="Já chegou ao Vendedor"
            />
            <StatTile
              value={subordinadosPendentesCount}
              label="Subordinados pendentes"
            />
          </StatRow>

          <Card
            className="nivel-overview-distribution-card"
            title={
              <span className="nivel-overview-card-title">
                <Users size={20} aria-hidden="true" />
                Distribuição dos seus subordinados
              </span>
            }
            subtitle="Acompanhe o recebimento e a distribuição das metas da sua equipe."
          >
            {subordinados.length === 0 ? (
              <EmptyState>Nenhum subordinado com meta disponível neste ciclo.</EmptyState>
            ) : (
              <AllocationStatusTable items={subordinados} />
            )}
          </Card>
        </>
      )}
    </section>
  );
}
