import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { AccumulatedSalesResult, Cycle, HierarchyNode } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";

export function useAcumuladoVendas() {
  const { user } = useAuth();

  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [selectedCycleId, setSelectedCycleId] = useState<number | null>(null);
  const [nodes, setNodes] = useState<HierarchyNode[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [result, setResult] = useState<AccumulatedSalesResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.get<Cycle[]>("/cycles/").then((data) => {
      setCycles(data);
      const open = data.find((cycle) => cycle.status === "ABERTO");
      setSelectedCycleId((current) => current ?? open?.id ?? data[0]?.id ?? null);
    });
    void api.get<HierarchyNode[]>("/hierarchy/nodes/").then(setNodes);
  }, []);

  // Ponto de partida do drill-down: a posição mais sênior do próprio usuário (ver
  // HierarchyNodeQuerySet.by_seniority no backend — aqui só pegamos a primeira, que já vem nessa
  // ordem em `/auth/me/`).
  useEffect(() => {
    if (selectedNodeId === null && user && user.hierarchy_nodes.length > 0) {
      setSelectedNodeId(user.hierarchy_nodes[0].id);
    }
  }, [user, selectedNodeId]);

  useEffect(() => {
    if (selectedCycleId === null || selectedNodeId === null) return;
    setLoading(true);
    setError(null);
    void api
      .get<AccumulatedSalesResult>(
        `/allocations/results/acumulado-vendas/?cycle=${selectedCycleId}&node=${selectedNodeId}`,
      )
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof ApiError ? err.message : "Não foi possível carregar o acumulado de vendas.");
      })
      .finally(() => setLoading(false));
  }, [selectedCycleId, selectedNodeId]);

  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const currentNode = selectedNodeId !== null ? (nodesById.get(selectedNodeId) ?? null) : null;
  const parentNode = currentNode?.parent != null ? (nodesById.get(currentNode.parent) ?? null) : null;

  return {
    cycles,
    selectedCycleId,
    setSelectedCycleId,
    selectedNodeId,
    setSelectedNodeId,
    parentNode,
    result,
    loading,
    error,
  };
}
