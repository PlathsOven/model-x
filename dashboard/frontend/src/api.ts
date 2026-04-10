// Thin typed wrappers around the backend's /api/* endpoints.

import type {
  AgentTraces,
  AllTraces,
  PhaseRow,
  Episode,
  FillRow,
  LifetimeMetrics,
  MarketSummary,
  Metrics,
  OrderRow,
  Orderbook,
  PositionsResponse,
  QuoteRow,
  Timeseries,
} from "./types";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} — ${url}\n${body}`);
  }
  return (await res.json()) as T;
}

async function postJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
  return (await res.json()) as T;
}

// Helper: append market_id query string when provided.
function withMarket(url: string, marketId?: string | null): string {
  if (!marketId) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}market_id=${encodeURIComponent(marketId)}`;
}

export const api = {
  markets: () => getJson<MarketSummary[]>("/api/markets"),
  episode: (marketId?: string | null) =>
    getJson<Episode>(withMarket("/api/episode", marketId)),
  phases: (marketId?: string | null) =>
    getJson<PhaseRow[]>(withMarket("/api/phases", marketId)),
  fills: (
    opts?: {
      agent?: string;
      phase?: "MM" | "HF";
    },
    marketId?: string | null
  ) => {
    const qs = new URLSearchParams();
    if (opts?.agent) qs.set("agent", opts.agent);
    if (opts?.phase) qs.set("phase", opts.phase);
    const q = qs.toString();
    return getJson<FillRow[]>(
      withMarket(`/api/fills${q ? `?${q}` : ""}`, marketId)
    );
  },
  quotes: (phaseId?: string, marketId?: string | null) =>
    getJson<QuoteRow[]>(
      withMarket(
        `/api/quotes${phaseId ? `?phase_id=${encodeURIComponent(phaseId)}` : ""}`,
        marketId
      )
    ),
  orders: (phaseId?: string, marketId?: string | null) =>
    getJson<OrderRow[]>(
      withMarket(
        `/api/orders${phaseId ? `?phase_id=${encodeURIComponent(phaseId)}` : ""}`,
        marketId
      )
    ),
  orderbook: (phaseId: string, marketId?: string | null) =>
    getJson<Orderbook>(
      withMarket(`/api/orderbook/${encodeURIComponent(phaseId)}`, marketId)
    ),
  traces: () => getJson<AllTraces>("/api/traces"),
  agentTraces: (agent: string) =>
    getJson<AgentTraces & { account_id: string }>(
      `/api/traces/${encodeURIComponent(agent)}`
    ),
  metrics: (marketId?: string | null) =>
    getJson<Metrics>(withMarket("/api/metrics", marketId)),
  metricsLifetime: () => getJson<LifetimeMetrics>("/api/metrics/lifetime"),
  positions: (marketId?: string | null) =>
    getJson<PositionsResponse>(withMarket("/api/positions", marketId)),
  timeseries: (marketId?: string | null) =>
    getJson<Timeseries>(withMarket("/api/timeseries", marketId)),
  reload: () => postJson<{ ok: boolean }>("/api/reload"),
};
