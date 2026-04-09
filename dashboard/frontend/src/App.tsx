import { useCallback, useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  BookOpen,
  Home,
  LineChart as LineChartIcon,
  ListTree,
  MessageSquare,
  RefreshCw,
  Trophy,
} from "lucide-react";
import { api } from "./api";
import type { Episode } from "./types";
import { EpisodeOverview } from "./components/EpisodeOverview";
import { TimeSeriesChart } from "./components/TimeSeriesChart";
import { TradeLog } from "./components/TradeLog";
import { OrderbookViewer } from "./components/OrderbookViewer";
import { ReasoningTraces } from "./components/ReasoningTraces";
import { PerformanceMetrics } from "./components/PerformanceMetrics";
import { PositionTracker } from "./components/PositionTracker";

type ViewKey =
  | "overview"
  | "timeseries"
  | "tradelog"
  | "orderbook"
  | "traces"
  | "metrics"
  | "positions";

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
];

export default function App() {
  const [active, setActive] = useState<ViewKey>("overview");
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [focusCycle, setFocusCycle] = useState<number | null>(null);

  const loadEpisode = useCallback(async () => {
    setError(null);
    try {
      const ep = await api.episode();
      setEpisode(ep);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  }, []);

  useEffect(() => {
    loadEpisode();
  }, [loadEpisode]);

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

  const jumpToCycle = useCallback((cycle: number, view?: ViewKey) => {
    setFocusCycle(cycle);
    if (view) setActive(view);
  }, []);

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
              {!episode.traces_loaded && (
                <div className="text-amber-400 mt-1">⚠ traces missing</div>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {error && (
          <div className="m-6 rounded border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-300">
            <div className="font-semibold mb-1">Error loading data</div>
            <pre className="whitespace-pre-wrap font-mono text-xs">{error}</pre>
            <div className="mt-2 text-zinc-400">
              Make sure the backend is running (
              <code className="font-mono text-zinc-300">python server.py</code>
              ) and that the db/traces paths exist.
            </div>
          </div>
        )}

        {!episode && !error && (
          <div className="p-8 text-zinc-500 text-sm">Loading…</div>
        )}

        {episode && (
          <div className="p-6">
            {active === "overview" && <EpisodeOverview episode={episode} />}
            {active === "timeseries" && (
              <TimeSeriesChart
                episode={episode}
                focusCycle={focusCycle}
                onClearFocus={() => setFocusCycle(null)}
              />
            )}
            {active === "tradelog" && (
              <TradeLog
                episode={episode}
                onCycleClick={(c) => jumpToCycle(c, "timeseries")}
              />
            )}
            {active === "orderbook" && (
              <OrderbookViewer
                episode={episode}
                initialCycle={focusCycle ?? 0}
              />
            )}
            {active === "metrics" && <PerformanceMetrics episode={episode} />}
            {active === "positions" && <PositionTracker episode={episode} />}
            {active === "traces" && <ReasoningTraces episode={episode} />}
          </div>
        )}
      </main>
    </div>
  );
}
