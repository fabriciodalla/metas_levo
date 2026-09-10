import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Cycle, GoalAllocation, HierarchyNode } from "../api/types";

// Carregamento compartilhado pelas telas de distribuição (Distribuir Metas, Distribuir Produtos,
// Meta Supervisor): ciclos + ciclo selecionado (o aberto, por padrão), nós da hierarquia, e
// alocações do ciclo selecionado, com um `refresh()` pra recarregar após uma ação.
export function useCycleAllocationsData() {
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [selectedCycleId, setSelectedCycleId] = useState<number | null>(null);
  const [allocations, setAllocations] = useState<GoalAllocation[]>([]);
  const [nodes, setNodes] = useState<HierarchyNode[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void api.get<Cycle[]>("/cycles/").then((data) => {
      setCycles(data);
      const open = data.find((cycle) => cycle.status === "ABERTO");
      setSelectedCycleId(open?.id ?? data[0]?.id ?? null);
    });
    void api.get<HierarchyNode[]>("/hierarchy/nodes/").then(setNodes);
  }, []);

  useEffect(() => {
    if (selectedCycleId === null) return;
    setLoading(true);
    void api
      .get<GoalAllocation[]>(`/allocations/?cycle=${selectedCycleId}`)
      .then(setAllocations)
      .finally(() => setLoading(false));
  }, [selectedCycleId]);

  function refresh() {
    // Recarrega `nodes` junto com `allocations` — não só o que a ação acabou de mudar. Bug real
    // (2026-08-07): um Supervisor novo (ex.: adicionado em "Gestão → Usuários", outra aba/rota)
    // não aparecia como alvo de distribuição nesta tela mesmo depois da pessoa "sincronizar"
    // (chamar `refresh()` após distribuir outro subgrupo) — a árvore ficava presa no snapshot do
    // primeiro carregamento da tela, e só um reload completo da página trazia o nó novo.
    void api.get<HierarchyNode[]>("/hierarchy/nodes/").then(setNodes);
    if (selectedCycleId === null) return;
    void api.get<GoalAllocation[]>(`/allocations/?cycle=${selectedCycleId}`).then(setAllocations);
  }

  return { cycles, selectedCycleId, setSelectedCycleId, allocations, nodes, loading, refresh };
}
