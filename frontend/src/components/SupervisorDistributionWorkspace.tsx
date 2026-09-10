import { ChevronLeft, ChevronRight, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { HierarchyNode } from "../api/types";
import { SupervisorCard } from "./SupervisorCard";
import { Alert } from "./ui/Alert";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";
import { MetricChip } from "./ui/MetricChip";
import { ProgressBar } from "./ui/ProgressBar";

function formatKg(value: number): string {
  return `${Math.round(value).toLocaleString("pt-BR")} kg`;
}

function formatPct(value: number): string {
  return `${Math.round(value)}%`;
}

export interface SupervisorWorkspaceRow {
  supervisor: HierarchyNode;
  quantityKg: number | "";
  onChange?: (value: number | "") => void;
  metaTotalSupervisorKg: number;
  metaSupervisorGrupoKg: number;
  last3MonthsAvgKg: number | null;
}

interface Props {
  subgroupNome: string;
  rows: SupervisorWorkspaceRow[];
  total: number;
  diff: number;
  editable: boolean;
  submitting: boolean;
  canSave: boolean;
  hasDraft: boolean;
  error: string | null;
  info: string | null;
  onSave: () => void;
  groupTotalKg: number;
  /** Reaproveitado também pela tela "Meta Vendedor" — só o texto muda entre os dois papéis. */
  title?: string;
  emptyRowsMessage?: string;
  cardMetaLabel?: string;
}

// Painel principal da tela "Meta Supervisor": sempre 2 cards inteiros lado a lado (nunca um
// terceiro cortado na borda), navegáveis pelas setas — cada clique desliza exatamente um card,
// nunca parando com um card pela metade (scroll-snap). Todos mostram só o subgrupo selecionado na
// lista lateral, mais o resumo fixo do subgrupo abaixo do carrossel.
// `editable=false` só quando o subgrupo ATUALMENTE selecionado já foi distribuído (reabrir um
// subgrupo isolado saiu de escopo aqui — pedido do usuário, 2026-09-03: o reset por subgrupo
// atrapalhava mais do que ajudava; só o reset do GRUPO inteiro continua, em
// `ResetGroupDistributionButton`, um nível acima em `SubgroupCascadeWorkspace`) — o botão Salvar
// continua ativo mesmo assim, pois salva o grupo inteiro (todos os subgrupos com rascunho pronto),
// não só o que está visível no momento.
export function SupervisorDistributionWorkspace({
  subgroupNome,
  rows,
  total,
  diff,
  editable,
  submitting,
  canSave,
  hasDraft,
  error,
  info,
  onSave,
  groupTotalKg,
  title = "Distribuição para Supervisores",
  emptyRowsMessage = "Nenhum supervisor disponível para distribuição.",
  cardMetaLabel = "Meta do supervisor",
}: Props) {
  const metaSubgrupo = total + diff;
  const percentSubgrupo = metaSubgrupo > 0 ? (total / metaSubgrupo) * 100 : 0;
  const isOver = diff < 0;

  // Sempre 2 cards inteiros lado a lado (nunca um terceiro cortado na borda) — navegar pelas
  // setas desliza exatamente um card por vez, com scroll-snap garantindo que nunca pare com um
  // card pela metade.
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [canScrollPrev, setCanScrollPrev] = useState(false);
  const [canScrollNext, setCanScrollNext] = useState(false);

  function updateScrollState() {
    const el = scrollerRef.current;
    if (!el) return;
    setCanScrollPrev(el.scrollLeft > 4);
    setCanScrollNext(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  }

  useEffect(() => {
    updateScrollState();
    window.addEventListener("resize", updateScrollState);
    return () => window.removeEventListener("resize", updateScrollState);
  }, [rows.length]);

  function scrollByOneCard(direction: 1 | -1) {
    scrollerRef.current?.scrollBy({ left: direction * (scrollerRef.current.clientWidth / 2), behavior: "smooth" });
  }

  return (
    <div className="sv-workspace">
      <div className="sv-workspace-header">
        <div className="card-title-group">
          <h4>{title}</h4>
          {hasDraft && (
            <span className="unsaved-indicator">
              <span className="unsaved-dot" aria-hidden="true" />
              Alterações não salvas no grupo
            </span>
          )}
          {!editable && <Badge variant="success">Este subgrupo já foi salvo</Badge>}
        </div>
        <Button onClick={onSave} disabled={!canSave || submitting} aria-label="Salvar distribuição do grupo">
          <Save size={16} />
          {submitting ? "Salvando…" : "Salvar distribuição"}
        </Button>
      </div>

      {rows.length === 0 ? (
        <EmptyState>{emptyRowsMessage}</EmptyState>
      ) : (
        <div className="sv-carousel-shell">
          <button
            type="button"
            className="sv-carousel-arrow"
            onClick={() => scrollByOneCard(-1)}
            disabled={!canScrollPrev}
            aria-label="Ver supervisor anterior"
          >
            <ChevronLeft size={20} strokeWidth={2} />
          </button>

          <div className="sv-carousel-wrap">
            <div className="sv-carousel" ref={scrollerRef} onScroll={updateScrollState}>
              {rows.map((row) => (
                <SupervisorCard
                  key={row.supervisor.id}
                  supervisor={row.supervisor}
                  subgroupNome={subgroupNome}
                  quantityKg={row.quantityKg}
                  onChange={row.onChange}
                  metaTotalSupervisorKg={row.metaTotalSupervisorKg}
                  metaSupervisorGrupoKg={row.metaSupervisorGrupoKg}
                  last3MonthsAvgKg={row.last3MonthsAvgKg}
                  groupTotalKg={groupTotalKg}
                  metaLabel={cardMetaLabel}
                />
              ))}
            </div>
          </div>

          <button
            type="button"
            className="sv-carousel-arrow"
            onClick={() => scrollByOneCard(1)}
            disabled={!canScrollNext}
            aria-label="Ver próximo supervisor"
          >
            <ChevronRight size={20} strokeWidth={2} />
          </button>
        </div>
      )}

      <div className="rdt-summary sv-sticky-summary">
        <MetricChip label="Total distribuído no subgrupo" value={formatKg(total)} tone="success" size="xl" />
        <div className="rdt-summary-bar">
          <ProgressBar percent={percentSubgrupo} variant={isOver ? "danger" : "success"} />
          <span>{formatPct(percentSubgrupo)}</span>
        </div>
        <MetricChip
          className="rdt-summary-block-end"
          label="Restante no subgrupo"
          value={formatKg(Math.abs(diff))}
          size="xl"
          tone={diff === 0 ? "success" : isOver ? "danger" : "warning"}
        />
      </div>

      {isOver && (
        <Alert variant="danger">
          Distribuição acima da meta do subgrupo em {formatKg(-diff)}. Ajuste os valores para salvar.
        </Alert>
      )}
      {error && <Alert variant="danger">{error}</Alert>}
      {info && <Alert variant="warning">{info}</Alert>}
    </div>
  );
}
