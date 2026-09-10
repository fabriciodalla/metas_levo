import { Download, Eraser, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Cycle, CycleCompleteness, SyncResult, VendorGroupSummary } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { Modal } from "../../components/ui/Modal";

function formatKg(value: number): string {
  return value.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + " kg";
}

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

function CloseCycleAction({ cycle, onClosed }: { cycle: Cycle; onClosed: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [completeness, setCompleteness] = useState<CycleCompleteness | null>(null);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function openConfirm() {
    setConfirming(true);
    setCompleteness(null);
    void api.get<CycleCompleteness>(`/cycles/${cycle.id}/completeness/`).then(setCompleteness);
  }

  async function handleConfirm() {
    setClosing(true);
    setError(null);
    try {
      await api.post<Cycle>(`/cycles/${cycle.id}/close/`, { force: true });
      setConfirming(false);
      onClosed();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao fechar o ciclo.");
    } finally {
      setClosing(false);
    }
  }

  const stuckKg = completeness?.stuck_allocations.reduce((sum, item) => sum + item.quantity_kg, 0) ?? 0;

  return (
    <>
      <Button type="button" variant="outline" size="sm" onClick={openConfirm}>
        Fechar
      </Button>
      {confirming && (
        <Modal
          title={`Fechar o ciclo ${String(cycle.mes).padStart(2, "0")}/${cycle.ano}?`}
          onClose={() => setConfirming(false)}
        >
          <p>Essa ação não pode ser desfeita.</p>
          {completeness && !completeness.complete && (
            <Alert variant="warning">
              Ciclo incompleto: {completeness.stuck_allocations.length} alocação(ões) presa(s)
              totalizando {stuckKg} kg não vão chegar ao Vendedor se você fechar assim mesmo.
            </Alert>
          )}
          {error && (
            <Alert variant="danger" role="alert">
              {error}
            </Alert>
          )}
          <div className="field-group mt-4">
            <Button variant="danger" onClick={() => void handleConfirm()} disabled={closing}>
              {closing ? "Fechando…" : "Fechar ciclo"}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)} disabled={closing}>
              Cancelar
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

function OpenCycleCard({ cycles, onOpened }: { cycles: Cycle[]; onOpened: () => void }) {
  const now = new Date();
  const [ano, setAno] = useState(now.getFullYear());
  const [mes, setMes] = useState(now.getMonth() + 1);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const existingKeys = useMemo(() => new Set(cycles.map((c) => `${c.ano}-${c.mes}`)), [cycles]);
  const alreadyExists = existingKeys.has(`${ano}-${mes}`);

  async function handleOpen() {
    setOpening(true);
    setError(null);
    try {
      await api.post<Cycle>("/cycles/open/", { ano, mes });
      onOpened();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao abrir o ciclo.");
    } finally {
      setOpening(false);
    }
  }

  return (
    <Card title="Ciclos">
      <p>
        Abre o ciclo do mês em que a distribuição de metas vai rodar. Só é possível abrir um mês
        que ainda não tem ciclo cadastrado.
      </p>
      <div className="field-group">
        <div className="field">
          <label className="field-label" htmlFor="new-cycle-mes">
            Mês
          </label>
          <select id="new-cycle-mes" value={mes} onChange={(e) => setMes(Number(e.target.value))}>
            {MESES.map((nome, index) => (
              <option key={nome} value={index + 1}>
                {nome}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="new-cycle-ano">
            Ano
          </label>
          <input
            id="new-cycle-ano"
            type="number"
            value={ano}
            onChange={(e) => setAno(Number(e.target.value))}
          />
        </div>
      </div>
      {alreadyExists && (
        <p className="field-hint">
          Já existe um ciclo para {String(mes).padStart(2, "0")}/{ano}.
        </p>
      )}
      <Button onClick={() => void handleOpen()} disabled={opening || alreadyExists}>
        <Plus size={16} />
        {opening ? "Abrindo…" : "Abrir novo ciclo"}
      </Button>
      {error && (
        <div className="mt-4">
          <Alert variant="danger" role="alert">
            {error}
          </Alert>
        </div>
      )}

      <div className="table-wrap mt-4">
        <table className="table">
          <thead>
            <tr>
              <th>Ciclo</th>
              <th>Status</th>
              <th>Aberto em</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {cycles.length === 0 && (
              <tr>
                <td colSpan={4} className="table-empty-cell">
                  Nenhum ciclo cadastrado.
                </td>
              </tr>
            )}
            {cycles.map((cycle) => (
              <tr key={cycle.id}>
                <td>
                  {String(cycle.mes).padStart(2, "0")}/{cycle.ano}
                </td>
                <td>
                  <Badge variant={cycle.status === "ABERTO" ? "success" : "neutral"}>
                    {cycle.status === "ABERTO" ? "aberto" : "fechado"}
                  </Badge>
                </td>
                <td>{new Date(cycle.created_at).toLocaleDateString("pt-BR")}</td>
                <td>{cycle.status === "ABERTO" && <CloseCycleAction cycle={cycle} onClosed={onOpened} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

interface VendorSummaryFlatRow {
  vendedorId: number;
  vendedorNome: string;
  mapeado: boolean;
  localId: number | null;
  localNome: string | null;
  supervisorId: number | null;
  supervisorNome: string | null;
  grupoNome: string | null;
  avg3: number | null;
  avg12: number | null;
}

type MediaFilter = "all" | "zero" | "nonzero";

function VendorGroupSummaryCard({ refreshToken }: { refreshToken: number }) {
  const [summary, setSummary] = useState<VendorGroupSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [localFilter, setLocalFilter] = useState<number | "all">("all");
  const [supervisorFilter, setSupervisorFilter] = useState<number | "all">("all");
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function handleDownload() {
    setDownloadError(null);
    try {
      await api.download("/sales-history/vendor-subgroup-export/", "resumo_vendedor_subgrupo.csv");
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : "Falha ao baixar o arquivo.");
    }
  }

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .get<VendorGroupSummary>("/sales-history/vendor-group-summary/")
      .then(setSummary)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Falha ao carregar o resumo."))
      .finally(() => setLoading(false));
  }, [refreshToken]);

  const allRows: VendorSummaryFlatRow[] = useMemo(() => {
    if (!summary) return [];
    return summary.vendedores.flatMap((vendedor): VendorSummaryFlatRow[] => {
      const base = {
        vendedorId: vendedor.id,
        vendedorNome: vendedor.nome,
        localId: vendedor.local_id,
        localNome: vendedor.local_nome,
        supervisorId: vendedor.supervisor_id,
        supervisorNome: vendedor.supervisor_nome,
      };
      if (!vendedor.mapeado) {
        return [{ ...base, mapeado: false, grupoNome: null, avg3: null, avg12: null }];
      }
      return summary.grupos.map((grupo) => {
        const total = vendedor.totals.find((t) => t.grupo_id === grupo.id);
        return {
          ...base,
          mapeado: true,
          grupoNome: grupo.nome,
          avg3: total?.avg_3_months_kg ?? 0,
          avg12: total?.avg_12_months_kg ?? 0,
        };
      });
    });
  }, [summary]);

  const locals = useMemo(() => {
    const map = new Map<number, string>();
    for (const row of allRows) {
      if (row.localId !== null && row.localNome !== null) map.set(row.localId, row.localNome);
    }
    return Array.from(map, ([id, nome]) => ({ id, nome })).sort((a, b) => a.nome.localeCompare(b.nome));
  }, [allRows]);

  const supervisors = useMemo(() => {
    const map = new Map<number, string>();
    for (const row of allRows) {
      if (row.supervisorId === null || row.supervisorNome === null) continue;
      if (localFilter !== "all" && row.localId !== localFilter) continue;
      map.set(row.supervisorId, row.supervisorNome);
    }
    return Array.from(map, ([id, nome]) => ({ id, nome })).sort((a, b) => a.nome.localeCompare(b.nome));
  }, [allRows, localFilter]);

  function handleLocalFilterChange(value: string) {
    const nextLocalId = value === "all" ? "all" : Number(value);
    setLocalFilter(nextLocalId);
    if (nextLocalId === "all") return;
    const supervisorStillValid = allRows.some(
      (row) => row.supervisorId === supervisorFilter && row.localId === nextLocalId
    );
    if (!supervisorStillValid) setSupervisorFilter("all");
  }

  const hasActiveFilters = localFilter !== "all" || supervisorFilter !== "all" || mediaFilter !== "all";

  function clearFilters() {
    setLocalFilter("all");
    setSupervisorFilter("all");
    setMediaFilter("all");
  }

  const rows = useMemo(() => {
    return allRows.filter((row) => {
      if (localFilter !== "all" && row.localId !== localFilter) return false;
      if (supervisorFilter !== "all" && row.supervisorId !== supervisorFilter) return false;
      if (mediaFilter === "zero" && !(row.avg3 === null || row.avg3 === 0)) return false;
      if (mediaFilter === "nonzero" && !(row.avg3 !== null && row.avg3 > 0)) return false;
      return true;
    });
  }, [allRows, localFilter, supervisorFilter, mediaFilter]);

  const missingCount = summary?.vendedores.filter((v) => !v.mapeado).length ?? 0;

  return (
    <Card
      title="Resumo por vendedor e grupo"
      subtitle="Médias de venda (kg) segundo a base sincronizada, por Vendedor ativo x Grupo de produto"
      actions={
        <Button type="button" variant="outline" size="sm" onClick={() => void handleDownload()}>
          <Download size={14} /> Baixar base completa (CSV)
        </Button>
      }
    >
      {loading && <p>Carregando…</p>}
      {error && (
        <Alert variant="danger" role="alert">
          {error}
        </Alert>
      )}
      {downloadError && (
        <Alert variant="danger" role="alert">
          {downloadError}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {missingCount > 0 && (
            <div className="mb-3">
              <Alert variant="warning">
                {missingCount} vendedor(es) ativo(s) na hierarquia não aparecem na base
                sincronizada — provavelmente falta cadastrar/curar o mapeamento de nome externo
                (Django Admin) ou a pessoa não tem venda/carteira no ERP ainda.
              </Alert>
            </div>
          )}
          <div className="field-group mb-3">
            <div className="field">
              <label className="field-label" htmlFor="vendor-summary-local">
                Coordenador Local
              </label>
              <select
                id="vendor-summary-local"
                value={localFilter}
                onChange={(e) => handleLocalFilterChange(e.target.value)}
              >
                <option value="all">Todos</option>
                {locals.map((local) => (
                  <option key={local.id} value={local.id}>
                    {local.nome}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="vendor-summary-supervisor">
                Supervisor
              </label>
              <select
                id="vendor-summary-supervisor"
                value={supervisorFilter}
                onChange={(e) =>
                  setSupervisorFilter(e.target.value === "all" ? "all" : Number(e.target.value))
                }
              >
                <option value="all">Todos</option>
                {supervisors.map((supervisor) => (
                  <option key={supervisor.id} value={supervisor.id}>
                    {supervisor.nome}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="vendor-summary-media">
                Média 3 meses
              </label>
              <select
                id="vendor-summary-media"
                value={mediaFilter}
                onChange={(e) => setMediaFilter(e.target.value as MediaFilter)}
              >
                <option value="all">Todos</option>
                <option value="zero">Só sem média (zero)</option>
                <option value="nonzero">Só com média</option>
              </select>
            </div>
            <div className="field">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={clearFilters}
                disabled={!hasActiveFilters}
                title="Limpar filtros"
              >
                <Eraser size={16} /> Limpar filtros
              </Button>
            </div>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Vendedor</th>
                  <th>Coordenador Local</th>
                  <th>Supervisor</th>
                  <th>Grupo</th>
                  <th>Média 3 meses</th>
                  <th>Média 12 meses</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={6} className="table-empty-cell">
                      Nenhum vendedor encontrado com esses filtros.
                    </td>
                  </tr>
                )}
                {rows.map((row, index) => (
                  <tr key={`${row.vendedorId}-${row.grupoNome ?? index}`}>
                    <td>
                      {row.vendedorNome}
                      {!row.mapeado && (
                        <Badge variant="warning">
                          <TriangleAlert size={12} /> sem sincronização
                        </Badge>
                      )}
                    </td>
                    <td>{row.localNome ?? "—"}</td>
                    <td>{row.supervisorNome ?? "—"}</td>
                    <td>{row.grupoNome ?? "—"}</td>
                    <td>
                      {row.avg3 !== null && row.avg3 > 0 ? (
                        formatKg(row.avg3)
                      ) : (
                        <Badge variant="warning">0 kg</Badge>
                      )}
                    </td>
                    <td>{row.avg12 !== null ? formatKg(row.avg12) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

export function PreProcessamentoPage() {
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [summaryRefreshToken, setSummaryRefreshToken] = useState(0);

  function reloadCycles() {
    void api.get<Cycle[]>("/cycles/").then(setCycles);
  }

  useEffect(reloadCycles, []);

  async function handleSync() {
    setSyncing(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.post<SyncResult>("/sales-history/sync/");
      setResult(data);
      setSummaryRefreshToken((n) => n + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao sincronizar os dados.");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <section>
      <Card>
        <p>
          Sincroniza o acumulado de vendas e a carteira de clientes do ERP (últimos 12 meses) e
          reconstrói a base usada na sugestão automática de metas. Rode antes de abrir a
          distribuição do ciclo.
        </p>
        <Button onClick={() => void handleSync()} disabled={syncing}>
          <RefreshCw size={16} />
          {syncing ? "Sincronizando…" : "Sincronizar dados agora"}
        </Button>
        {error && (
          <div className="mt-4">
            <Alert variant="danger" role="alert">
              {error}
            </Alert>
          </div>
        )}
        {result && (
          <div className="mt-4">
            <Alert variant="success">
              Sincronizado desde {result.synced_since}: {result.accumulated_count} linha(s) de
              acumulado, {result.portfolio_count} cliente(s) na carteira, {result.baseline_count}{" "}
              linha(s) na base de distribuição.
            </Alert>
          </div>
        )}
      </Card>

      <VendorGroupSummaryCard refreshToken={summaryRefreshToken} />

      <OpenCycleCard cycles={cycles} onOpened={reloadCycles} />

      <Card title="Configurações adicionais">
        <p className="mb-0">
          <em>
            Em breve. Os mapeamentos de histórico de vendas (vendedor/subgrupo externos) continuam no{" "}
            <a href="/admin/" target="_blank" rel="noreferrer">
              Django Admin
            </a>{" "}
            por enquanto.
          </em>
        </p>
      </Card>
    </section>
  );
}
