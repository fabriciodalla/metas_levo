import {
  BadgeCheck,
  Calendar,
  ChevronDown,
  ChevronUp,
  Info,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Cycle, GroupSuggestion, MonthlyPoint } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Spinner } from "../components/ui/Spinner";
import { EmptyState } from "../components/ui/EmptyState";

const MONTH_ABBR = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const MONTH_FULL = [
  "Janeiro",
  "Fevereiro",
  "Março",
  "Abril",
  "Maio",
  "Junho",
  "Julho",
  "Agosto",
  "Setembro",
  "Outubro",
  "Novembro",
  "Dezembro",
];

function formatKg(value: number): string {
  return `${Math.round(value).toLocaleString("pt-BR")} kg`;
}

function formatSignedPct(value: number | null, digits = 1): string {
  if (value === null) return "—";
  const sign = value >= 0 ? "+" : "-";
  return `${sign}${Math.abs(value).toFixed(digits).replace(".", ",")}%`;
}

function formatSignedKg(diff: number): string {
  const sign = diff >= 0 ? "+" : "-";
  return `${sign}${Math.abs(Math.round(diff)).toLocaleString("pt-BR")} kg`;
}

// vs. ano passado e vs. últimos 3 meses comparam o valor da META (o que está no campo editável —
// sugestão por padrão, mas o que o Gerente efetivamente digitar) a faturamento REAL (não a
// componentes internos do modelo) — são as duas leituras que o gerente usa pra calibrar a meta.
function yoyPct(metaKg: number, suggestion: GroupSuggestion): number | null {
  const lastYear = suggestion.same_month_last_year_kg;
  if (lastYear === null || lastYear <= 0) return null;
  return ((metaKg - lastYear) / lastYear) * 100;
}

function last3MonthsAvg(history: MonthlyPoint[]): number | null {
  if (history.length === 0) return null;
  const last3 = history.slice(-3);
  return last3.reduce((sum, point) => sum + point.quantity_kg, 0) / last3.length;
}

function vs3MonthsPct(metaKg: number, suggestion: GroupSuggestion): number | null {
  const avg3 = last3MonthsAvg(suggestion.history);
  if (avg3 === null || avg3 <= 0) return null;
  return ((metaKg - avg3) / avg3) * 100;
}

function editedKgFor(suggestion: GroupSuggestion, edits: Record<number, string>): number {
  return Math.round(Number(edits[suggestion.group_id] ?? suggestion.suggested_kg));
}

function aggregateYoyPct(suggestions: GroupSuggestion[], edits: Record<number, string>): number | null {
  const totalMeta = suggestions.reduce((sum, s) => sum + editedKgFor(s, edits), 0);
  const totalLastYear = suggestions.reduce((sum, s) => sum + (s.same_month_last_year_kg ?? 0), 0);
  if (totalLastYear <= 0) return null;
  return ((totalMeta - totalLastYear) / totalLastYear) * 100;
}

function aggregateVs3MonthsPct(suggestions: GroupSuggestion[], edits: Record<number, string>): number | null {
  const totalMeta = suggestions.reduce((sum, s) => sum + editedKgFor(s, edits), 0);
  const totalAvg3 = suggestions.reduce((sum, s) => sum + (last3MonthsAvg(s.history) ?? 0), 0);
  if (totalAvg3 <= 0) return null;
  return ((totalMeta - totalAvg3) / totalAvg3) * 100;
}

// Só usado na frase de explicação (tom de negócio) — não vira mais um número solto no card.
function seasonalPct(suggestion: GroupSuggestion): number {
  return (suggestion.seasonal_index - 1) * 100;
}

function bestMonth(history: MonthlyPoint[]): MonthlyPoint | null {
  return history.reduce<MonthlyPoint | null>(
    (best, point) => (!best || point.quantity_kg > best.quantity_kg ? point : best),
    null,
  );
}

function worstMonth(history: MonthlyPoint[]): MonthlyPoint | null {
  return history.reduce<MonthlyPoint | null>(
    (worst, point) => (!worst || point.quantity_kg < worst.quantity_kg ? point : worst),
    null,
  );
}

interface GroupCardProps {
  suggestion: GroupSuggestion;
  cycle: Cycle;
  totalSuggestedKg: number;
  value: string;
  onChange: (value: string) => void;
}

function ComparisonCol({ label, pct, refLabel }: { label: string; pct: number | null; refLabel: string }) {
  return (
    <div className="mg-group-col">
      <span className="mg-group-label">{label}</span>
      <span className="mg-group-value">
        {pct !== null ? (
          <>
            {pct >= 0 ? (
              <TrendingUp size={16} strokeWidth={2} className="mg-positive" />
            ) : (
              <TrendingDown size={16} strokeWidth={2} className="mg-negative" />
            )}{" "}
            {formatSignedPct(pct)}
          </>
        ) : (
          "—"
        )}
      </span>
      <span className="mg-group-sub">{refLabel}</span>
    </div>
  );
}

function GroupCard({ suggestion, cycle, totalSuggestedKg, value, onChange }: GroupCardProps) {
  const [expanded, setExpanded] = useState(false);
  const pctOfTotal = totalSuggestedKg > 0 ? (suggestion.suggested_kg / totalSuggestedKg) * 100 : 0;

  const editedValue = Math.round(Number(value || suggestion.suggested_kg));
  const diff = editedValue - suggestion.suggested_kg;
  const yoy = yoyPct(editedValue, suggestion);
  const vs3 = vs3MonthsPct(editedValue, suggestion);

  const monthName = MONTH_FULL[cycle.mes - 1];
  const seasonal = seasonalPct(suggestion);
  const best = bestMonth(suggestion.history);
  const worst = worstMonth(suggestion.history);
  const sameLastYear = suggestion.history.length > 0 ? suggestion.history[0] : null;

  const explanationParts: string[] = [];
  if (yoy !== null) {
    explanationParts.push(
      `Frente ao mesmo mês do ano passado, a meta informada representa ${
        yoy >= 0 ? "um crescimento" : "uma queda"
      } de ${Math.abs(yoy).toFixed(1).replace(".", ",")}%.`,
    );
  }
  if (vs3 !== null) {
    explanationParts.push(
      `Comparada à média dos últimos 3 meses, a meta informada fica ${Math.abs(vs3).toFixed(1).replace(".", ",")}% ${
        vs3 >= 0 ? "acima" : "abaixo"
      }.`,
    );
  }
  if (Math.abs(seasonal) >= 1) {
    explanationParts.push(
      `${monthName} historicamente vende ${Math.abs(seasonal).toFixed(1).replace(".", ",")}% ${
        seasonal > 0 ? "acima" : "abaixo"
      } da média do ano.`,
    );
  }

  return (
    <div className="mg-group-card-wrap">
      <div className={`mg-group-card${expanded ? " mg-group-card-expanded" : ""}`}>
        <div className="mg-group-col mg-group-name">
          <strong>{suggestion.group_nome}</strong>
          <span>Grupo de produtos</span>
        </div>

        <div className="mg-group-col">
          <span className="mg-group-label">Sugestão</span>
          <span className="mg-group-value">{formatKg(suggestion.suggested_kg)}</span>
          <span className="mg-group-sub mg-positive">{pctOfTotal.toFixed(0)}% da meta total</span>
        </div>

        <ComparisonCol
          label="Vs. ano passado"
          pct={yoy}
          refLabel={sameLastYear ? formatKg(sameLastYear.quantity_kg) : "Sem histórico"}
        />

        <ComparisonCol
          label="Vs. últimos 3 meses"
          pct={vs3}
          refLabel={last3MonthsAvg(suggestion.history) !== null ? `média ${formatKg(last3MonthsAvg(suggestion.history)!)}` : "Sem histórico"}
        />

        <div className="mg-group-col mg-group-input-col">
          <div className="mg-group-input">
            <input
              type="number"
              min={0}
              step={1}
              value={value}
              disabled={suggestion.already_created}
              onChange={(e) => onChange(e.target.value)}
              aria-label={`Meta de ${suggestion.group_nome} em kg`}
            />
            <span>kg</span>
          </div>
          {suggestion.already_created ? (
            <span className="mg-group-sub mg-neutral">Meta já criada</span>
          ) : diff === 0 ? (
            <span className="mg-group-sub mg-positive">Igual à sugestão</span>
          ) : (
            <span className={`mg-group-sub ${diff > 0 ? "mg-positive" : "mg-negative"}`}>{formatSignedKg(diff)}</span>
          )}
        </div>

        <button
          type="button"
          className="mg-group-toggle-btn"
          onClick={() => setExpanded((v) => !v)}
          aria-label="Por que essa sugestão?"
          aria-expanded={expanded}
        >
          {expanded ? <ChevronUp size={20} strokeWidth={2} /> : <ChevronDown size={20} strokeWidth={2} />}
        </button>
      </div>

      {expanded && (
        <div className="mg-group-expand">
          <h4>Histórico de faturamento (12 meses)</h4>
          <div className="mg-history-table-wrap">
            <table className="mg-history-table">
              <thead>
                <tr>
                  <th>Mês/ano</th>
                  <th>Kg faturado</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {suggestion.history.map((point) => {
                  const isBest = best !== null && point.ano === best.ano && point.mes === best.mes;
                  const isWorst = worst !== null && point.ano === worst.ano && point.mes === worst.mes;
                  const isLastYear =
                    sameLastYear !== null && point.ano === sameLastYear.ano && point.mes === sameLastYear.mes;
                  const rowClass = isBest
                    ? "mg-history-row-best"
                    : isWorst
                      ? "mg-history-row-worst"
                      : isLastYear
                        ? "mg-history-row-lastyear"
                        : "";
                  const valueClass = isBest ? "mg-positive" : isWorst ? "mg-negative" : "";
                  return (
                    <tr key={`${point.ano}-${point.mes}`} className={rowClass}>
                      <td>
                        {MONTH_ABBR[point.mes - 1]}/{point.ano}
                      </td>
                      <td className={valueClass}>{formatKg(point.quantity_kg)}</td>
                      <td className="mg-history-tags">
                        {isBest && <span className="mg-tag mg-tag-best">Melhor mês</span>}
                        {isWorst && <span className="mg-tag mg-tag-worst">Pior mês</span>}
                        {isLastYear && <span className="mg-tag mg-tag-lastyear">Mesmo mês ano passado</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {explanationParts.length > 0 && (
            <p className="mg-group-expand-explanation">{explanationParts.join(" ")}</p>
          )}

          {suggestion.has_gap && (
            <div className="mg-warning">
              <TriangleAlert size={16} strokeWidth={2} />
              <span>
                Pelo menos um mês do histórico não possui dados sincronizados. A sugestão pode estar
                distorcida.
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function MetaGerencialPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const gerenteNode = useMemo(() => user?.hierarchy_nodes.find((n) => n.level === "GERENTE"), [user]);

  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [selectedCycleId, setSelectedCycleId] = useState<number | null>(null);
  const [suggestions, setSuggestions] = useState<GroupSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    void api.get<Cycle[]>("/cycles/").then((data) => {
      setCycles(data);
      const open = data.find((cycle) => cycle.status === "ABERTO");
      setSelectedCycleId(open?.id ?? data[0]?.id ?? null);
    });
  }, []);

  function refreshSuggestions() {
    if (selectedCycleId === null || !gerenteNode) return;
    setLoading(true);
    void api
      .get<GroupSuggestion[]>(`/allocations/suggestions/?cycle=${selectedCycleId}&owner_node=${gerenteNode.id}`)
      .then((data) => {
        setSuggestions(data);
        setEdits((prev) => {
          const next = { ...prev };
          for (const s of data) {
            if (!(s.group_id in next)) next[s.group_id] = String(s.suggested_kg);
          }
          return next;
        });
      })
      .finally(() => setLoading(false));
  }

  useEffect(refreshSuggestions, [selectedCycleId, gerenteNode?.id]);

  const selectedCycle = cycles.find((c) => c.id === selectedCycleId) ?? null;
  const totalSuggestedKg = useMemo(() => suggestions.reduce((sum, s) => sum + s.suggested_kg, 0), [suggestions]);
  const overallYoyPct = useMemo(() => aggregateYoyPct(suggestions, edits), [suggestions, edits]);
  const overallVs3MonthsPct = useMemo(() => aggregateVs3MonthsPct(suggestions, edits), [suggestions, edits]);
  const totalEditedKg = useMemo(
    () =>
      suggestions.reduce(
        (sum, s) => sum + Math.max(0, Math.round(Number(edits[s.group_id] ?? s.suggested_kg))),
        0,
      ),
    [suggestions, edits],
  );

  async function handleSaveAll() {
    if (selectedCycleId === null || !gerenteNode) return;
    setSaveError(null);
    const targets = suggestions.filter((s) => !s.already_created);
    if (targets.length === 0) return;

    setSaving(true);
    const failures: string[] = [];
    for (const suggestion of targets) {
      const raw = edits[suggestion.group_id] ?? String(suggestion.suggested_kg);
      const quantity = Math.max(0, Math.round(Number(raw)));
      try {
        await api.post("/allocations/root/", {
          cycle_id: selectedCycleId,
          owner_node_id: gerenteNode.id,
          granularity: "GROUP",
          group_id: suggestion.group_id,
          quantity_kg: quantity,
        });
      } catch (err) {
        failures.push(`${suggestion.group_nome}${err instanceof ApiError ? ` (${err.message})` : ""}`);
      }
    }
    setSaving(false);

    if (failures.length > 0) {
      setSaveError(`Não foi possível criar a meta de: ${failures.join(", ")}.`);
      refreshSuggestions();
      return;
    }
    navigate("/distribuicao/distribuir");
  }

  if (!gerenteNode) return null;

  return (
    <div className="mg-page">
      <div className="mg-header">
        <div className="mg-header-cards">
          <div className="mg-mini-card">
            <Calendar size={18} strokeWidth={2} />
            <select
              value={selectedCycleId ?? ""}
              onChange={(e) => setSelectedCycleId(Number(e.target.value))}
              aria-label="Ciclo"
            >
              {cycles.map((cycle) => (
                <option key={cycle.id} value={cycle.id}>
                  {String(cycle.mes).padStart(2, "0")}/{cycle.ano} ({cycle.status})
                </option>
              ))}
            </select>
          </div>
          <div className="mg-mini-card">
            <BadgeCheck size={18} strokeWidth={2} />
            <span className={`mg-status-badge ${selectedCycle?.status === "ABERTO" ? "mg-status-open" : ""}`}>
              {selectedCycle?.status ?? "—"}
            </span>
          </div>
        </div>
      </div>

      {loading && <Spinner />}

      {!loading && suggestions.length === 0 && <EmptyState>Nenhum grupo ativo encontrado.</EmptyState>}

      {!loading && suggestions.length > 0 && selectedCycle && (
        <>
          <div className="mg-kpi-row">
            <div className="mg-kpi-card">
              <div className="mg-kpi-text">
                <span className="mg-kpi-label">META TOTAL</span>
                <span className="mg-kpi-value">{formatKg(totalEditedKg)}</span>
                <span className="mg-kpi-caption">Soma dos valores informados</span>
              </div>
            </div>
            <div className="mg-kpi-card">
              <div className="mg-kpi-text">
                <span className="mg-kpi-label">CRESCIMENTO VS. ANO PASSADO</span>
                <span className="mg-kpi-value">{formatSignedPct(overallYoyPct)}</span>
                <span className="mg-kpi-caption">Comparado ao mesmo mês do ano passado</span>
              </div>
            </div>
            <div className="mg-kpi-card">
              <div className="mg-kpi-text">
                <span className="mg-kpi-label">VS. ÚLTIMOS 3 MESES</span>
                <span className="mg-kpi-value">{formatSignedPct(overallVs3MonthsPct)}</span>
                <span className="mg-kpi-caption">Comparado à média dos últimos 3 meses</span>
              </div>
            </div>
          </div>

          <div className="mg-info-box">
            <Info size={18} strokeWidth={2} />
            <p>
              As sugestões são calculadas automaticamente utilizando tendência e sazonalidade dos últimos 12
              meses completos. Revise apenas os grupos que desejar alterar.
            </p>
          </div>

          <div className="mg-group-list">
            {[...suggestions]
              .sort((a, b) => a.group_nome.localeCompare(b.group_nome, "pt-BR"))
              .map((suggestion) => (
                <GroupCard
                  key={suggestion.group_id}
                  suggestion={suggestion}
                  cycle={selectedCycle}
                  totalSuggestedKg={totalSuggestedKg}
                  value={edits[suggestion.group_id] ?? String(suggestion.suggested_kg)}
                  onChange={(value) => setEdits((prev) => ({ ...prev, [suggestion.group_id]: value }))}
                />
              ))}
          </div>

          {saveError && <div className="mg-save-error">{saveError}</div>}

          <div className="mg-footer">
            <div className="mg-footer-total">
              <span className="mg-footer-total-label">Meta sugerida (total)</span>
              <span className="mg-footer-total-value">{totalSuggestedKg.toLocaleString("pt-BR")} kg</span>
            </div>
            <div className="mg-footer-actions">
              <button type="button" className="mg-btn-outline" onClick={() => navigate("/distribuicao/distribuir")}>
                Cancelar
              </button>
              <button type="button" className="mg-btn-primary" onClick={handleSaveAll} disabled={saving}>
                {saving ? "Salvando…" : "Salvar e criar metas"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
