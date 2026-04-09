// TypeScript interfaces that mirror the backend response shapes exactly.

export interface ContractInfo {
  id: string;
  name: string;
  description: string;
  multiplier: number;
  position_limit: number;
  settlement_value: number | null;
  settlement_date: string | null;
}

export interface AccountSummary {
  id: string;
  name: string;
  role: "MM" | "HF";
  model: string;
  final_position: number;
  final_pnl: number | null;
}

export interface EpisodeStats {
  total_fills: number;
  total_volume: number;
  mm_fills: number;
  hf_fills: number;
}

export type EpisodeStatus = "ok" | "db_missing" | "no_contracts" | "error";

export interface Episode {
  contract: ContractInfo | null;
  num_cycles: number;
  settled: boolean;
  accounts: AccountSummary[];
  stats: EpisodeStats;
  traces_loaded: boolean;
  sources: { db_path: string; traces_path: string };
  // Live-update status fields populated by /api/episode on every poll.
  loaded: boolean;
  status: EpisodeStatus;
  status_detail: string | null;
  loaded_at: number;
  db_mtime: number;
  traces_mtime: number;
}

export interface CycleRow {
  cycle_index: number;
  cycle_id: string;
  phase: "MM_OPEN" | "HF_OPEN" | "HF_CLOSED" | "MM_CLOSED";
  mm_mark: number | null;
  hf_mark: number | null;
  mark: number | null;
  num_quotes: number;
  num_orders: number;
  mm_fills: number;
  hf_fills: number;
  info: string | null;
}

export interface FillRow {
  id: string;
  cycle_index: number;
  cycle_id: string;
  phase: "MM" | "HF";
  buyer: string;
  seller: string;
  price: number;
  size: number;
  is_self_cross: boolean;
}

export interface QuoteRow {
  cycle_index: number;
  cycle_id: string;
  account_id: string;
  bid_price: number;
  bid_size: number;
  ask_price: number;
  ask_size: number;
}

export interface OrderRow {
  cycle_index: number;
  cycle_id: string;
  account_id: string;
  side: "buy" | "sell";
  size: number;
}

export interface BookLevel {
  account_id: string;
  side: "bid" | "ask";
  price: number;
  size: number;
}

export interface Orderbook {
  cycle_index: number;
  cycle_id: string;
  phase: string;
  mm_mark: number | null;
  hf_mark: number | null;
  quotes: {
    account_id: string;
    bid_price: number;
    bid_size: number;
    ask_price: number;
    ask_size: number;
  }[];
  mm_fills: FillRow[];
  residual_book: BookLevel[];
  orders: { account_id: string; side: "buy" | "sell"; size: number }[];
  hf_fills: FillRow[];
  positions_before: Record<string, number>;
  positions_after: Record<string, number>;
}

export interface TraceEntry {
  phase: "MM" | "HF";
  cycle_id: string;
  cycle_number: number;
  account_id: string;
  model: string;
  request: string;
  raw_response: string | null;
  parsed: {
    fair_value_estimate?: number;
    reasoning?: string;
    bid_price?: number;
    bid_size?: number;
    ask_price?: number;
    ask_size?: number;
    side?: "buy" | "sell" | "pass";
    size?: number;
  } | null;
  decision: {
    bid_price?: number;
    bid_size?: number;
    ask_price?: number;
    ask_size?: number;
    side?: string;
    size?: number;
  } | null;
  error: string | null;
}

export interface AgentTraces {
  model: string;
  role: "MM" | "HF";
  traces: TraceEntry[];
}

export interface AllTraces {
  loaded: boolean;
  contract?: any;
  num_cycles?: number;
  info_schedule?: Record<string, string>;
  agents?: Record<string, AgentTraces>;
}

export interface MMScoresDict {
  account_id: string;
  total_pnl: number | null;
  sharpe: number | null;
  volume: number;
  volume_share: number;
  pnl_bps: number | null;
  uptime: number;
  consensus: number;
  markout_1: number | null;
  markout_5: number | null;
  markout_20: number | null;
  avg_abs_position: number;
  self_cross_count: number;
  self_cross_volume: number;
}

export interface HFScoresDict {
  account_id: string;
  total_pnl: number | null;
  sharpe: number | null;
  markout_1: number | null;
  markout_5: number | null;
  markout_20: number | null;
}

export interface Metrics {
  settled: boolean;
  mm: Record<string, MMScoresDict>;
  hf: Record<string, HFScoresDict>;
}

export interface PositionPoint {
  cycle_index: number;
  position: number;
  cash: number;
  pnl_mtm: number;
  pnl_realized: number | null;
}

export interface PositionsResponse {
  agents: Record<string, PositionPoint[]>;
}

export interface TimeseriesRow {
  cycle_index: number;
  phase: string;
  mm_mark: number | null;
  hf_mark: number | null;
  mark: number | null;
  quotes_by_agent: Record<
    string,
    {
      bid_price: number;
      bid_size: number;
      ask_price: number;
      ask_size: number;
      mid: number;
    }
  >;
  fv_by_agent: Record<string, number>;
  info: string | null;
}

export interface TimeseriesFill {
  cycle_index: number;
  price: number;
  size: number;
  phase: "MM" | "HF";
  buyer: string;
  seller: string;
  is_self_cross: boolean;
}

export interface Timeseries {
  cycles: TimeseriesRow[];
  fills: TimeseriesFill[];
  settlement: number | null;
  info_cycles: number[];
  info_by_cycle: Record<string, string>;
  mm_accounts: string[];
  hf_accounts: string[];
}
