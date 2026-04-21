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

export type MarketState =
  | "RUNNING"
  | "PENDING_SETTLEMENT"
  | "SETTLED"
  | "PAUSED";

export interface Episode {
  contract: ContractInfo | null;
  market_state?: MarketState;
  phase_count?: number;
  last_phase_ts?: number;
  pending_mm?: number;
  phase_duration_seconds?: number | null;
  settled: boolean;
  accounts: AccountSummary[];
  stats: EpisodeStats;
  traces_loaded: boolean;
  sources: { db_path: string; traces_path: string };
  loaded: boolean;
  status: EpisodeStatus;
  status_detail: string | null;
  loaded_at: number;
  db_mtime: number;
  traces_mtime: number;
}

export interface MarketSummary {
  id: string;
  name: string;
  description: string;
  state: MarketState;
  phase_count: number;
  last_phase_ts: number;
  settlement_date: string | null;
  settlement_value: number | null;
  multiplier: number;
  settled: boolean;
}

export interface LifetimePerMarket {
  market_id: string;
  role: "MM" | "HF";
  total_pnl: number | null;
  sharpe: number | null;
  volume: number | null;
  settled_at: number | null;
}

export interface LifetimeAgent {
  account_id: string;
  name: string;
  markets_traded: number;
  total_pnl: number;
  total_volume: number;
  avg_sharpe: number;
  best_market_pnl: number;
  worst_market_pnl: number;
  per_market: LifetimePerMarket[];
}

export interface LifetimeMetrics {
  agents: Record<string, LifetimeAgent>;
}

export interface PhaseRow {
  phase_id: string;
  phase_type: "MM" | "HF";
  timestamp: number;
  phase: "OPEN" | "CLOSED";
  mark: number | null;
  num_quotes: number;
  num_orders: number;
  mm_fills: number;
  hf_fills: number;
  info: string | null;
}

export interface FillRow {
  id: string;
  phase_id: string;
  timestamp: number;
  phase: "MM" | "HF";
  buyer: string;
  seller: string;
  price: number;
  size: number;
  is_self_cross: boolean;
}

export interface QuoteRow {
  phase_id: string;
  timestamp: number;
  account_id: string;
  bid_price: number;
  bid_size: number;
  ask_price: number;
  ask_size: number;
}

export interface OrderRow {
  phase_id: string;
  timestamp: number;
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
  phase_id: string;
  phase_type: string;
  timestamp: number;
  phase: string;
  mark: number | null;
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
  phase_id: string;
  phase_type: string;
  timestamp: number;
  account_id: string;
  model: string;
  request: string;
  raw_response: string | null;
  parsed: {
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
  info_schedule?: Record<string, string>;
  agents?: Record<string, AgentTraces>;
}

export interface MMScoresDict {
  account_id: string;
  total_pnl: number;
  sharpe: number;
  volume: number;
  volume_share: number;
  pnl_bps: number;
  uptime: number;
  consensus: number;
  markout_2: number;
  markout_10: number;
  markout_40: number;
  avg_abs_position: number;
  self_cross_count: number;
  self_cross_volume: number;
}

export interface HFScoresDict {
  account_id: string;
  total_pnl: number;
  sharpe: number;
  markout_2: number;
  markout_10: number;
  markout_40: number;
}

export interface Metrics {
  settled: boolean;
  mm: Record<string, MMScoresDict>;
  hf: Record<string, HFScoresDict>;
}

export interface PositionPoint {
  timestamp: number;
  phase_type: string;
  position: number;
  cash: number;
  pnl_mtm: number;
  pnl_realized: number | null;
}

export interface PositionsResponse {
  agents: Record<string, PositionPoint[]>;
}

export interface TimeseriesRow {
  phase_id: string;
  phase_type: string;
  timestamp: number;
  phase: string;
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
  info: string | null;
  closed_at: number | null;
}

export interface TimeseriesFill {
  phase_id: string;
  timestamp: number;
  price: number;
  size: number;
  phase: "MM" | "HF";
  buyer: string;
  seller: string;
  is_self_cross: boolean;
}

export interface Timeseries {
  phases: TimeseriesRow[];
  fills: TimeseriesFill[];
  settlement: number | null;
  info_phases: string[];
  info_by_phase: Record<string, string>;
  mm_accounts: string[];
  hf_accounts: string[];
}
