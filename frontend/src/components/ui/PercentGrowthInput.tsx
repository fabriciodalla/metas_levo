import { useEffect, useState, type ChangeEvent, type FocusEvent } from "react";

interface Props {
  /** `null` quando não há referência (média) pra calcular o percentual — campo fica vazio. */
  value: number | null;
  onChange: (value: number) => void;
  disabled?: boolean;
  ariaLabel?: string;
}

function formatPct(value: number | null): string {
  if (value === null) return "";
  return value.toFixed(1).replace(".", ",");
}

// Campo de percentual editável (aceita sinal e uma casa decimal, vírgula ou ponto) — usado para
// editar a meta indiretamente a partir de um crescimento % sobre uma referência (ex.: média dos
// últimos 3 meses). Só reformata o texto a partir do valor computado quando o campo NÃO está em
// foco — enquanto o usuário digita, o texto bruto manda, senão cada tecla seria sobrescrita pelo
// arredondamento do valor em kg que o percentual dispara no componente pai.
export function PercentGrowthInput({ value, onChange, disabled, ariaLabel }: Props) {
  const [text, setText] = useState(() => formatPct(value));
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setText(formatPct(value));
  }, [value, focused]);

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const raw = event.target.value;
    if (!/^-?\d*[.,]?\d*$/.test(raw)) return;
    setText(raw);

    const parsed = Number(raw.replace(",", "."));
    if (raw !== "" && raw !== "-" && !Number.isNaN(parsed)) {
      onChange(parsed);
    }
  }

  function handleFocus(event: FocusEvent<HTMLInputElement>) {
    setFocused(true);
    event.target.select();
  }

  const signClass = value === null || value === 0 ? "" : value > 0 ? "pct-input-positive" : "pct-input-negative";

  return (
    <span className={["pct-input", signClass].filter(Boolean).join(" ")}>
      <input
        type="text"
        inputMode="decimal"
        value={text}
        onChange={handleChange}
        onFocus={handleFocus}
        onBlur={() => setFocused(false)}
        disabled={disabled}
        aria-label={ariaLabel}
        placeholder="0"
      />
      <span className="pct-input-suffix">%</span>
    </span>
  );
}
