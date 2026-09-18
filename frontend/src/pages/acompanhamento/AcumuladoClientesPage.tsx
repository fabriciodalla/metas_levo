import { useState } from "react";
import type { ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  Download,
  Package2,
  Percent,
  Scale,
  UserCheck,
  UserPlus,
  UserX,
  Users,
  Wallet,
} from "lucide-react";
import { api, ApiError } from "../../api/client";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ProgressBar } from "../../components/ui/ProgressBar";
import { Spinner } from "../../components/ui/Spinner";
import type { ClientGroupTicketMedio, ClientInactiveRow } from "../../api/types";
import frangoIcon from "../../assets/icon-frango.png";
import revendaIcon from "../../assets/icon-revenda.png";
import { ChildNodeSelect } from "./ChildNodeSelect";
import { MonthSelect } from "./MonthSelect";
import { useAcumuladoClientes } from "./useAcumuladoClientes";

// Ícone de imagem (glifo monocromático em PNG, ver pasta `icones/` na raiz do repo) recolorido via
// CSS mask — mesmo padrão de `AcumuladoVendasPage`.
function IconGlyph({ src, size = 16 }: { src: string; size?: number }) {
  return (
    <span
      className="acv-icon-glyph"
      style={{ width: size, height: size, WebkitMaskImage: `url(${src})`, maskImage: `url(${src})` }}
    />
  );
}

// Ícone específico só existe pra Frango/Revenda — qualquer outro grupo cadastrado no catálogo
// fica sem ícone (só o nome), sem precisar de arte nova.
function groupIconSrc(groupNome: string): string | null {
  const normalized = groupNome.trim().toUpperCase();
  if (normalized.startsWith("FRANGO")) return frangoIcon;
  if (normalized.startsWith("REVENDA")) return revendaIcon;
  return null;
}

// Cor própria por grupo (pedido do usuário, 2026-09-18: destacar Frangos e Revenda com cores
// diferentes no card de Ticket médio por volume) — mesmo critério de prefixo do groupIconSrc, só
// que resolvendo pra uma cor em vez de um ícone. Grupo sem cor própria cai no tom neutro.
function groupColorTone(groupNome: string): "accent" | "info" | "neutral" {
  const normalized = groupNome.trim().toUpperCase();
  if (normalized.startsWith("FRANGO")) return "accent";
  if (normalized.startsWith("REVENDA")) return "info";
  return "neutral";
}

const kgFormatter = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pctFormatter = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });

const MESES = [
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
  return `${kgFormatter.format(Math.round(value))} kg`;
}

function formatPct(value: number | null): string {
  return value === null ? "—" : `${pctFormatter.format(value * 100)}%`;
}

function formatUltimaCompra(row: ClientInactiveRow): string {
  if (row.ultima_compra_ano === null || row.ultima_compra_mes === null) return "Nunca comprou";
  return `${MESES[row.ultima_compra_mes - 1]}/${row.ultima_compra_ano}`;
}

// Uma linha por cliente (agrupado), com seta pra expandir os subgrupos da última compra — pedido
// do usuário, 2026-09-18: a tabela agrupada por cliente evita ficar gigante, o detalhe por
// subgrupo fica escondido até quem for olhar precisar dele. Mesmo padrão de linha expansível do
// Acumulado de Vendas (`acv-team-row`/`acv-team-detail-row`, ver AcumuladoVendasPage).
function ClienteSemCompraRow({ row }: { row: ClientInactiveRow }) {
  const [expanded, setExpanded] = useState(false);
  const hasItens = row.itens_ultima_compra.length > 0;

  return (
    <>
      <tr
        className={hasItens ? "acv-team-row" : undefined}
        onClick={hasItens ? () => setExpanded((value) => !value) : undefined}
      >
        <td>{row.client_code}</td>
        <td>
          <span className="acv-team-row-name">
            {hasItens && (expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
            {row.client_name.toUpperCase()}
          </span>
        </td>
        <td>{formatUltimaCompra(row)}</td>
        <td>{row.peso_ultima_compra_kg > 0 ? formatKg(row.peso_ultima_compra_kg) : "—"}</td>
      </tr>
      {expanded && hasItens && (
        <tr className="acv-team-detail-row">
          <td colSpan={4}>
            <div className="acv-kpi-group-split">
              {row.itens_ultima_compra.map((item) => (
                <div key={item.subgroup_name} className="acv-kpi-group-split-item acv-kpi-group-split-item-neutral">
                  <span className="acv-kpi-group-split-name">{item.subgroup_name}</span>
                  <span className="acv-kpi-group-split-value">{formatKg(item.peso_kg)}</span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// Legenda dos cards "Base Clientes" e "Clientes ativos" (revisão 2026-09-18, quinta volta): duas
// linhas — a base usada no cálculo (carteira acumulada até o mês passado, `clientes_ativos_mes_
// anterior` nos dois casos) numa linha, meta do ciclo e quanto falta na outra — em vez de uma
// frase só, pra ficar mais fácil de escanear. Falta = meta menos o valor atual, sem passar de zero.
function metaAbsolutaCaptionLines(atual: number, mesAnterior: number, meta: number): string[] {
  const falta = Math.max(0, meta - atual);
  return [
    `Base: ${kgFormatter.format(mesAnterior)} clientes até o mês passado`,
    `Meta do ciclo: ${kgFormatter.format(meta)} · Faltam ${kgFormatter.format(falta)} clientes`,
  ];
}

// Captação e Positivação são os 2 KPIs de pagamento (confirmado pelo usuário, 2026-09-18) — só
// eles têm meta nesta tela, então são os únicos com essa faixa de cor por atingimento. Os demais
// cards continuam informativos (KpiCard/KpiGroupSplitCard), sem essa lógica.
function metaTone(pctAtingido: number | null): "success" | "warning" | "danger" | "neutral" {
  if (pctAtingido === null) return "neutral";
  if (pctAtingido >= 1) return "success";
  if (pctAtingido >= 0.7) return "warning";
  return "danger";
}

// Mesmo padrão de `GoalProgressBar` do Acumulado de Vendas: barra 0-100 com uma régua vertical
// marcando onde a meta cai — dá pra ver de quanto falta mesmo quando o resultado já passou dela.
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

// Mesmo card usado no topo do Acumulado de Vendas (`.acv-kpi-*`, ver AcumuladoVendasPage) — é
// puramente visual, sem nada específico daquela tela, então reaproveita a mesma classe em vez de
// duplicar CSS.
function KpiCard({
  icon,
  tone,
  label,
  value,
  caption,
}: {
  icon: ReactNode;
  tone: "primary" | "success" | "danger" | "warning";
  label: string;
  value: string;
  caption?: string | string[];
}) {
  const captionLines = caption === undefined ? [] : Array.isArray(caption) ? caption : [caption];
  return (
    <div className="acv-kpi-card">
      <div className="acv-kpi-card-head">
        <span className={`acv-kpi-icon acv-kpi-icon-${tone}`}>{icon}</span>
        <div className="acv-kpi-label acv-kpi-label-lg">{label}</div>
      </div>
      <div className="acv-kpi-value">{value}</div>
      {captionLines.map((line, index) => (
        <div key={index} className="acv-kpi-caption">
          {line}
        </div>
      ))}
    </div>
  );
}

// Mesmo shell visual do KpiCard, mas com um valor por grupo de produto em vez de um valor único —
// Ticket médio por volume, que separa Frangos de Revenda.
function KpiGroupSplitCard({
  icon,
  tone,
  label,
  groups,
}: {
  icon: ReactNode;
  tone: "primary" | "success" | "danger" | "warning";
  label: string;
  groups: ClientGroupTicketMedio[];
}) {
  return (
    <div className="acv-kpi-card">
      <div className="acv-kpi-card-head">
        <span className={`acv-kpi-icon acv-kpi-icon-${tone}`}>{icon}</span>
        <div className="acv-kpi-label acv-kpi-label-lg">{label}</div>
      </div>
      <div className="acv-kpi-group-split">
        {groups.map((grupo) => {
          const iconSrc = groupIconSrc(grupo.grupo_nome);
          const groupTone = groupColorTone(grupo.grupo_nome);
          return (
            <div key={grupo.grupo_id} className={`acv-kpi-group-split-item acv-kpi-group-split-item-${groupTone}`}>
              <span className={`acv-kpi-group-split-icon acv-kpi-group-split-icon-${groupTone}`}>
                {iconSrc ? <IconGlyph src={iconSrc} size={16} /> : <Package2 size={14} />}
              </span>
              <span className="acv-kpi-group-split-name">{grupo.grupo_nome}</span>
              <span className="acv-kpi-group-split-value">
                {grupo.ticket_medio_kg !== null ? formatKg(grupo.ticket_medio_kg) : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function KpiCaptacaoCard({ captacao, meta }: { captacao: number; meta: number }) {
  const pctAtingido = meta > 0 ? captacao / meta : null;
  const tone = metaTone(pctAtingido);
  return (
    <div className="acv-kpi-card">
      <div className="acv-kpi-card-head">
        <span className={`acv-kpi-icon acv-kpi-icon-${tone === "neutral" ? "primary" : tone}`}>
          <UserPlus size={18} />
        </span>
        <div className="acv-kpi-label acv-kpi-label-lg">Captação</div>
      </div>
      <div className="acv-kpi-value-row">
        <div className="acv-kpi-value">{kgFormatter.format(captacao)}</div>
        <Badge variant={tone}>{formatPct(pctAtingido)}</Badge>
      </div>
      <ProgressBar percent={pctAtingido !== null ? pctAtingido * 100 : 0} variant={tone} size="sm" />
      <div className="acv-kpi-caption">Meta do ciclo: {kgFormatter.format(meta)} clientes</div>
    </div>
  );
}

function KpiPositivacaoCard({ pct, metaPct }: { pct: number | null; metaPct: number }) {
  const pctAtingido = pct !== null && metaPct > 0 ? pct / metaPct : null;
  const tone = metaTone(pctAtingido);
  return (
    <div className="acv-kpi-card">
      <div className="acv-kpi-card-head">
        <span className={`acv-kpi-icon acv-kpi-icon-${tone === "neutral" ? "primary" : tone}`}>
          <Percent size={18} />
        </span>
        <div className="acv-kpi-label acv-kpi-label-lg">Positivação</div>
      </div>
      <div className="acv-kpi-value-row">
        <div className="acv-kpi-value">{formatPct(pct)}</div>
        <Badge variant={tone}>{formatPct(pctAtingido)}</Badge>
      </div>
      <GoalProgressBar
        percent={pct !== null ? pct * 100 : 0}
        variant={tone}
        goalPercent={metaPct * 100}
        label={`Positivação: ${formatPct(pct)}, meta ${formatPct(metaPct)}`}
      />
      <div className="acv-kpi-caption">Meta do ciclo: {formatPct(metaPct)} da carteira</div>
    </div>
  );
}

export function AcumuladoClientesPage() {
  const {
    months,
    selectedPeriod,
    setSelectedPeriod,
    selectedNodeId,
    setSelectedNodeId,
    parentNode,
    children,
    result,
    loading,
    error,
  } = useAcumuladoClientes();
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function handleDownloadClientesSemCompra() {
    if (selectedNodeId === null) return;
    setDownloadError(null);
    setDownloading(true);
    try {
      await api.download(
        `/sales-history/results/acumulado-clientes/clientes-sem-compra-export/?ano=${selectedPeriod.ano}&mes=${selectedPeriod.mes}&node=${selectedNodeId}`,
        `clientes_sem_compra_${selectedNodeId}_${selectedPeriod.mes}-${selectedPeriod.ano}.csv`,
      );
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : "Falha ao baixar a planilha.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="acv-page">
      <div className="acv-toolbar">
        <MonthSelect months={months} value={selectedPeriod} onChange={setSelectedPeriod} />
        <ChildNodeSelect nodes={children} selectedNodeId={selectedNodeId} onSelect={setSelectedNodeId} />
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
          </div>

          <div className="acv-kpi-row acv-kpi-row-3col">
            <KpiCard
              icon={<Users size={18} />}
              tone="primary"
              label="Base Clientes"
              value={kgFormatter.format(result.carteira_total)}
              caption={metaAbsolutaCaptionLines(
                result.carteira_total,
                result.clientes_ativos_mes_anterior,
                result.base_clientes_meta,
              )}
            />
            <KpiCaptacaoCard captacao={result.captacao} meta={result.captacao_meta} />
            <KpiCard
              icon={<Wallet size={18} />}
              tone="success"
              label="Ticket médio"
              value={result.ticket_medio_kg !== null ? formatKg(result.ticket_medio_kg) : "—"}
            />
            <KpiCard
              icon={<UserCheck size={18} />}
              tone="primary"
              label="Clientes ativos"
              value={kgFormatter.format(result.clientes_ativos)}
              caption={metaAbsolutaCaptionLines(
                result.clientes_ativos,
                result.clientes_ativos_mes_anterior,
                result.clientes_ativos_meta,
              )}
            />
            <KpiPositivacaoCard pct={result.positivacao_pct} metaPct={result.positivacao_meta_pct} />
            <KpiGroupSplitCard
              icon={<Scale size={18} />}
              tone="primary"
              label="Ticket médio por volume"
              groups={result.ticket_medio_por_grupo}
            />
          </div>

          {result.clientes_sem_compra.length > 0 ? (
            <Card
              title="Clientes sem compra este mês"
              subtitle="Agrupado por cliente, ordenado pelo maior volume da última compra — clique na seta pra ver os subgrupos."
              actions={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleDownloadClientesSemCompra()}
                  disabled={downloading}
                >
                  <Download size={14} /> Baixar planilha (CSV)
                </Button>
              }
            >
              {downloadError && (
                <Alert variant="danger" role="alert">
                  {downloadError}
                </Alert>
              )}
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Clifor</th>
                      <th>Cliente</th>
                      <th>Última compra</th>
                      <th>Peso da última compra</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.clientes_sem_compra.map((row) => (
                      <ClienteSemCompraRow key={row.client_code} row={row} />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : (
            <EmptyState icon={<UserX size={28} strokeWidth={1.5} />}>
              Todos os clientes da carteira compraram neste ciclo.
            </EmptyState>
          )}
        </>
      )}
    </section>
  );
}
