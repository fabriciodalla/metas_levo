import type { ReactNode } from "react";
import { MetricChip } from "./MetricChip";

// Caixa com barra de destaque (`.stat-tile`) em volta de um `MetricChip` — o par label+valor em
// si não é mais reimplementado aqui (bug real, 2026-08-07: `.stat-tile-value`/`.stat-tile-label`
// duplicavam exatamente o que `MetricChip` já resolve, com sua própria cópia de CSS). Só a caixa
// (fundo, borda, sombra, barra lateral) continua própria daqui — nada mais no app reaproveita
// esse formato específico de "grade de KPIs no topo da tela".
export function StatTile({
  value,
  label,
}: {
  value: ReactNode;
  label: string;
}) {
  return (
    <div className="stat-tile">
      <MetricChip size="2xl" tone="primary" label={label} value={value} />
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return <div className="stat-row">{children}</div>;
}
