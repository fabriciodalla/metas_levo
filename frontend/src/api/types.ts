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
  external_name: string;
  covered_node: number;
  covered_node_nome: string;
  ano: number;
  mes: number;
}

export interface FeristaCoverageInput {
  external_name: string;
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
