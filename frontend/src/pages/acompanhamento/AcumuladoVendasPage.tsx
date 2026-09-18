import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, BarChart3, CheckCircle2, ChevronDown, ChevronRight, Package2, TrendingUp } from "lucide-react";
import { CycleSelect } from "../admin/CycleSelect";
import { useAuth } from "../../auth/AuthContext";
import { api, ApiError } from "../../api/client";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ProgressBar } from "../../components/ui/ProgressBar";
import { Spinner } from "../../components/ui/Spinner";
import metaTotalIcon from "../../assets/icon-meta-total.png";
import realizadoIcon from "../../assets/icon-realizado.png";
import faltaMetaIcon from "../../assets/icon-falta-meta.png";
import ritmoNecessarioIcon from "../../assets/icon-ritmo-necessario.png";
import frangoIcon from "../../assets/icon-frango.png";
import revendaIcon from "../../assets/icon-revenda.png";
import type {
  AccumulatedSalesResult,
  AcvGroupResult,
  AcvStatus,
  AcvTeamGroupSummary,
  AcvTeamMemberResult,
} from "../../api/types";
import { useAcumuladoVendas } from "./useAcumuladoVendas";

// Ícone de imagem (glifo monocromático em PNG, ver pasta `icones/` na raiz do repo) recolorido via
// CSS mask — herda a cor do elemento pai (`currentColor`), então o mesmo arquivo serve pro badge
// verde, amarelo ou vermelho sem precisar de uma versão por cor.
function IconGlyph({ src, size = 20 }: { src: string; size?: number }) {
  return (
    <span
      className="acv-icon-glyph"
      style={{
        width: size,
        height: size,
        WebkitMaskImage: `url(${src})`,
        maskImage: `url(${src})`,
      }}
    />
  );
}

// Ícone específico só existe pra Frango/Revenda (arquivos fornecidos) — qualquer outro grupo que
// venha a ser cadastrado no catálogo cai no ícone genérico, sem precisar de arte nova.
function groupIconSrc(groupNome: string): string | null {
  const normalized = groupNome.trim().toUpperCase();
  if (normalized.startsWith("FRANGO")) return frangoIcon;
  if (normalized.startsWith("REVENDA")) return revendaIcon;
  return null;
}

const kgFormatter = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pctFormatter = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });

function formatKg(value: number): string {
  return `${kgFormatter.format(Math.round(value))} kg`;
}

// Números grandes (meta/realizado/falta somados de todos os grupos) em "Mi"/"mil" — só na faixa
// de totais do topo da tela; os cards por grupo continuam com o número cheio.
function formatKgAbbrev(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `${(value / 1_000_000).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Mi`;
  }
  if (abs >= 1_000) {
    return `${Math.round(value / 1_000).toLocaleString("pt-BR")} mil`;
  }
  return kgFormatter.format(Math.round(value));
}

function formatPct(value: number | null): string {
  return value === null ? "—" : `${pctFormatter.format(value * 100)}%`;
}

const STATUS_LABEL: Record<AcvStatus, string> = {
  VERDE: "Meta batida",
  AMARELO: "Atenção",
  VERMELHO: "Abaixo da meta",
  SEM_META: "Sem meta",
};

const STATUS_TONE: Record<AcvStatus, "success" | "warning" | "danger" | "neutral"> = {
  VERDE: "success",
  AMARELO: "warning",
  VERMELHO: "danger",
  SEM_META: "neutral",
};

// Ícone à esquerda do texto do badge — mesmo tom da cor, reforça o significado sem depender só
// da cor (acessibilidade e "bate o olho" mais rápido).
function badgeIcon(tone: "success" | "warning" | "danger" | "neutral"): ReactNode {
  if (tone === "success") return <CheckCircle2 size={13} />;
  if (tone === "neutral") return null;
  return <AlertTriangle size={13} />;
}

// Mesma ideia do badge de grupo (Volume e Subgrupos combinados) aplicada ao resumo da pessoa:
// além do tom (já rebaixado corretamente pelo backend quando falta subgrupo), o texto também diz
// qual das duas frentes está faltando, olhando todos os grupos com meta dessa pessoa.
function teamStatusLabel(member: AcvTeamMemberResult): string {
  if (member.status_geral === "VERDE" || member.status_geral === "SEM_META") {
    return STATUS_LABEL[member.status_geral];
  }

  const gruposComMeta = member.grupos.filter((g) => g.meta_kg > 0);
  const faltaVolume = gruposComMeta.some((g) => g.status !== "VERDE");
  const faltaSubgrupos = gruposComMeta.some((g) => g.subgrupos_atingiu === false);

  if (faltaVolume && faltaSubgrupos) return "Falta volume e subgrupos";
  if (faltaVolume) return "Falta volume";
  if (faltaSubgrupos) return "Falta subgrupos";
  return STATUS_LABEL[member.status_geral];
}

function StatusBadge({ member }: { member: AcvTeamMemberResult }) {
  const tone = STATUS_TONE[member.status_geral];
  return (
    <Badge variant={tone} icon={badgeIcon(tone)}>
      {teamStatusLabel(member)}
    </Badge>
  );
}

const VOLUME_MINIMO_PCT = 99.5;

// Badge do card de grupo: reflete só a meta de Volume (o card não exibe mais o detalhe de
// subgrupos, então o badge não precisa mais combinar as duas frentes).
function groupBadge(group: AcvGroupResult): { label: string; tone: "success" | "warning" | "danger" | "neutral" } {
  if (group.meta_kg <= 0) return { label: "Sem meta", tone: "neutral" };
  if (group.atingiu_grupo) return { label: "Meta atingida", tone: "success" };

  const volumeOk = group.status === "VERDE";
  const subgruposOk = group.subgrupos_atingiu === true;
  const tone = group.status === "AMARELO" ? "warning" : "danger";
  if (!volumeOk && !subgruposOk) return { label: "Falta volume e subgrupos", tone };
  if (!volumeOk) return { label: "Falta volume", tone };
  return { label: "Falta subgrupos", tone };
}

function GoalProgressBar({
  percent,
  variant,
  goalPercent,
  label,
}: {
  percent: number;
  variant: "success" | "warning" | "danger" | "neutral";
  goalPercent: number;
  label: string;
}) {
  return (
    <div className="acv-goal-progress">
      <ProgressBar percent={percent} variant={variant} size="sm" label={label} />
      <span className="acv-goal-marker" style={{ left: `${Math.min(100, Math.max(0, goalPercent))}%` }} />
    </div>
  );
}

// Soma simples dos grupos já calculados pelo backend (nenhuma fórmula nova, só agregação de
// exibição) — dá o retrato "de avião" antes de descer pra Frangos/Revenda/etc. individualmente.
function computeTotals(grupos: AcvGroupResult[], diasUteisRestantes: number) {
  const metaTotal = grupos.reduce((sum, g) => sum + g.meta_kg, 0);
  const realizadoTotal = grupos.reduce((sum, g) => sum + g.realizado_kg, 0);
  const faltaTotal = Math.max(metaTotal - realizadoTotal, 0);
  const pctTotal = metaTotal > 0 ? realizadoTotal / metaTotal : null;
  const ritmoTotal =
    metaTotal <= 0 ? null : faltaTotal <= 0 ? 0 : diasUteisRestantes > 0 ? faltaTotal / diasUteisRestantes : null;
  return { metaTotal, realizadoTotal, faltaTotal, pctTotal, ritmoTotal };
}

function TotalSummaryRow({ grupos, diasUteisRestantes }: { grupos: AcvGroupResult[]; diasUteisRestantes: number }) {
  const { metaTotal, realizadoTotal, faltaTotal, pctTotal, ritmoTotal } = computeTotals(grupos, diasUteisRestantes);

  return (
    <div className="acv-kpi-row">
      <div className="acv-kpi-card">
        <div className="acv-kpi-card-head">
          <span className="acv-kpi-icon acv-kpi-icon-primary">
            <IconGlyph src={metaTotalIcon} size={18} />
          </span>
          <div>
            <div className="acv-kpi-label">Meta Total</div>
            <div className="acv-kpi-subtitle">Volume de vendas (kg)</div>
          </div>
        </div>
        <div className="acv-kpi-value">{formatKgAbbrev(metaTotal)} kg</div>
        <div className="acv-kpi-caption">Nossa meta no período</div>
      </div>

      <div className="acv-kpi-card">
        <div className="acv-kpi-card-head">
          <span className="acv-kpi-icon acv-kpi-icon-success">
            <IconGlyph src={realizadoIcon} size={18} />
          </span>
          <div>
            <div className="acv-kpi-label">Realizado</div>
            <div className="acv-kpi-subtitle">Volume de vendas (kg)</div>
          </div>
        </div>
        <div className="acv-kpi-value-row">
          <div className="acv-kpi-value acv-kpi-value-success">{formatKgAbbrev(realizadoTotal)} kg</div>
          <Badge variant="success">{formatPct(pctTotal)}</Badge>
        </div>
        <ProgressBar percent={pctTotal !== null ? pctTotal * 100 : 0} variant="success" size="sm" />
        <div className="acv-kpi-caption">{formatPct(pctTotal)} da meta atingida</div>
      </div>

      <div className="acv-kpi-card">
        <div className="acv-kpi-card-head">
          <span className="acv-kpi-icon acv-kpi-icon-danger">
            <IconGlyph src={faltaMetaIcon} size={18} />
          </span>
          <div>
            <div className="acv-kpi-label">Falta para a Meta</div>
            <div className="acv-kpi-subtitle">Volume de vendas (kg)</div>
          </div>
        </div>
        <div className="acv-kpi-value acv-kpi-value-danger">{formatKgAbbrev(faltaTotal)} kg</div>
        <div className="acv-kpi-caption">Ainda precisamos vender</div>
      </div>

      <div className="acv-kpi-card">
        <div className="acv-kpi-card-head">
          <span className="acv-kpi-icon acv-kpi-icon-warning">
            <IconGlyph src={ritmoNecessarioIcon} size={18} />
          </span>
          <div>
            <div className="acv-kpi-label">Meta Diária</div>
            <div className="acv-kpi-subtitle">Por dia útil</div>
          </div>
        </div>
        <div className="acv-kpi-value">
          {ritmoTotal !== null ? `${formatKgAbbrev(ritmoTotal)} kg/dia útil` : "—"}
        </div>
        <div className="acv-kpi-caption">Para bater a meta no prazo</div>
      </div>
    </div>
  );
}

function GroupCard({ group }: { group: AcvGroupResult }) {
  const [expanded, setExpanded] = useState(false);
  const volumePercent = group.pct !== null ? group.pct * 100 : 0;
  const badge = groupBadge(group);
  const iconSrc = groupIconSrc(group.group_nome);
  const subgruposTone: "success" | "danger" | "neutral" =
    group.pct_subgrupos === null ? "neutral" : group.subgrupos_atingiu ? "success" : "danger";

  return (
    <Card className="acv-group-card">
      <div className="acv-group-card-head">
        <span className={`acv-group-icon acv-group-icon-${badge.tone}`}>
          {iconSrc ? <IconGlyph src={iconSrc} size={22} /> : <Package2 size={20} />}
        </span>
        <div className="acv-group-titles">
          <div className="acv-group-name">{group.group_nome}</div>
          <div className="acv-group-subtitle">Volume de vendas (kg)</div>
        </div>
        {group.meta_kg > 0 && (
          <div className="acv-group-volume-inline">
            <div className="acv-group-volume-row acv-group-volume-row-bar">
              <span className="acv-group-volume-label">
                <BarChart3 size={12} /> Volume
              </span>
              <span className={`acv-group-volume-pct acv-metric-value-${STATUS_TONE[group.status]}`}>
                {formatPct(group.pct)}
              </span>
              <div className="acv-group-volume-bar">
                <GoalProgressBar
                  percent={volumePercent}
                  variant={STATUS_TONE[group.status]}
                  goalPercent={VOLUME_MINIMO_PCT}
                  label={`Volume ${group.group_nome}: ${formatPct(group.pct)}, meta ${VOLUME_MINIMO_PCT}%`}
                />
              </div>
            </div>
            <div className="acv-group-volume-row">
              <span className="acv-group-volume-label">Subgrupos</span>
              <span className={`acv-group-volume-pct acv-metric-value-${subgruposTone}`}>
                {formatPct(group.pct_subgrupos)}
              </span>
              <span className="acv-group-volume-goal">
                {group.subgrupos_com_meta > 0
                  ? `${group.subgrupos_atingidos} de ${group.subgrupos_com_meta} atingidos`
                  : "sem subgrupos com meta"}
              </span>
            </div>
          </div>
        )}
        <Badge variant={badge.tone} icon={badgeIcon(badge.tone)}>
          {badge.label}
        </Badge>
      </div>

      {group.meta_kg > 0 && (
        <div className="acv-group-figures">
          <div className="acv-group-figure">
            <span className="acv-group-figure-label">Meta</span>
            <span className="acv-group-figure-value">{formatKg(group.meta_kg)}</span>
          </div>
          <div className="acv-group-figure">
            <span className="acv-group-figure-label">Realizado</span>
            <span className="acv-group-figure-value">{formatKg(group.realizado_kg)}</span>
          </div>
          <div className="acv-group-figure">
            <span className="acv-group-figure-label">Falta</span>
            <span
              className={`acv-group-figure-value ${group.faltam_kg > 0 ? "acv-group-figure-value-danger" : "acv-group-figure-value-success"}`}
            >
              {formatKg(group.faltam_kg)}
            </span>
          </div>
          <div className="acv-group-figure">
            <span className="acv-group-figure-label">Meta diária</span>
            <span className="acv-group-figure-value">
              {group.ritmo_kg_dia_util !== null ? `${formatKg(group.ritmo_kg_dia_util)}/dia útil` : "—"}
            </span>
          </div>
          <div className="acv-group-figure">
            <span className="acv-group-figure-label">Tendência de faturamento</span>
            <span
              className={`acv-group-figure-value ${
                group.tendencia_kg === null
                  ? ""
                  : group.tendencia_kg >= group.meta_kg
                    ? "acv-group-figure-value-success"
                    : "acv-group-figure-value-danger"
              }`}
            >
              {group.tendencia_kg !== null ? formatKg(group.tendencia_kg) : "—"}
            </span>
          </div>
        </div>
      )}

      {group.subgrupos.length > 0 && (
        <div className="acv-group-subgroup-wrap">
          <button type="button" className="acv-subgroup-toggle" onClick={() => setExpanded((value) => !value)}>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {expanded ? "Ocultar subgrupos" : `Ver ${group.subgrupos.length} subgrupo(s)`}
          </button>
          {expanded && (
            <div className="table-wrap acv-member-subgroup-table-wrap">
              <table className="table acv-subgroup-table">
                <thead>
                  <tr>
                    <th>Subgrupo</th>
                    <th>Meta</th>
                    <th>Realizado</th>
                    <th>%</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {group.subgrupos.map((row) => (
                    <tr key={row.subgroup_id}>
                      <td>{row.subgroup_nome}</td>
                      <td>{row.meta_kg > 0 ? formatKg(row.meta_kg) : "—"}</td>
                      <td>{formatKg(row.realizado_kg)}</td>
                      <td>{formatPct(row.pct)}</td>
                      <td>
                        {row.atingiu === null ? (
                          <Badge variant="neutral">sem meta</Badge>
                        ) : row.atingiu ? (
                          <Badge variant="success">Atingiu</Badge>
                        ) : (
                          <Badge variant="danger">Não atingiu</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// Mesma ideia dos cards de grupo (Volume e Subgrupos com o mesmo peso, cada um colorido pelo
// próprio status) — aqui compactada em duas linhas dentro da célula da tabela de equipe.
function TeamGroupCell({ summary }: { summary: AcvTeamGroupSummary | undefined }) {
  if (!summary) return <span className="acv-team-cell-empty">—</span>;

  const subgruposTone: "success" | "danger" | "neutral" =
    summary.pct_subgrupos === null ? "neutral" : summary.subgrupos_atingiu ? "success" : "danger";

  return (
    <div className="acv-team-cell">
      <div className={`acv-team-cell-line acv-team-cell-${STATUS_TONE[summary.status]}`}>
        <span className="acv-team-cell-tag">Volume</span>
        <span className="acv-team-cell-value">{formatPct(summary.pct)}</span>
      </div>
      <div className={`acv-team-cell-line acv-team-cell-${subgruposTone}`}>
        <span className="acv-team-cell-tag">Subgrupos</span>
        <span className="acv-team-cell-value">{formatPct(summary.pct_subgrupos)}</span>
      </div>
    </div>
  );
}

// Detalhe de um grupo (Frangos/Revenda) dentro do resumo expandido de uma pessoa da equipe —
// mesma informação que já existiu no card de grupo do topo (métricas + tabela de subgrupo), só
// que agora vive aqui, por pessoa, em vez de duplicar por grupo lá em cima.
function MemberGroupDetail({ group }: { group: AcvGroupResult }) {
  const [expanded, setExpanded] = useState(false);
  if (group.meta_kg <= 0) return null;

  const subgruposTone: "success" | "danger" | "neutral" =
    group.pct_subgrupos === null ? "neutral" : group.subgrupos_atingiu ? "success" : "danger";
  const cardTone = STATUS_TONE[group.status];

  return (
    <div className={`acv-member-group acv-member-group-${cardTone}`}>
      <div className="acv-member-group-head">
        <span className="acv-member-group-name">{group.group_nome}</span>
      </div>
      <div className="acv-member-group-stats">
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Volume</span>
          <span className={`acv-member-stat-value acv-metric-value-${cardTone}`}>{formatPct(group.pct)}</span>
          <span className="acv-member-stat-caption">meta {VOLUME_MINIMO_PCT}%</span>
        </div>
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Subgrupos</span>
          <span className={`acv-member-stat-value acv-metric-value-${subgruposTone}`}>
            {formatPct(group.pct_subgrupos)}
          </span>
          <span className="acv-member-stat-caption">
            {group.subgrupos_com_meta > 0
              ? `${group.subgrupos_atingidos} de ${group.subgrupos_com_meta} atingidos`
              : "sem subgrupos com meta"}
          </span>
        </div>
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Meta</span>
          <span className="acv-member-stat-value">{formatKg(group.meta_kg)}</span>
        </div>
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Realizado</span>
          <span className="acv-member-stat-value">{formatKg(group.realizado_kg)}</span>
        </div>
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Falta</span>
          <span
            className={`acv-member-stat-value ${group.faltam_kg > 0 ? "acv-metric-value-danger" : "acv-metric-value-success"}`}
          >
            {formatKg(group.faltam_kg)}
          </span>
        </div>
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Meta diária</span>
          <span className="acv-member-stat-value">
            {group.ritmo_kg_dia_util !== null ? `${formatKg(group.ritmo_kg_dia_util)}/dia útil` : "—"}
          </span>
        </div>
        <div className="acv-member-stat">
          <span className="acv-member-stat-label">Tendência de faturamento</span>
          <span
            className={`acv-member-stat-value ${
              group.tendencia_kg === null
                ? ""
                : group.tendencia_kg >= group.meta_kg
                  ? "acv-metric-value-success"
                  : "acv-metric-value-danger"
            }`}
          >
            {group.tendencia_kg !== null ? formatKg(group.tendencia_kg) : "—"}
          </span>
        </div>
      </div>
      {group.subgrupos.length > 0 && (
        <button type="button" className="acv-subgroup-toggle" onClick={() => setExpanded((value) => !value)}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {expanded ? "Ocultar subgrupos" : `Ver ${group.subgrupos.length} subgrupo(s)`}
        </button>
      )}
      {expanded && group.subgrupos.length > 0 && (
        <div className="table-wrap acv-member-subgroup-table-wrap">
          <table className="table acv-subgroup-table">
            <thead>
              <tr>
                <th>Subgrupo</th>
                <th>Meta</th>
                <th>Realizado</th>
                <th>%</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {group.subgrupos.map((row) => (
                <tr key={row.subgroup_id}>
                  <td>{row.subgroup_nome}</td>
                  <td>{row.meta_kg > 0 ? formatKg(row.meta_kg) : "—"}</td>
                  <td>{formatKg(row.realizado_kg)}</td>
                  <td>{formatPct(row.pct)}</td>
                  <td>
                    {row.atingiu === null ? (
                      <Badge variant="neutral">sem meta</Badge>
                    ) : row.atingiu ? (
                      <Badge variant="success">Atingiu</Badge>
                    ) : (
                      <Badge variant="danger">Não atingiu</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Cache em memória (só dura a sessão da aba, some no reload) do resultado por pessoa da equipe —
// evita reconsultar a API toda vez que a mesma linha é colapsada e reaberta (o painel desmonta e
// remontava do zero a cada toggle, refazendo o mesmo request e piscando o spinner de novo).
// Chaveado por ciclo+nó pra nunca misturar dados de ciclos diferentes.
const teamDetailCache = new Map<string, AccumulatedSalesResult>();

function teamDetailCacheKey(cycleId: number, nodeId: number): string {
  return `${cycleId}:${nodeId}`;
}

// Painel expandido de uma pessoa da equipe: carrega sob demanda (só quando a linha é aberta) o
// resultado completo daquele nó — mesma rota usada pra tela inteira, só que aqui embutido inline —
// e mostra o resumo por grupo (volume/subgrupos) dela mesma. Não aninha a equipe dela (o próximo
// nível hierárquico) por padrão: o card "Equipe" é só o 1º grau abaixo de quem está logado.
//
// Única exceção (Decisão O5, revisão 2026-09-17): quando essa linha é uma posição que o próprio
// usuário logado também ocupa (ex.: um Coordenador que também é Supervisor de um dos seus próprios
// Supervisores — um login só pros dois cargos), aparece uma opção extra pra ele ver os Vendedores
// dessa posição, já que ninguém mais tem como chegar lá por outra linha da equipe. Pra qualquer
// outra pessoa (ex.: o Gerente olhando a mesma linha), essa opção não aparece — só o resumo de
// grupo, igual a qualquer outra linha.
function TeamMemberPanel({ nodeId, cycleId }: { nodeId: number; cycleId: number }) {
  const { user } = useAuth();
  const cacheKey = teamDetailCacheKey(cycleId, nodeId);
  const [detail, setDetail] = useState<AccumulatedSalesResult | null>(() => teamDetailCache.get(cacheKey) ?? null);
  const [loading, setLoading] = useState(() => !teamDetailCache.has(cacheKey));
  const [error, setError] = useState<string | null>(null);
  const [showOwnTeam, setShowOwnTeam] = useState(false);

  useEffect(() => {
    const cached = teamDetailCache.get(cacheKey);
    if (cached) {
      setDetail(cached);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .get<AccumulatedSalesResult>(`/allocations/results/acumulado-vendas/?cycle=${cycleId}&node=${nodeId}`)
      .then((data) => {
        if (cancelled) return;
        teamDetailCache.set(cacheKey, data);
        setDetail(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Não foi possível carregar o resumo desta pessoa.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cacheKey, cycleId, nodeId]);

  if (loading) return <Spinner />;
  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!detail) return null;

  const gruposComMeta = detail.grupos.filter((group) => group.meta_kg > 0);
  const isOwnPosition = user?.hierarchy_nodes.some((node) => node.id === nodeId) ?? false;

  return (
    <div className="acv-member-panel">
      {gruposComMeta.length > 0 ? (
        <div className="acv-member-groups">
          {gruposComMeta.map((group) => (
            <MemberGroupDetail key={group.group_id} group={group} />
          ))}
        </div>
      ) : (
        <div className="acv-member-panel-empty">Sem dados de meta para este nível.</div>
      )}

      {isOwnPosition && detail.equipe.length > 0 && (
        <div className="acv-member-subteam-wrap">
          <button type="button" className="acv-subgroup-toggle" onClick={() => setShowOwnTeam((value) => !value)}>
            {showOwnTeam ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {showOwnTeam ? "Ocultar meus vendedores" : "Ver meus vendedores"}
          </button>
          {showOwnTeam && (
            <div className="acv-member-subteam">
              <TeamTable equipe={detail.equipe} groups={detail.grupos} cycleId={cycleId} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TeamRow({
  member,
  groups,
  cycleId,
}: {
  member: AcvTeamMemberResult;
  groups: AcvGroupResult[];
  cycleId: number;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <tr className="acv-team-row" onClick={() => setExpanded((value) => !value)}>
        <td>
          <span className="acv-team-row-name">
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            {member.node_nome}
          </span>
        </td>
        {groups.map((group) => {
          const summary = member.grupos.find((g) => g.group_id === group.group_id);
          return (
            <td key={group.group_id}>
              <TeamGroupCell summary={summary} />
            </td>
          );
        })}
        <td>
          <StatusBadge member={member} />
        </td>
      </tr>
      {expanded && (
        <tr className="acv-team-detail-row">
          <td colSpan={groups.length + 2}>
            <TeamMemberPanel nodeId={member.node_id} cycleId={cycleId} />
          </td>
        </tr>
      )}
    </>
  );
}

function TeamTable({
  equipe,
  groups,
  cycleId,
}: {
  equipe: AcvTeamMemberResult[];
  groups: AcvGroupResult[];
  cycleId: number;
}) {
  return (
    <div className="table-wrap">
      <table className="table acv-team-table">
        <thead>
          <tr>
            <th>Nome</th>
            {groups.map((group) => (
              <th key={group.group_id}>{group.group_nome}</th>
            ))}
            <th>Status geral</th>
          </tr>
        </thead>
        <tbody>
          {equipe.map((member) => (
            <TeamRow key={member.node_id} member={member} groups={groups} cycleId={cycleId} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AcumuladoVendasPage() {
  const { cycles, selectedCycleId, setSelectedCycleId, setSelectedNodeId, parentNode, result, loading, error } =
    useAcumuladoVendas();

  return (
    <section className="acv-page">
      <div className="acv-toolbar">
        <CycleSelect cycles={cycles} value={selectedCycleId} onChange={setSelectedCycleId} />
        {parentNode && (
          <button type="button" className="acv-back-button" onClick={() => setSelectedNodeId(parentNode.id)}>
            ← Voltar para {parentNode.nome}
          </button>
        )}
      </div>

      {error && <Alert variant="danger">{error}</Alert>}
      {loading && <Spinner />}

      {!loading && result && (
        <>
          <div className="acv-header-row">
            <h2 className="acv-node-title">{result.node_nome}</h2>
            {result.dias_uteis_restantes > 0 && (
              <span className="acv-dias-uteis">
                {result.dias_uteis_restantes} dia(s) útil(eis) restante(s) no ciclo
              </span>
            )}
          </div>

          {result.grupos.length === 0 ? (
            <EmptyState icon={<TrendingUp size={28} strokeWidth={1.5} />}>
              Nenhum grupo de produto cadastrado.
            </EmptyState>
          ) : (
            <>
              <TotalSummaryRow grupos={result.grupos} diasUteisRestantes={result.dias_uteis_restantes} />
              <div className="acv-group-grid">
                {result.grupos.map((group) => (
                  <GroupCard key={group.group_id} group={group} />
                ))}
              </div>
            </>
          )}

          {result.equipe.length > 0 && (
            <Card title="Equipe" subtitle="Ordenada por quem mais precisa de atenção.">
              <TeamTable equipe={result.equipe} groups={result.grupos} cycleId={result.cycle_id} />
            </Card>
          )}
        </>
      )}
    </section>
  );
}
