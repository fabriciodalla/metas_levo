export interface HierarchyNodeSummary {
  id: number;
  level: string;
  nome: string;
}

export interface User {
  id: number;
  username: string;
  email: string;
  is_admin: boolean;
  hierarchy_nodes: HierarchyNodeSummary[];
}

export interface HierarchyNode {
  id: number;
  level: string;
  level_display: string;
  parent: number | null;
  nome: string;
  ativo: boolean;
  is_representante: boolean;
}

export interface Cycle {
  id: number;
  ano: number;
  mes: number;
  status: "ABERTO" | "FECHADO";
  created_at: string;
  closed_at: string | null;
}

export type Granularity = "GROUP" | "SUBGROUP" | "PRODUCT";

export interface GoalAllocation {
  id: number;
  cycle: number;
  owner_node: number;
  owner_node_level: string;
  owner_node_nome: string;
  owner_node_usernames: string[];
  parent_allocation: number | null;
  granularity: Granularity;
  group: number | null;
  group_nome: string | null;
  subgroup: number | null;
  subgroup_nome: string | null;
  product: number | null;
  quantity_kg: number;
  distributed: boolean;
  has_further_distribution: boolean;
  criado_por: number;
  created_at: string;
  updated_at: string;
}

export interface ProductGroup {
  id: number;
  nome: string;
  ativo: boolean;
}

export interface ProductSubgroup {
  id: number;
  nome: string;
  group: number;
  ativo: boolean;
}

export interface StuckAllocation {
  allocation_id: number;
  owner_node_id: number;
  owner_node_level: string;
  quantity_kg: number;
}

export interface CycleCompleteness {
  complete: boolean;
  stuck_allocations: StuckAllocation[];
}

export interface ChildAllocationInput {
  owner_node_id: number;
  quantity_kg: number;
  granularity: Granularity;
  group_id?: number | null;
  subgroup_id?: number | null;
  product_id?: number | null;
}

export interface AllocationOverview {
  id: number;
  cycle: number;
  owner_node: number;
  owner_node_level: string;
  owner_node_nome: string;
  owner_node_parent_id: number | null;
  owner_node_parent_nome: string | null;
  owner_node_usernames: string[];
  parent_allocation: number | null;
  granularity: Granularity;
  group_nome: string | null;
  subgroup_nome: string | null;
  quantity_kg: number;
  distributed: boolean;
  criado_por_username: string;
  created_at: string;
  updated_at: string;
}

export interface VendedorAllocationRow {
  gerente: string;
  local: string;
  supervisor: string;
  vendedor: string;
  grupo: string;
  subgrupo: string;
  quantity_kg: number;
  status: "META" | "META AJUSTADA";
}

export interface SyncResult {
  synced_since: string;
  accumulated_count: number;
  portfolio_count: number;
  baseline_count: number;
}

export interface VendorGroupTotal {
  grupo_id: number;
  avg_3_months_kg: number;
  avg_12_months_kg: number;
}

export interface VendorGroupSummaryRow {
  id: number;
  nome: string;
  mapeado: boolean;
  em_ferias: boolean;
  supervisor_id: number | null;
  supervisor_nome: string | null;
  local_id: number | null;
  local_nome: string | null;
  totals: VendorGroupTotal[];
}

export interface VendorGroupSummary {
  grupos: { id: number; nome: string }[];
  vendedores: VendorGroupSummaryRow[];
}

export interface UserAccount {
  id: number;
  username: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  hierarchy_nodes: HierarchyNode[];
}

export interface MonthlyPoint {
  ano: number;
  mes: number;
  quantity_kg: number;
}

export interface GroupSuggestion {
  group_id: number;
  group_nome: string;
  trend_kg: number;
  seasonal_index: number;
  suggested_kg: number;
  has_gap: boolean;
  same_month_last_year_kg: number | null;
  history: MonthlyPoint[];
  already_created: boolean;
}

export interface ChildDistributionContext {
  owner_node_id: number;
  history: MonthlyPoint[];
  same_month_last_year_kg: number | null;
  /** Quando a alocação sendo distribuída é de um subgrupo específico (Meta Supervisor/Meta
   * Vendedor), este valor já é a média daquele subgrupo, não do grupo inteiro. */
  last_3_months_avg_kg: number | null;
  historical_share_pct: number | null;
  has_gap: boolean;
  suggested_kg: number | null;
}

export interface SubgroupDistributionContext {
  subgroup_id: number;
  subgroup_nome: string;
  history: MonthlyPoint[];
  same_month_last_year_kg: number | null;
  last_3_months_avg_kg: number | null;
  historical_share_pct: number | null;
  has_gap: boolean;
  suggested_kg: number | null;
}

export interface FeristaCoverage {
  id: number;
  covering_node: number;
  covering_node_nome: string;
  covered_node: number;
  covered_node_nome: string;
  ano: number;
  mes: number;
}

export interface FeristaCoverageInput {
  covering_node: number;
  covered_node: number;
  ano: number;
  mes: number;
}

export interface CreateRootAllocationInput {
  cycle_id: number;
  owner_node_id: number;
  granularity: Granularity;
  quantity_kg: number;
  group_id?: number | null;
  subgroup_id?: number | null;
  product_id?: number | null;
}

export interface UserAccountInput {
  username: string;
  email: string;
  password?: string;
  is_admin?: boolean;
  is_active?: boolean;
  level?: string | null;
  parent_node_id?: number | null;
}

// Representante: Vendedor sem usuário vinculado por design (sem acesso ao sistema) — mesmo papel
// na cascata de um Vendedor comum, só sem login. Ver CLAUDE.md / HierarchyNode.is_representante.
export interface RepresentanteInput {
  nome: string;
  parent: number | null;
  ativo?: boolean;
  is_representante?: true;
  level?: "VENDEDOR";
}

// Tela "Acompanhamento > Acumulado de Vendas" — GET /allocations/results/acumulado-vendas/.
export type AcvStatus = "VERDE" | "AMARELO" | "VERMELHO" | "SEM_META";

export interface AcvSubgroupResult {
  subgroup_id: number;
  subgroup_nome: string;
  meta_kg: number;
  realizado_kg: number;
  pct: number | null;
  atingiu: boolean | null;
}

export interface AcvGroupResult {
  group_id: number;
  group_nome: string;
  meta_kg: number;
  realizado_kg: number;
  pct: number | null;
  faltam_kg: number;
  status: AcvStatus;
  ritmo_kg_dia_util: number | null;
  tendencia_kg: number | null;
  same_month_last_year_kg: number | null;
  last_3_months_avg_kg: number | null;
  subgrupos_com_meta: number;
  subgrupos_atingidos: number;
  pct_subgrupos: number | null;
  subgrupos_atingiu: boolean | null;
  atingiu_grupo: boolean | null;
  subgrupos: AcvSubgroupResult[];
}

export interface AcvTeamGroupSummary {
  group_id: number;
  group_nome: string;
  meta_kg: number;
  realizado_kg: number;
  pct: number | null;
  status: AcvStatus;
  pct_subgrupos: number | null;
  subgrupos_atingiu: boolean | null;
  atingiu_grupo: boolean | null;
}

export interface AcvTeamMemberResult {
  node_id: number;
  node_nome: string;
  status_geral: AcvStatus;
  grupos: AcvTeamGroupSummary[];
}

export interface AccumulatedSalesResult {
  node_id: number;
  node_nome: string;
  cycle_id: number;
  cycle_ano: number;
  cycle_mes: number;
  dias_uteis_restantes: number;
  grupos: AcvGroupResult[];
  equipe: AcvTeamMemberResult[];
}

export interface ClientInactivePurchaseItem {
  subgroup_name: string;
  peso_kg: number;
}

export interface ClientInactiveRow {
  client_code: number;
  client_name: string;
  ultima_compra_ano: number | null;
  ultima_compra_mes: number | null;
  itens_ultima_compra: ClientInactivePurchaseItem[];
  peso_ultima_compra_kg: number;
}

export interface ClientGroupTicketMedio {
  grupo_id: number;
  grupo_nome: string;
  clientes_ativos: number;
  ticket_medio_kg: number | null;
}

export interface ClientAccumuladoResult {
  node_id: number;
  node_nome: string;
  ano: number;
  mes: number;
  carteira_total: number;
  clientes_ativos: number;
  clientes_ativos_mes_anterior: number;
  positivacao_pct: number | null;
  positivacao_meta_pct: number;
  captacao: number;
  captacao_meta: number;
  base_clientes_meta: number;
  clientes_ativos_meta: number;
  clientes_sem_compra_count: number;
  ticket_medio_kg: number | null;
  ticket_medio_por_grupo: ClientGroupTicketMedio[];
  clientes_sem_compra: ClientInactiveRow[];
}
