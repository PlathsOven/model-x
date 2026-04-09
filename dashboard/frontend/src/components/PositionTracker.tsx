import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import type { Episode, PositionsResponse } from "../types";
import {
  AXIS_COLOR,
  GRID_COLOR,
  buildAgentColors,
} from "../lib/colors";
import { fmtInt, fmtPnl } from "../lib/format";
import { Card, SectionHeader } from "./ui";

type Metric = "position" | "pnl" | "cash";

export function PositionTracker({
  episode,
  dataVersion,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  marketId?: string | null;
}) {
  const [pos, setPos] = useState<PositionsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(episode.accounts.map((a) => a.id))
  );
  const [metric, setMetric] = useState<Metric>("position");

  useEffect(() => {
    api
      .positions(marketId)
      .then(setPos)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  // When new agents appear (e.g. live demo just added one) include them in
  // the visible selection by default. Don't touch the selection if the user
  // has explicitly hidden some.
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const a of episode.accounts) {
        if (!next.has(a.id)) {
          next.add(a.id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [episode.accounts]);

  const mmAccounts = episode.accounts.filter((a) => a.role === "MM").map((a) => a.id);
  const hfAccounts = episode.accounts.filter((a) => a.role === "HF").map((a) => a.id);
  const agentColors = useMemo(
    () => buildAgentColors(mmAccounts, hfAccounts),
    [episode]
  );

  const rows = useMemo(() => {
    if (!pos) return [];
    const agents = Object.keys(pos.agents);
    if (agents.length === 0) return [];
    const spine = pos.agents[agents[0]];
    return spine.map((_, i) => {
      const row: Record<string, any> = { cycle: spine[i].cycle_index };
      for (const a of agents) {
        const p = pos.agents[a][i];
        if (metric === "position") row[a] = p.position;
        else if (metric === "pnl") row[a] = p.pnl_realized ?? p.pnl_mtm;
        else if (metric === "cash") row[a] = p.cash;
      }
      return row;
    });
  }, [pos, metric]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  if (!pos)
    return <div className="text-sm text-zinc-500">Loading…</div>;

  const visible = episode.accounts
    .filter((a) => selected.has(a.id))
    .map((a) => a.id);

  const fmt =
    metric === "position" ? fmtInt : metric === "pnl" ? fmtPnl : fmtPnl;

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Position &amp; PnL tracker"
        subtitle="Per-agent time series — switch between position, mark-to-market PnL, or cash"
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
            Metric
          </span>
          {(["position", "pnl", "cash"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMetric(m)}
              className={
                "px-2 py-1 rounded border " +
                (metric === m
                  ? "border-emerald-500 bg-emerald-900/30 text-emerald-300"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
              }
            >
              {m}
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="text-zinc-500 uppercase tracking-widest text-[10px] mr-1">
            Agents
          </span>
          {episode.accounts.map((a) => {
            const on = selected.has(a.id);
            return (
              <button
                key={a.id}
                onClick={() => toggle(a.id)}
                className={
                  "flex items-center gap-2 px-2 py-1 rounded border font-mono " +
                  (on
                    ? "border-zinc-700 bg-zinc-800 text-zinc-100"
                    : "border-zinc-800 bg-zinc-900 text-zinc-500")
                }
              >
                <span
                  className="inline-block w-3 h-3 rounded-sm"
                  style={{
                    background: on ? agentColors[a.id] : "transparent",
                    border: on ? "none" : `1px solid ${agentColors[a.id]}`,
                  }}
                />
                {a.id}
              </button>
            );
          })}
        </div>
      </Card>

      <Card>
        <div className="h-[500px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={rows}
              margin={{ top: 10, right: 30, left: 10, bottom: 10 }}
            >
              <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" />
              <XAxis
                dataKey="cycle"
                type="number"
                domain={[0, Math.max(0, episode.num_cycles - 1)]}
                allowDecimals={false}
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 11 }}
              />
              <YAxis
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 11 }}
                tickFormatter={(v) => fmt(v)}
                width={70}
              />
              <Tooltip
                formatter={(value: any, name: any) => [fmt(value as number), name]}
                labelFormatter={(l) => `Cycle ${l}`}
              />
              {visible.map((a) => (
                <Line
                  key={a}
                  type="monotone"
                  dataKey={a}
                  stroke={agentColors[a]}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}
