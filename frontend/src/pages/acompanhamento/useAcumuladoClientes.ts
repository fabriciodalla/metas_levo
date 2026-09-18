import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ClientAccumuladoResult, HierarchyNode } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import type { Period } from "./MonthSelect";

// Últimos `count` meses (o corrente incluso, mais recente primeiro) — janela independente de
// `Cycle` (ciclo de metas): esta tela não compara com meta, então cobre qualquer mês com dado de
// venda sincronizado, mesma janela usada pelo sync (`sync_sales_history --months=12`).
function lastMonths(count: number, today: Date = new Date()): Period[] {
  const months: Period[] = [];
  let ano = today.getFullYear();
  let mes = today.getMonth() + 1;
  for (let i = 0; i < count; i++) {
    months.push({ ano, mes });
    mes -= 1;
    if (mes < 1) {
      mes = 12;
      ano -= 1;
    }
  }
  return months;
}

export function useAcumuladoClientes() {
  const { user } = useAuth();

  const months = useMemo(() => lastMonths(13), []);
  const [selectedPeriod, setSelectedPeriod] = useState<Period>(months[0]);
  const [nodes, setNodes] = useState<HierarchyNode[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [result, setResult] = useState<ClientAccumuladoResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.get<HierarchyNode[]>("/hierarchy/nodes/").then(setNodes);
  }, []);

  // Mesmo ponto de partida do Acumulado de Vendas: a posição mais sênior do próprio usuário.
  useEffect(() => {
    if (selectedNodeId === null && user && user.hierarchy_nodes.length > 0) {
      setSelectedNodeId(user.hierarchy_nodes[0].id);
    }
  }, [user, selectedNodeId]);

  useEffect(() => {
    if (selectedNodeId === null) return;
    setLoading(true);
    setError(null);
    void api
      .get<ClientAccumuladoResult>(
        `/sales-history/results/acumulado-clientes/?ano=${selectedPeriod.ano}&mes=${selectedPeriod.mes}&node=${selectedNodeId}`,
      )
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof ApiError ? err.message : "Não foi possível carregar o acumulado de clientes.");
      })
      .finally(() => setLoading(false));
  }, [selectedPeriod, selectedNodeId]);

  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const currentNode = selectedNodeId !== null ? (nodesById.get(selectedNodeId) ?? null) : null;
  const parentNode = currentNode?.parent != null ? (nodesById.get(currentNode.parent) ?? null) : null;

  const ownNodeId = user && user.hierarchy_nodes.length > 0 ? user.hierarchy_nodes[0].id : null;

  // Filhos diretos da própria posição de login do usuário (`ownNodeId`), sempre um nível abaixo
  // (Gerente->Coordenador, Coordenador->Supervisor, Supervisor->Vendedor) — mesma regra de
  // `_effective_team_children` (`apps/allocations/results.py`, tabela "Equipe" do Acumulado de
  // Vendas), só que aqui vira uma janela simples com os nomes, não uma tabela com meta/realizado.
  //
  // Fixo em `ownNodeId`, não em `selectedNodeId` (revisão 2026-09-18, terceira volta): a lista tem
  // que continuar oferecendo os IRMÃOS do nó atual (ex.: um Gerente vendo o Coordenador A ainda
  // precisa poder trocar pro Coordenador B), mas nunca um nível a mais (o Gerente não pode
  // "destravar" Supervisor só por já estar dentro de um Coordenador) — usar `selectedNodeId` aqui
  // faria a lista mudar pros filhos de QUALQUER nó visitado; usar `ownNodeId` mantém sempre a
  // mesma lista (um nível abaixo da própria posição), disponível o tempo todo.
  const children = useMemo(
    () =>
      ownNodeId !== null
        ? nodes
            .filter((node) => node.parent === ownNodeId && node.ativo)
            .sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR"))
        : [],
    [nodes, ownNodeId],
  );

  return {
    months,
    selectedPeriod,
    setSelectedPeriod,
    selectedNodeId,
    setSelectedNodeId,
    parentNode,
    children,
    result,
    loading,
    error,
  };
}
