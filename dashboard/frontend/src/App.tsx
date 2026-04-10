import { useCallback, useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  BookOpen,
  Globe,
  Home,
  LineChart as LineChartIcon,
  ListTree,
  MessageSquare,
  RefreshCw,
  Trophy,
} from "lucide-react";
import { api } from "./api";
import type { Episode, EpisodeStatus, MarketSummary } from "./types";
import { EpisodeOverview } from "./components/EpisodeOverview";
import { TimeSeriesChart } from "./components/TimeSeriesChart";
import { TradeLog } from "./components/TradeLog";
import { OrderbookViewer } from "./components/OrderbookViewer";
import { ReasoningTraces } from "./components/ReasoningTraces";
import { PerformanceMetrics } from "./components/PerformanceMetrics";
import { PositionTracker } from "./components/PositionTracker";
import { LifetimeMetricsView } from "./components/LifetimeMetrics";

const POLL_INTERVAL_MS = 2000;

const STATUS_LABEL: Record<EpisodeStatus, string> = {
  ok: "Connected to a populated database.",
  db_missing: "Waiting for the database file to appear on disk.",
  no_contracts: "Database is open but contains no contracts yet.",
  error: "Backend hit an error reading the database.",
};

type ViewKey =
  | "overview"
  | "timeseries"
  | "tradelog"
  | "orderbook"
  | "traces"
  | "metrics"
  | "positions"
  | "lifetime";

interface NavItem {
  key: ViewKey;
  label: string;
  icon: LucideIcon;
}

const NAV: NavItem[] = [
  { key: "overview", label: "Overview", icon: Home },
  { key: "timeseries", label: "Time Series", icon: LineChartIcon },
  { key: "tradelog", label: "Trade Log", icon: ListTree },
  { key: "orderbook", label: "Orderbook", icon: BookOpen },
  { key: "metrics", label: "Metrics", icon: Trophy },
  { key: "positions", label: "Positions", icon: BarChart3 },
  { key: "traces", label: "Reasoning", icon: MessageSquare },
  { key: "lifetime", label: "Lifetime", icon: Globe },
];

export default function App() {
  const [active, setActive] = useState<ViewKey>("overview");
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [focusPhaseId, setFocusPhaseId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  // Multi-market state. `marketsList` is the dropdown source, `marketId`
  // is the currently-selected market (null = default to first).
  const [marketsList, setMarketsList] = useState<MarketSummary[]>([]);
  const [marketId, setMarketId] = useState<string | null>(null);

  // Keep a wall-clock tick going so the "updated Xs ago" string in the
  // sidebar stays current even when the backend hasn't changed.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  // Poll /api/episode every POLL_INTERVAL_MS. This is the only place that
  // talks to the backend at the App level — child views fetch their own
  // data via useEffect([dataVersion]).
  const loadEpisode = useCallback(async () => {
    try {
      const ep = await api.episode(marketId);
      setEpisode(ep);
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  }, [marketId]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      try {
        const [ep, mks] = await Promise.all([
          api.episode(marketId),
          api.markets().catch(() => [] as MarketSummary[]),
        ]);
        if (cancelled) return;
        setEpisode(ep);
        setMarketsList(mks);
        // If the user hasn't picked a market and we just discovered some,
        // implicitly use the first one — keeps the URL/state in sync with
        // what /api/episode returned.
        if (marketId === null && mks.length > 0 && ep.contract) {
          setMarketId(ep.contract.id);
        }
        setError(null);
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.message || String(e));
      }
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [marketId]);

  const handleReload = async () => {
    setReloading(true);
    try {
      await api.reload();
      await loadEpisode();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setReloading(false);
    }
  };

  const jumpToPhase = useCallback((phaseId: string, view?: ViewKey) => {
    setFocusPhaseId(phaseId);
    if (view) setActive(view);
  }, []);

  // dataVersion is the prop that all view components depend on for re-fetching.
  // Bumps whenever the backend reports a different load timestamp, which
  // happens after every successful auto-reload.
  const dataVersion = episode?.loaded_at ?? 0;

  // Sidebar status indicator: live (green) / waiting (amber) / error (red).
  const liveStatus = useMemo(() => {
    if (error) return { tone: "red" as const, label: "error", dot: "×" };
    if (!episode) return { tone: "zinc" as const, label: "loading", dot: "·" };
    if (episode.loaded) return { tone: "emerald" as const, label: "live", dot: "●" };
    return { tone: "amber" as const, label: "waiting", dot: "○" };
  }, [episode, error]);

  const updatedAgo = episode
    ? Math.max(0, Math.round(now - episode.loaded_at))
    : null;

  return (
    <div className="flex h-full min-h-screen bg-zinc-900 text-zinc-100">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 border-r border-zinc-800 bg-zinc-950/60 flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <Activity className="text-emerald-400" size={22} />
            <div>
              <div className="font-semibold tracking-tight">ModelX</div>
              <div className="text-[10px] uppercase tracking-widest text-zinc-500">
                Debug Dashboard
              </div>
            </div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-3">
          {NAV.map((item) => {
            const Icon = item.icon;
            const isActive = item.key === active;
            return (
              <button
                key={item.key}
                onClick={() => setActive(item.key)}
                className={
                  "flex w-full items-center gap-3 px-5 py-2 text-sm transition-colors " +
                  (isActive
                    ? "bg-zinc-800 text-white border-l-2 border-emerald-400"
                    : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100 border-l-2 border-transparent")
                }
              >
                <Icon size={16} />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="p-4 border-t border-zinc-800 space-y-2">
          {/* Live status pill */}
          <div
            className={
              "flex items-center gap-2 rounded px-2 py-1.5 text-[10px] uppercase tracking-widest font-medium border " +
              (liveStatus.tone === "emerald"
                ? "bg-emerald-900/30 border-emerald-800 text-emerald-300"
                : liveStatus.tone === "amber"
                  ? "bg-amber-900/30 border-amber-800 text-amber-300"
                  : liveStatus.tone === "red"
                    ? "bg-red-900/30 border-red-800 text-red-300"
                    : "bg-zinc-800 border-zinc-700 text-zinc-400")
            }
          >
            <span aria-hidden>{liveStatus.dot}</span>
            <span>{liveStatus.label}</span>
            {updatedAgo !== null && (
              <span className="ml-auto text-[9px] text-zinc-500 normal-case tracking-normal">
                {updatedAgo === 0 ? "just now" : `${updatedAgo}s ago`}
              </span>
            )}
          </div>

          <button
            onClick={handleReload}
            disabled={reloading}
            className="flex items-center gap-2 px-3 py-2 w-full rounded text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-200 disabled:opacity-50"
          >
            <RefreshCw
              size={14}
              className={reloading ? "animate-spin" : ""}
            />
            {reloading ? "Reloading…" : "Reload data"}
          </button>
          {episode && (
            <div className="text-[10px] text-zinc-500 font-mono break-all">
              <div>db: {episode.sources.db_path}</div>
              <div>traces: {episode.sources.traces_path}</div>
              {episode.loaded && !episode.traces_loaded && (
                <div className="text-amber-400 mt-1">⚠ traces missing</div>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {/* Multi-market selector header. Hidden on the lifetime tab (which
            is global) and when there's nothing to select. */}
        {marketsList.length > 0 && active !== "lifetime" && (
          <MarketSelector
            markets={marketsList}
            value={marketId}
            onChange={setMarketId}
          />
        )}

        {error && (
          <div className="m-6 rounded border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-300">
            <div className="font-semibold mb-1">Error talking to backend</div>
            <pre className="whitespace-pre-wrap font-mono text-xs">{error}</pre>
            <div className="mt-2 text-zinc-400">
              Make sure the backend is running (
              <code className="font-mono text-zinc-300">python server.py</code>
              ). The dashboard will reconnect automatically once it's reachable.
            </div>
          </div>
        )}

        {!episode && !error && (
          <div className="p-8 text-zinc-500 text-sm">Loading…</div>
        )}

        {episode && !episode.loaded && active !== "lifetime" && (
          <WaitingScreen episode={episode} />
        )}

        {/* Lifetime tab is global — it works even when no individual market
            is loaded yet, so we render it independently of episode.loaded. */}
        {active === "lifetime" && (
          <div className="p-6">
            <LifetimeMetricsView dataVersion={dataVersion} />
          </div>
        )}

        {episode && episode.loaded && active !== "lifetime" && (
          <div className="p-6">
            {active === "overview" && (
              <EpisodeOverview
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {active === "timeseries" && (
              <TimeSeriesChart
                episode={episode}
                dataVersion={dataVersion}
                focusPhaseId={focusPhaseId}
                onClearFocus={() => setFocusPhaseId(null)}
                marketId={marketId}
              />
            )}
            {active === "tradelog" && (
              <TradeLog
                episode={episode}
                dataVersion={dataVersion}
                onPhaseClick={(phaseId) => jumpToPhase(phaseId, "timeseries")}
                marketId={marketId}
              />
            )}
            {active === "orderbook" && (
              <OrderbookViewer
                episode={episode}
                dataVersion={dataVersion}
                initialPhaseId={focusPhaseId ?? undefined}
                marketId={marketId}
              />
            )}
            {active === "metrics" && (
              <PerformanceMetrics
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {active === "positions" && (
              <PositionTracker
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {active === "traces" && (
              <ReasoningTraces episode={episode} dataVersion={dataVersion} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function MarketSelector({
  markets,
  value,
  onChange,
}: {
  markets: MarketSummary[];
  value: string | null;
  onChange: (id: string) => void;
}) {
  // Use the first market as the visible default when no value is set yet.
  const current = value ?? markets[0]?.id ?? "";
  const selected = markets.find((m) => m.id === current);

  return (
    <div className="border-b border-zinc-800 bg-zinc-950/40 px-6 py-3 flex items-center gap-3">
      <span className="text-[10px] uppercase tracking-widest text-zinc-500">
        Market
      </span>
      <select
        value={current}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm font-mono text-zinc-100 focus:outline-none focus:border-emerald-500"
      >
        {markets.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name} ({m.id})
          </option>
        ))}
      </select>
      {selected && (
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <span
            className={
              "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider border " +
              (selected.state === "RUNNING"
                ? "border-emerald-800 bg-emerald-900/30 text-emerald-300"
                : selected.state === "PENDING_SETTLEMENT"
                  ? "border-amber-800 bg-amber-900/30 text-amber-300"
                  : selected.state === "SETTLED"
                    ? "border-blue-800 bg-blue-900/30 text-blue-300"
                    : "border-zinc-700 bg-zinc-800 text-zinc-300")
            }
          >
            {selected.state.toLowerCase().replace("_", " ")}
          </span>
          {selected.settlement_date && (
            <span>· settles {selected.settlement_date}</span>
          )}
        </div>
      )}
    </div>
  );
}

function WaitingScreen({ episode }: { episode: Episode }) {
  return (
    <div className="p-10 max-w-2xl mx-auto">
      <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/50 p-10 text-center">
        <div className="flex justify-center mb-4">
          <div className="relative">
            <div className="absolute inset-0 rounded-full bg-amber-500/30 animate-ping" />
            <div className="relative h-3 w-3 rounded-full bg-amber-400" />
          </div>
        </div>
        <h2 className="text-xl font-semibold text-zinc-100">
          Waiting for ModelX data…
        </h2>
        <p className="mt-2 text-sm text-zinc-400">
          {STATUS_LABEL[episode.status]}
        </p>
        {episode.status_detail && (
          <pre className="mt-3 text-xs text-zinc-500 font-mono whitespace-pre-wrap">
            {episode.status_detail}
          </pre>
        )}
        <div className="mt-6 inline-block text-left text-xs text-zinc-500 font-mono space-y-1">
          <div>
            <span className="text-zinc-600">db: </span>
            {episode.sources.db_path}
          </div>
          <div>
            <span className="text-zinc-600">traces: </span>
            {episode.sources.traces_path}
          </div>
        </div>
        <p className="mt-6 text-xs text-zinc-500">
          The dashboard polls every {Math.round(POLL_INTERVAL_MS / 1000)}s and
          will populate automatically once the database has at least one
          contract. Generate it with{" "}
          <code className="font-mono text-zinc-400">
            python3 run_live.py --db {episode.sources.db_path}
          </code>
          .
        </p>
      </div>
    </div>
  );
}
