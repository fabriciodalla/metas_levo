import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { GoalAllocation } from "../api/types";
import { Alert } from "./ui/Alert";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Modal } from "./ui/Modal";

interface Props {
  allocation: GoalAllocation;
  onReset: () => void;
  size?: "sm" | "md";
}

// "Resetar distribuição": devolve ao dono a chance de corrigir um erro (destino errado,
// quantidade errada) numa alocação já distribuída, sem precisar mexer em nada além do próprio
// nível — mesmo POST /allocations/{id}/reopen/ já usado pelo H4 (ReopenAllocationService.reopen).
//
// Só libera quando ninguém abaixo já repassou o que recebeu adiante com trabalho real por trás
// (`has_further_distribution` — backend ignora desde 2026-09-03, ver GoalAllocationViewSet.
// get_queryset: filhos com 0 kg, e repasses automáticos de autogestão — SelfVendedorAuto
// DistributionService, único alvo possível — mesmo com quantidade > 0, já que nenhum dos dois é
// decisão de ninguém) — resetar aqui apagaria em cascata um trabalho que já avançou, sem quem
// fez esse trabalho saber (pedido do usuário, 2026-08-04). Quando bloqueado, mostra a explicação
// em vez do botão: quem está travando precisa resetar a distribuição *dele* primeiro (o mesmo
// botão, um nível abaixo) — feito isso, este libera sozinho, sem nada especial aqui.
export function ResetDistributionButton({ allocation, onReset, size = "sm" }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!allocation.distributed) return null;

  if (allocation.has_further_distribution) {
    return (
      <span title="Quem recebeu já distribuiu essa meta adiante — peça pra essa pessoa resetar a distribuição dela primeiro. Depois disso este reset libera.">
        <Badge variant="neutral">Já distribuído adiante</Badge>
      </span>
    );
  }

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      await api.post(`/allocations/${allocation.id}/reopen/`);
      setConfirming(false);
      onReset();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao resetar distribuição.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size={size}
        onClick={() => setConfirming(true)}
        title="Desfaz esta distribuição pra você corrigir e redistribuir"
      >
        <RotateCcw size={16} />
        Resetar distribuição
      </Button>
      {confirming && (
        <Modal title="Resetar distribuição?" onClose={() => setConfirming(false)}>
          <p>
            Isso desfaz esta distribuição e devolve a quantidade pra você redistribuir do zero.
            Nada abaixo deste nível foi distribuído ainda, então nenhum trabalho de terceiros se
            perde.
          </p>
          {error && <Alert variant="danger">{error}</Alert>}
          <div className="field-group mt-4">
            <Button variant="danger" onClick={() => void handleConfirm()} disabled={submitting}>
              {submitting ? "Resetando…" : "Resetar distribuição"}
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
