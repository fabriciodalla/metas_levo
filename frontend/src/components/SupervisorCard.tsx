import type { HierarchyNode } from "../api/types";
import { Badge } from "./ui/Badge";
import { MetricChip } from "./ui/MetricChip";
import { NumericKgInput } from "./ui/NumericKgInput";
import { PercentGrowthInput } from "./ui/PercentGrowthInput";
import { ProgressBar } from "./ui/ProgressBar";

function formatKg(value: number): string {
  return `${Math.round(value).toLocaleString("pt-BR")} kg`;
}

function toTitleCase(nome: string): string {
  return nome
    .trim()
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function initials(nome: string): string {
  const parts = nome.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

interface Props {
  supervisor: HierarchyNode;
  subgroupNome: string;
  quantityKg: number | "";
  onChange?: (value: number | "") => void;
  metaTotalSupervisorKg: number;
  metaSupervisorGrupoKg: number;
  /** Média de kg dos últimos 3 meses deste alvo neste subgrupo — referência fixa (não editável)
   * pro campo de % de crescimento abaixo. `null` quando não há histórico suficiente. */
  last3MonthsAvgKg: number | null;
  groupTotalKg: number;
  /** Reaproveitado também pela tela "Meta Vendedor" (mesmo componente, um nível abaixo na
   * hierarquia) — só o texto do primeiro indicador muda ("Meta do supervisor"/"Meta do vendedor"). */
  metaLabel?: string;
}

// Um card por pessoa na grade de distribuição (Supervisor em "Meta Supervisor", Vendedor em "Meta
// Vendedor") — mostra só o subgrupo atualmente selecionado na lateral esquerda (nunca a lista
// inteira de subgrupos). `onChange` ausente = card em modo leitura (subgrupo já distribuído,
// editar de novo exige reabrir a alocação primeiro).
export function SupervisorCard({
  supervisor,
  subgroupNome,
  quantityKg,
  onChange,
  metaTotalSupervisorKg,
  metaSupervisorGrupoKg,
  last3MonthsAvgKg,
  groupTotalKg,
  metaLabel = "Meta do supervisor",
}: Props) {
  const readOnly = !onChange;
  const groupPercent = groupTotalKg > 0 ? (metaSupervisorGrupoKg / groupTotalKg) * 100 : 0;

  // Média (referência fixa) e % de crescimento sobre ela são as duas faces do mesmo número: a
  // meta deste subgrupo (`quantityKg`, campo abaixo). Editar a % recalcula a meta em kg; editar a
  // meta recalcula a % automaticamente (é derivada) — nunca duas fontes de verdade divergentes.
  // `hasAvg` só exige que o backend tenha calculado uma média (mesmo que seja 0 — histórico por
  // subgrupo é esparso, então "zero venda nos últimos 3 meses" é um valor real, não "sem dado").
  // `canEditPct` é mais estrito: sem uma base > 0 não dá pra expressar a meta como % dela.
  const hasAvg = last3MonthsAvgKg !== null;
  const canEditPct = hasAvg && last3MonthsAvgKg! > 0;
  const growthPct =
    canEditPct && quantityKg !== "" ? ((quantityKg - last3MonthsAvgKg!) / last3MonthsAvgKg!) * 100 : null;

  function handleGrowthChange(pct: number) {
    if (!canEditPct || !onChange) return;
    onChange(Math.max(0, Math.round(last3MonthsAvgKg! * (1 + pct / 100))));
  }

  return (
    <article className={["sv-card", !supervisor.ativo ? "sv-card-inactive" : ""].filter(Boolean).join(" ")}>
      <div className="sv-card-header">
        <span className="sv-card-avatar" aria-hidden="true">
          {initials(supervisor.nome)}
        </span>
        <div className="sv-card-identity">
          <span className="sv-card-name">{toTitleCase(supervisor.nome)}</span>
          <Badge variant={supervisor.ativo ? "success" : "neutral"}>
            {supervisor.ativo ? "Ativo" : "Inativo"}
          </Badge>
        </div>
      </div>

      <div className="sv-card-metrics">
        <MetricChip
          className="sv-card-metric"
          size="lg"
          label={metaLabel}
          value={formatKg(metaTotalSupervisorKg)}
          valueTitle={formatKg(metaTotalSupervisorKg)}
        />
        <div className="sv-card-metric-divider" aria-hidden="true" />
        <MetricChip
          className="sv-card-metric"
          size="lg"
          label="Meta no grupo"
          value={formatKg(metaSupervisorGrupoKg)}
          valueTitle={formatKg(metaSupervisorGrupoKg)}
        />
      </div>
      <ProgressBar
        percent={groupPercent}
        variant={groupPercent > 100 ? "danger" : "success"}
        size="sm"
        label={`Progresso de ${toTitleCase(supervisor.nome)} no grupo selecionado`}
      />

      {hasAvg && (
        <div className="sv-card-growth">
          <div className="sv-card-growth-col">
            <span className="sv-card-growth-label">Média 3 meses</span>
            <span className="sv-card-growth-value">{formatKg(last3MonthsAvgKg!)}</span>
          </div>
          <div className="sv-card-growth-col">
            <span className="sv-card-growth-label">% crescimento</span>
            <PercentGrowthInput
              value={growthPct}
              onChange={handleGrowthChange}
              disabled={readOnly || !canEditPct}
              ariaLabel={`Percentual de crescimento sobre a média para ${toTitleCase(supervisor.nome)} em ${subgroupNome}`}
            />
          </div>
        </div>
      )}

      <div className="sv-card-row">
        <label className="sv-card-row-label" htmlFor={`sv-input-${supervisor.id}`}>
          {subgroupNome}
        </label>
        <NumericKgInput
          id={`sv-input-${supervisor.id}`}
          value={quantityKg}
          onChange={(value) => onChange?.(value)}
          disabled={readOnly}
          ariaLabel={`Meta definida para ${toTitleCase(supervisor.nome)} em ${subgroupNome}`}
        />
      </div>
    </article>
  );
}
