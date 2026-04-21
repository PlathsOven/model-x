import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  RefreshCw,
} from "lucide-react";
import { api } from "./api";
import type { Episode, EpisodeStatus, MarketSummary } from "./types";
import { TimeSeriesChart } from "./components/TimeSeriesChart";
import { TradesView } from "./components/TradesView";
import { ReasoningTraces } from "./components/ReasoningTraces";
import { PerformanceMetrics } from "./components/PerformanceMetrics";
import { PositionTracker } from "./components/PositionTracker";
import { LifetimeMetricsView } from "./components/LifetimeMetrics";
import { ContextView } from "./components/ContextView";
import { fmtInt, fmtPrice, formatSettlementDate } from "./lib/format";

const POLL_INTERVAL_MS = 2000;

const STATUS_LABEL: Record<EpisodeStatus, string> = {
  ok: "Connected to a populated database.",
  db_missing: "Waiting for the database file to appear on disk.",
  no_contracts: "Database is open but contains no contracts yet.",
  error: "Backend hit an error reading the database.",
};

type TabKey =
  | "performance"
  | "positions"
  | "context"
  | "reasoning"
  | "trades"
  | "lifetime";

interface TabItem {
  key: TabKey;
  label: string;
}

const TABS: TabItem[] = [
  { key: "performance", label: "Performance" },
  { key: "positions", label: "Positions" },
  { key: "context", label: "Context" },
  { key: "reasoning", label: "Reasoning" },
  { key: "trades", label: "Trades" },
  { key: "lifetime", label: "Lifetime" },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("performance");
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [focusPhaseId, setFocusPhaseId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const [marketsList, setMarketsList] = useState<MarketSummary[]>([]);
  const [marketId, setMarketId] = useState<string | null>(null);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

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

  const jumpToPhase = useCallback((phaseId: string) => {
    setFocusPhaseId(phaseId);
  }, []);

  const dataVersion = episode?.loaded_at ?? 0;

  const liveStatus = useMemo(() => {
    if (error) return { tone: "red" as const, label: "error", dot: "×" };
    if (!episode) return { tone: "zinc" as const, label: "loading", dot: "·" };
    if (episode.loaded) return { tone: "emerald" as const, label: "live", dot: "●" };
    return { tone: "amber" as const, label: "waiting", dot: "○" };
  }, [episode, error]);

  const updatedAgo = episode
    ? Math.max(0, Math.round(now - episode.loaded_at))
    : null;

  const mmCount = episode?.accounts.filter((a) => a.role === "MM").length ?? 0;
  const hfCount = episode?.accounts.filter((a) => a.role === "HF").length ?? 0;

  const visibleTabs = useMemo(
    () =>
      TABS.filter(
        (t) => t.key !== "lifetime" || marketsList.length > 1
      ),
    [marketsList]
  );

  return (
    <div className="flex flex-col h-full min-h-screen bg-zinc-900 text-zinc-100">
      {/* Top bar */}
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm">
        <div className="flex items-center gap-4 px-5 py-2.5">
          {/* Logo */}
          <div className="flex items-center gap-2 shrink-0">
            <Activity className="text-emerald-400" size={20} />
            <span className="font-semibold tracking-tight text-sm">ModelX</span>
          </div>

          {/* Contract name */}
          {episode?.contract && (
            <div className="min-w-0 hidden sm:block text-sm text-zinc-300 truncate">
              {episode.contract.name}
            </div>
          )}

          <div className="flex-1" />

          {/* Market selector */}
          {marketsList.length > 0 && activeTab !== "lifetime" && (
            <div className="flex items-center gap-2 shrink-0">
              <select
                value={marketId ?? marketsList[0]?.id ?? ""}
                onChange={(e) => setMarketId(e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs font-mono text-zinc-100 focus:outline-none focus:border-emerald-500"
              >
                {marketsList.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
              {(() => {
                const selected = marketsList.find(
                  (m) => m.id === (marketId ?? marketsList[0]?.id)
                );
                if (!selected) return null;
                return (
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
                );
              })()}
            </div>
          )}

          {/* Status + reload */}
          <div className="flex items-center gap-2 shrink-0">
            <span
              className={
                "text-xs " +
                (liveStatus.tone === "emerald"
                  ? "text-emerald-400"
                  : liveStatus.tone === "amber"
                    ? "text-amber-400"
                    : liveStatus.tone === "red"
                      ? "text-red-400"
                      : "text-zinc-500")
              }
            >
              {liveStatus.dot}
            </span>
            {updatedAgo !== null && (
              <span className="text-[10px] text-zinc-500">
                {updatedAgo === 0 ? "now" : `${updatedAgo}s`}
              </span>
            )}
            <button
              onClick={handleReload}
              disabled={reloading}
              className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-100 disabled:opacity-50"
              title="Reload data"
            >
              <RefreshCw
                size={14}
                className={reloading ? "animate-spin" : ""}
              />
            </button>
          </div>
        </div>
      </header>

      {/* Intro summary */}
      <section className="border-b border-zinc-800 bg-zinc-950/40 px-6 py-4 text-sm text-zinc-400">
        <p className="leading-relaxed">
          <span className="font-semibold text-zinc-200">ModelX</span> is a
          prediction exchange where LLM agents compete on linear contracts that
          settle against real-world outcomes. Each cycle runs in two phases:
        </p>
        <ul className="mt-2 ml-5 list-disc space-y-1 marker:text-zinc-600">
          <li>
            <span className="font-medium text-zinc-200">MM phase</span> —
            Market Makers post sealed two-sided quotes that match against each
            other. Mark-to-market is the VWAP of the remaining unmatched
            orderbook.
          </li>
          <li>
            <span className="font-medium text-zinc-200">HF phase</span> — Hedge
            Funds take from the residual MM orderbook. Mark-to-market is the
            VWAP of HF trade prices.
          </li>
        </ul>
        <p className="mt-2 leading-relaxed">
          Between cycles, open positions are revalued at the new mark and
          unrealized PnL updates accordingly (scaled by a per-contract
          multiplier). On the settlement date, the operator enters the
          real-world outcome value; open positions are marked to that value and
          PnL is finalized.
        </p>
      </section>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mt-4 rounded border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          <div className="font-semibold mb-1">Error talking to backend</div>
          <pre className="whitespace-pre-wrap font-mono text-xs">{error}</pre>
          <div className="mt-2 text-zinc-400">
            Make sure the backend is running (
            <code className="font-mono text-zinc-300">python server.py</code>
            ). The dashboard will reconnect automatically.
          </div>
        </div>
      )}

      {/* Loading / waiting states */}
      {!episode && !error && (
        <div className="p-8 text-zinc-500 text-sm">Loading…</div>
      )}

      {episode && !episode.loaded && (
        <WaitingScreen episode={episode} />
      )}

      {/* Main content — hero chart + tabs */}
      {episode && episode.loaded && (
        <main className="flex-1 overflow-y-auto">
          {/* Hero: Time Series Chart */}
          <div className="px-4 pt-3" style={{ height: "calc(70vh - 48px)", minHeight: 360 }}>
            <TimeSeriesChart
              episode={episode}
              dataVersion={dataVersion}
              focusPhaseId={focusPhaseId}
              onClearFocus={() => setFocusPhaseId(null)}
              marketId={marketId}
            />
          </div>

          {/* Summary stats row */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-6 py-2.5 border-b border-zinc-800 text-xs text-zinc-400">
            <Stat label="Phases" value={fmtInt(episode.phase_count ?? 0)} />
            <Stat
              label="Agents"
              value={`${episode.accounts.length}`}
              sub={`${mmCount} MM · ${hfCount} HF`}
            />
            <Stat
              label="Fills"
              value={fmtInt(episode.stats.total_fills)}
              sub={`${episode.stats.total_volume} vol`}
            />
            <NextPhaseStat episode={episode} now={now} />
            {episode.settled && episode.contract?.settlement_value != null && (
              <Stat
                label="Settlement"
                value={episode.contract.settlement_value.toFixed(4)}
                className="text-emerald-400"
              />
            )}
            {!episode.settled &&
              episode.market_state === "PENDING_SETTLEMENT" && (
                <Stat
                  label="Status"
                  value="Pending settlement"
                  className="text-amber-400"
                />
              )}
            {!episode.settled && episode.market_state === "PAUSED" && (
              <Stat label="Status" value="Paused" className="text-zinc-400" />
            )}
          </div>

          {/* About this market */}
          {episode.contract && (
            <AboutMarket episode={episode} />
          )}

          {/* Horizontal tab bar */}
          <nav className="flex items-center gap-1 px-6 py-2 border-b border-zinc-800 overflow-x-auto">
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={
                  "px-3 py-1.5 rounded text-sm whitespace-nowrap transition-colors " +
                  (activeTab === tab.key
                    ? "bg-zinc-800 text-white font-medium"
                    : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200")
                }
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {/* Tab content */}
          <div className="p-6">
            {activeTab === "performance" && (
              <PerformanceMetrics
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {activeTab === "positions" && (
              <PositionTracker
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {activeTab === "context" && (
              <ContextView
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {activeTab === "reasoning" && (
              <ReasoningTraces
                episode={episode}
                dataVersion={dataVersion}
                marketId={marketId}
              />
            )}
            {activeTab === "trades" && (
              <TradesView
                episode={episode}
                dataVersion={dataVersion}
                onPhaseClick={jumpToPhase}
                focusPhaseId={focusPhaseId}
                marketId={marketId}
              />
            )}
            {activeTab === "lifetime" && (
              <LifetimeMetricsView dataVersion={dataVersion} />
            )}
          </div>
        </main>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  className = "text-zinc-100",
}: {
  label: string;
  value: string;
  sub?: string;
  className?: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-zinc-500">{label}:</span>
      <span className={`font-medium ${className}`}>{value}</span>
      {sub && <span className="text-zinc-600">{sub}</span>}
    </span>
  );
}

function AboutMarket({ episode }: { episode: Episode }) {
  const contract = episode.contract;
  if (!contract) return null;
  const settlementDate = formatSettlementDate(contract.settlement_date);
  return (
    <div className="px-6 py-4 border-b border-zinc-800">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60">
        <div className="px-4 py-3 border-b border-zinc-800">
          <div className="text-sm font-semibold text-zinc-100">
            About this market
          </div>
        </div>
        <div className="p-4 grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-2">
            <h2 className="text-base font-semibold text-zinc-100">
              {contract.name}
            </h2>
            {contract.description && (
              <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                {contract.description}
              </p>
            )}
          </div>
          <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-4 space-y-3 text-xs">
            <MetaRow label="Resolution">
              {settlementDate ?? (
                <span className="text-zinc-500">not set</span>
              )}
            </MetaRow>
            <MetaRow label="Settlement">
              {contract.settlement_value != null ? (
                <span className="text-emerald-400 font-medium">
                  {fmtPrice(contract.settlement_value, 4)}
                </span>
              ) : episode.market_state === "PENDING_SETTLEMENT" ? (
                <span className="text-amber-400">pending</span>
              ) : (
                <span className="text-zinc-500">unsettled</span>
              )}
            </MetaRow>
            <MetaRow label="Multiplier">
              {contract.multiplier.toString()}
            </MetaRow>
            <MetaRow label="Position limit">
              {fmtInt(contract.position_limit)}
            </MetaRow>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetaRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-[10px] uppercase tracking-widest text-zinc-500">
        {label}
      </span>
      <span className="text-zinc-200 text-right">{children}</span>
    </div>
  );
}

function formatCountdown(secondsRemaining: number): string {
  const s = Math.max(0, Math.floor(secondsRemaining));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return rem === 0 ? `${m}m` : `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm === 0 ? `${h}h` : `${h}h ${mm}m`;
}

function NextPhaseStat({
  episode,
  now,
}: {
  episode: Episode;
  now: number;
}) {
  const pd = episode.phase_duration_seconds;
  if (
    episode.market_state !== "RUNNING" ||
    pd == null ||
    pd <= 0 ||
    episode.pending_mm == null
  ) {
    return null;
  }
  const nextTickAt = Math.ceil(now / pd) * pd;
  const remaining = nextTickAt - now;
  const isMm = episode.pending_mm === 1;
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-zinc-500">Next:</span>
      <span
        className={
          "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider border " +
          (isMm
            ? "border-blue-800 bg-blue-900/30 text-blue-300"
            : "border-orange-800 bg-orange-900/30 text-orange-300")
        }
      >
        {isMm ? "MM" : "HF"}
      </span>
      <span className="font-medium text-zinc-100 tabular-nums">
        in {formatCountdown(remaining)}
      </span>
    </span>
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
