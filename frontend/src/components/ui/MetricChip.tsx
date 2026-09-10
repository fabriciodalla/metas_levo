import type { ReactNode } from "react";

type Tone = "neutral" | "primary" | "success" | "warning" | "danger";
type Size = "sm" | "md" | "lg" | "xl" | "2xl";

interface Props {
  label: string;
  value: ReactNode;
  valueTitle?: string;
  tone?: Tone;
  size?: Size;
  className?: string;
}

// Rótulo em caixa alta + valor em destaque, empilhados — o mesmo "chip" que aparecia
// reimplementado em paralelo como `pg-header-kg`/`sv-card-metric`/`rdt-summary-block`, cada um com
// sua própria cópia quase idêntica de label/value/cor. Um só componente agora, com o `className`
// só para o espaçamento/posicionamento específico de cada tela (largura mínima, flex, alinhamento).
export function MetricChip({ label, value, valueTitle, tone = "neutral", size = "md", className }: Props) {
  return (
    <span className={["metric-chip", className].filter(Boolean).join(" ")}>
      <span className="metric-chip-label">{label}</span>
      <strong
        className={`metric-chip-value metric-chip-value-${size} metric-chip-value-${tone}`}
        title={valueTitle}
      >
        {value}
      </strong>
    </span>
  );
}
