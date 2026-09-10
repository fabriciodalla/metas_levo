import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ChildAllocationInput, ChildDistributionContext, GoalAllocation, HierarchyNode } from "../api/types";

export type SubgroupDraftRow = Record<number, number | "">; // supervisorId -> quantityKg

// Distribuição em lote pra tela "Meta Supervisor": ao contrário de `useDistributionRows` (usado em
// Gerente→Local, uma alocação por vez), aqui um Coordenador Local pode ter
// dezenas de subgrupos no mesmo grupo — exigir "Salvar" a cada um seria repetitivo. O rascunho de
// TODOS os subgrupos do grupo selecionado fica vivo ao trocar de seleção na lateral; só é
// descartado ao trocar de grupo/ciclo (com aviso — ver MetaSupervisorPage) ou depois de salvo. Cada
// subgrupo continua persistido pelo mesmo endpoint POST /allocations/{id}/distribute/ já usado nas
// outras telas, só a orquestração (uma chamada por subgrupo) muda — mas a MESMA regra de
// preenchimento completo + fechamento exato dessas telas (`useDistributionRows`) também vale aqui,
// só que pro GRUPO inteiro (`canSaveGroup`, revisão 2026-09-03, a pedido explícito do usuário):
// "Salvar distribuição" só libera quando TODO subgrupo do grupo (não só os que a pessoa abriu)
// estiver com todos os alvos preenchidos e fechando exato. Um subgrupo nunca aberto não tem
// rascunho (`isRowReadyToSave` retorna `false` pra ele) — a pessoa precisa passar por todos, não só
// pelos que decidiu editar, pro "Restante" do grupo zerar e o botão liberar.
export function useGroupSupervisorDraft(groupId: number | null, supervisors: HierarchyNode[]) {
  const [draft, setDraft] = useState<Record<number, SubgroupDraftRow>>({});
  const [contextBySubgroup, setContextBySubgroup] = useState<
    Record<number, Record<number, ChildDistributionContext>>
  >({});
  const [, setLoadedIds] = useState<Set<number>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    setDraft({});
    setContextBySubgroup({});
    setLoadedIds(new Set());
    setError(null);
    setInfo(null);
  }, [groupId]);

  // Busca o contexto histórico (mesmo endpoint de distribution-context) de um subgrupo, sem
  // recarregar nada dos já visitados nesta sessão — `loadedIds` é compartilhado com `ensureLoaded`
  // abaixo pra nunca buscar duas vezes o mesmo subgrupo, seja ele editável ou já distribuído.
  // `force` (usado depois de um "Resetar tudo do grupo") ignora esse cache: o subgrupo pode já ter
  // sido visitado ENQUANTO estava distribuído (`ensureContextLoaded`, sem criar rascunho), e sem
  // recarregar `onLoaded` nunca dispara de novo — o rascunho ficaria em branco, sem sugestão.
  const fetchContext = useCallback(
    (
      allocationId: number,
      onLoaded?: (byNode: Record<number, ChildDistributionContext>) => void,
      force = false,
    ) => {
      setLoadedIds((prev) => {
        if (prev.has(allocationId) && !force) return prev;
        void api
          .get<ChildDistributionContext[]>(`/allocations/${allocationId}/distribution-context/`)
          .then((data) => {
            const byNode = Object.fromEntries(data.map((ctx) => [ctx.owner_node_id, ctx]));
            setContextBySubgroup((current) => ({ ...current, [allocationId]: byNode }));
            onLoaded?.(byNode);
          })
          .catch(() => {});
        if (prev.has(allocationId)) return prev;
        const next = new Set(prev);
        next.add(allocationId);
        return next;
      });
    },
    [],
  );

  // Chamado quando um subgrupo é selecionado pela primeira vez (ou resetado em lote, `force=true`):
  // cria a linha em branco (uma por supervisor) e busca a sugestão histórica pra pré-preenchê-la.
  const ensureLoaded = useCallback(
    (allocationId: number, force = false) => {
      setDraft((prev) => {
        if (prev[allocationId] && !force) return prev;
        const row: SubgroupDraftRow = {};
        for (const supervisor of supervisors) row[supervisor.id] = "";
        return { ...prev, [allocationId]: row };
      });

      fetchContext(
        allocationId,
        (byNode) => {
          setDraft((current) => {
            const row = current[allocationId];
            if (!row) return current;
            let changed = false;
            const updated = { ...row };
            for (const supervisor of supervisors) {
              const suggested = byNode[supervisor.id]?.suggested_kg;
              if (updated[supervisor.id] === "" && suggested != null) {
                updated[supervisor.id] = suggested;
                changed = true;
              }
            }
            return changed ? { ...current, [allocationId]: updated } : current;
          });
        },
        force,
      );
    },
    [supervisors, fetchContext],
  );

  // Mesma busca de contexto, mas sem tocar no rascunho — usada quando o subgrupo selecionado JÁ
  // foi distribuído (card em modo leitura): não existe o que pré-preencher, mas o contexto ainda
  // alimenta a "Média 3 meses"/"% crescimento" informativas no card.
  const ensureContextLoaded = useCallback(
    (allocationId: number) => {
      fetchContext(allocationId);
    },
    [fetchContext],
  );

  function updateCell(allocationId: number, supervisorId: number, value: number | "") {
    setDraft((prev) => ({
      ...prev,
      [allocationId]: { ...(prev[allocationId] ?? {}), [supervisorId]: value },
    }));
  }

  function rowValue(allocationId: number, supervisorId: number): number | "" {
    return draft[allocationId]?.[supervisorId] ?? "";
  }

  function totalFor(allocationId: number): number {
    const row = draft[allocationId];
    if (!row) return 0;
    return Object.values(row).reduce<number>((sum, v) => sum + (typeof v === "number" ? v : 0), 0);
  }

  function hasDraftFor(allocationId: number): boolean {
    const row = draft[allocationId];
    return row ? Object.values(row).some((v) => v !== "") : false;
  }

  function hasAnyDraft(allocations: GoalAllocation[]): boolean {
    return allocations.some((a) => hasDraftFor(a.id));
  }

  // Regra já estipulada (mesma exigida em `useDistributionRows`/`DistributionForm`, usada em
  // Gerente→Local): só é permitido salvar quando TODOS os alvos têm um valor
  // preenchido (nenhum "" — um alvo sem decisão não pode virar 0 kg por omissão) e a soma fecha
  // exatamente com a meta do subgrupo, nem mais nem menos.
  function isRowReadyToSave(allocation: GoalAllocation): boolean {
    const row = draft[allocation.id];
    if (!row) return false;
    const allFilled = supervisors.every((supervisor) => typeof row[supervisor.id] === "number");
    return allFilled && totalFor(allocation.id) === allocation.quantity_kg;
  }

  // Trava do botão "Salvar distribuição": libera só quando TODO subgrupo do GRUPO (com meta > 0,
  // ainda não distribuído) está pronto — não só os que a pessoa tocou (revisão 2026-09-03, a
  // pedido explícito do usuário: navegar sem abrir um subgrupo deixa o rascunho dele inexistente
  // — `isRowReadyToSave` retorna `false` — então o "Restante" daquele subgrupo continua contando
  // como pendente até a pessoa abrir e revisar; só readar todos é que zera o restante do GRUPO
  // inteiro e libera o botão). Faltava exatamente essa trava — antes só exigia os subgrupos
  // TOCADOS estarem completos, deixando salvar mesmo com subgrupos do grupo ainda intocados.
  function canSaveGroup(allocations: GoalAllocation[]): boolean {
    const pending = allocations.filter((a) => !a.distributed);
    return pending.length > 0 && pending.every((a) => isRowReadyToSave(a));
  }

  // Salva de uma vez todo subgrupo do grupo — só chega a rodar depois que `canSaveGroup` já
  // liberou o botão (todo subgrupo do grupo completo e fechando exato), mas mantém a checagem
  // própria como segunda barreira caso `saveGroup` seja chamado fora desse fluxo.
  async function saveGroup(
    allocations: GoalAllocation[],
    nomeOf: (a: GoalAllocation) => string,
    onSaved: (saved: GoalAllocation[]) => void,
  ) {
    setError(null);
    setInfo(null);

    const touched = allocations.filter((a) => !a.distributed);
    const ready = touched.filter((a) => isRowReadyToSave(a));
    const pending = touched.filter((a) => !isRowReadyToSave(a));

    if (pending.length > 0) {
      setError(
        `Preencha todos os alvos e feche a diferença em zero em todo o grupo antes de salvar: ${pending.map(nomeOf).join(", ")}.`,
      );
      return;
    }

    if (ready.length === 0) {
      setError("Nenhuma alteração para salvar.");
      return;
    }

    setSubmitting(true);
    const saved: GoalAllocation[] = [];
    const failures: string[] = [];
    for (const allocation of ready) {
      const row = draft[allocation.id] ?? {};
      const children: ChildAllocationInput[] = supervisors.map((supervisor) => ({
        owner_node_id: supervisor.id,
        quantity_kg: typeof row[supervisor.id] === "number" ? (row[supervisor.id] as number) : 0,
        granularity: allocation.granularity,
        group_id: allocation.group,
        subgroup_id: allocation.subgroup,
        product_id: allocation.product,
      }));
      try {
        await api.post(`/allocations/${allocation.id}/distribute/`, { children });
        saved.push(allocation);
      } catch (err) {
        failures.push(`${nomeOf(allocation)}${err instanceof ApiError ? ` (${err.message})` : ""}`);
      }
    }
    setSubmitting(false);

    if (saved.length > 0) {
      setDraft((prev) => {
        const next = { ...prev };
        for (const allocation of saved) delete next[allocation.id];
        return next;
      });
    }

    if (failures.length > 0) setInfo(`falha ao salvar: ${failures.join(", ")}`);

    if (saved.length > 0) onSaved(saved);
  }

  return {
    hasDraftFor,
    hasAnyDraft,
    canSaveGroup,
    isRowReadyToSave,
    rowValue,
    updateCell,
    totalFor,
    ensureLoaded,
    ensureContextLoaded,
    contextBySubgroup,
    submitting,
    error,
    info,
    saveGroup,
  };
}
