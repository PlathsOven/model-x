// Thin typed wrappers around the backend's /api/* endpoints.

import type {
  AgentTraces,
  AllTraces,
  CycleRow,
  Episode,
  FillRow,
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

export const api = {
  episode: () => getJson<Episode>("/api/episode"),
  cycles: () => getJson<CycleRow[]>("/api/cycles"),
  fills: (opts?: {
    agent?: string;
    phase?: "MM" | "HF";
    cycleMin?: number;
    cycleMax?: number;
  }) => {
    const qs = new URLSearchParams();
    if (opts?.agent) qs.set("agent", opts.agent);
    if (opts?.phase) qs.set("phase", opts.phase);
    if (opts?.cycleMin !== undefined) qs.set("cycle_min", String(opts.cycleMin));
    if (opts?.cycleMax !== undefined) qs.set("cycle_max", String(opts.cycleMax));
    const q = qs.toString();
    return getJson<FillRow[]>(`/api/fills${q ? `?${q}` : ""}`);
  },
  quotes: (cycleIndex?: number) =>
    getJson<QuoteRow[]>(
      `/api/quotes${cycleIndex !== undefined ? `?cycle_index=${cycleIndex}` : ""}`
    ),
  orders: (cycleIndex?: number) =>
    getJson<OrderRow[]>(
      `/api/orders${cycleIndex !== undefined ? `?cycle_index=${cycleIndex}` : ""}`
    ),
  orderbook: (cycleIndex: number) =>
    getJson<Orderbook>(`/api/orderbook/${cycleIndex}`),
  traces: () => getJson<AllTraces>("/api/traces"),
  agentTraces: (agent: string) =>
    getJson<AgentTraces & { account_id: string }>(
      `/api/traces/${encodeURIComponent(agent)}`
    ),
  metrics: () => getJson<Metrics>("/api/metrics"),
  positions: () => getJson<PositionsResponse>("/api/positions"),
  timeseries: () => getJson<Timeseries>("/api/timeseries"),
  reload: () => postJson<{ ok: boolean }>("/api/reload"),
};
