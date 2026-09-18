export interface Period {
  ano: number;
  mes: number;
}

const MESES_ABREV = [
  "Jan",
  "Fev",
  "Mar",
  "Abr",
  "Mai",
  "Jun",
  "Jul",
  "Ago",
  "Set",
  "Out",
  "Nov",
  "Dez",
];

function periodKey(period: Period): string {
  return `${period.ano}-${period.mes}`;
}

// Independente de `Cycle` (ciclo de metas) de propósito — esta tela não compara com meta nenhuma,
// então o filtro cobre qualquer mês com dado de venda sincronizado, não só os meses que têm um
// ciclo de distribuição aberto/fechado (ver ClientAccumuladoView, revisão 2026-09-17).
export function MonthSelect({
  months,
  value,
  onChange,
}: {
  months: Period[];
  value: Period;
  onChange: (period: Period) => void;
}) {
  return (
    <div className="field-inline">
      <label className="field-label" htmlFor="month-select">
        Mês
      </label>
      <select
        id="month-select"
        value={periodKey(value)}
        onChange={(e) => {
          const found = months.find((period) => periodKey(period) === e.target.value);
          if (found) onChange(found);
        }}
      >
        {months.map((period) => (
          <option key={periodKey(period)} value={periodKey(period)}>
            {MESES_ABREV[period.mes - 1]}/{period.ano}
          </option>
        ))}
      </select>
    </div>
  );
}
