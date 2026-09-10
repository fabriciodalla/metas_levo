import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { GoalAllocation } from "../api/types";
import { Alert } from "./ui/Alert";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Modal } from "./ui/Modal";

interface Props {
  ownerNodeId: number;
  cycleId: number;
  groupId: number;
  groupNome: string;
  /** Subgrupos deste grupo, já distribuídos, que `ownerNodeId` possui neste ciclo. */
  distributedSubgroups: GoalAllocation[];
  onReset: (resetAllocationIds: number[]) => void;
}

// "Resetar tudo do grupo": pedido explícito do usuário (2026-09-03) — as telas "Meta Supervisor"/
// "Meta Vendedor" (SubgroupCascadeWorkspace) só tinham o reset por subgrupo (ResetDistributionButton),
// tedioso quando o nível errou a distribuição do GRUPO inteiro (um grupo pode ter dezenas de
// subgrupos). Reseta de uma vez só todos os subgrupos já distribuídos do grupo pro nível que os
// possui (POST /allocations/reset-group/, ReopenAllocationService.reopen_group) — mesma garantia de
// sempre (H4): tudo-ou-nada, nunca apaga trabalho real de quem já avançou abaixo (`onReset` repassa
// os IDs resetados pro chamador pré-preencher a sugestão automática de novo em cada um).
export function ResetGroupDistributionButton({
  ownerNodeId,
  cycleId,
  groupId,
  groupNome,
  distributedSubgroups,
  onReset,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (distributedSubgroups.length === 0) return null;

  // Mesmo pré-check do ResetDistributionButton: se algum subgrupo já tem trabalho real abaixo
  // (`has_further_distribution`, calculado pelo backend), nem oferece o botão — evita um clique
  // que só ia voltar com erro. A mensagem de erro do backend já lista quem está travando.
  const blocked = distributedSubgroups.some((a) => a.has_further_distribution);
  if (blocked) {
    return (
      <span title="Pelo menos um subgrupo deste grupo já foi distribuído adiante por quem recebeu — peça pra essa pessoa resetar a distribuição dela primeiro. Depois disso este reset libera.">
        <Badge variant="neutral">Já distribuído adiante</Badge>
      </span>
    );
  }

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const reset = await api.post<GoalAllocation[]>("/allocations/reset-group/", {
        owner_node_id: ownerNodeId,
        cycle_id: cycleId,
        group_id: groupId,
      });
      setConfirming(false);
      onReset(reset.map((a) => a.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao resetar distribuição do grupo.");
    } finally {
      setSubmitting(false);
    }
  }

  const count = distributedSubgroups.length;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setConfirming(true)}
        title="Desfaz a distribuição de todos os subgrupos deste grupo pra recomeçar do zero"
      >
        <RotateCcw size={16} />
        Resetar tudo do grupo
      </Button>
      {confirming && (
        <Modal title={`Resetar todo o grupo "${groupNome}"?`} onClose={() => setConfirming(false)}>
          <p>
            Isso desfaz a distribuição de {count === 1 ? "1 subgrupo" : `${count} subgrupos`} deste
            grupo e devolve as quantidades pra você redistribuir do zero. Os campos voltam
            pré-preenchidos com a sugestão automática — só falta revisar e salvar de novo.
          </p>
          {error && <Alert variant="danger">{error}</Alert>}
          <div className="field-group mt-4">
            <Button variant="danger" onClick={() => void handleConfirm()} disabled={submitting}>
              {submitting ? "Resetando…" : "Resetar tudo do grupo"}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)} disabled={submitting}>
              Cancelar
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
