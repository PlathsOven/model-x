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
import type { Episode, Metrics, PositionsResponse } from "../types";
import {
  AXIS_COLOR,
  GRID_COLOR,
  buildAgentColors,
} from "../lib/colors";
import {
  fmtBps,
  fmtInt,
  fmtPct,
  fmtPnl,
  fmtPrice,
  pnlClass,
} from "../lib/format";
import { Badge, Card, SectionHeader } from "./ui";

export function PerformanceMetrics({ episode }: { episode: Episode }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.metrics(), api.positions()])
      .then(([m, p]) => {
        setMetrics(m);
        setPositions(p);
      })
      .catch((e) => setErr(e?.message || String(e)));
  }, []);

  const mmAccounts = useMemo(
    () => episode.accounts.filter((a) => a.role === "MM").map((a) => a.id),
    [episode]
  );
  const hfAccounts = useMemo(
    () => episode.accounts.filter((a) => a.role === "HF").map((a) => a.id),
    [episode]
  );
  const agentColors = useMemo(
    () => buildAgentColors(mmAccounts, hfAccounts),
    [mmAccounts, hfAccounts]
  );

  // Build per-cycle PnL chart data: rows keyed by cycle_index with one column
  // per agent. Uses pnl_realized when settled, else pnl_mtm.
  const pnlRows = useMemo(() => {
    if (!positions) return [];
    // Use first agent's series as the cycle spine
    const agents = Object.keys(positions.agents);
    if (agents.length === 0) return [];
    const spine = positions.agents[agents[0]];
    return spine.map((_, i) => {
      const row: Record<string, any> = { cycle: spine[i].cycle_index };
      for (const a of agents) {
        const p = positions.agents[a][i];
        row[a] = p.pnl_realized ?? p.pnl_mtm;
      }
      return row;
    });
  }, [positions]);

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  if (!metrics || !positions)
    return <div className="text-sm text-zinc-500">Loading…</div>;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Performance metrics"
        subtitle={
          metrics.settled
            ? "Final scores computed from modelx.scoring"
            : "Contract not settled — PnL, Sharpe, and markouts are pending"
        }
        action={
          metrics.settled ? (
            <Badge tone="emerald">settled</Badge>
          ) : (
            <Badge tone="amber">pending</Badge>
          )
        }
      />

      {/* MM metrics table */}
      {mmAccounts.length > 0 && (
        <Card title="Market makers">
          <div className="overflow-x-auto">
            <table className="text-sm tabular">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 text-left font-medium">Metric</th>
                  {mmAccounts.map((a) => (
                    <th
                      key={a}
                      className="py-2 px-3 text-right font-medium font-mono"
                      style={{ color: agentColors[a] }}
                    >
                      {a}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <MetricRow
                  label="total_pnl"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.total_pnl}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="sharpe"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.sharpe}
                  fmt={(v) => fmtPrice(v, 4)}
                  colorize
                />
                <MetricRow
                  label="volume"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.volume}
                  fmt={fmtInt}
                />
                <MetricRow
                  label="volume_share"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.volume_share}
                  fmt={(v) => fmtPct(v, 1)}
                />
                <MetricRow
                  label="pnl_bps"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.pnl_bps}
                  fmt={(v) => fmtBps(v, 1)}
                  colorize
                />
                <MetricRow
                  label="uptime"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.uptime}
                  fmt={(v) => fmtPct(v, 0)}
                />
                <MetricRow
                  label="consensus"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.consensus}
                  fmt={(v) => fmtPct(v, 1)}
                />
                <MetricRow
                  label="markout_1"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.markout_1}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_5"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.markout_5}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_20"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.markout_20}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="avg_abs_position"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.avg_abs_position}
                  fmt={(v) => fmtPrice(v, 2)}
                />
                <MetricRow
                  label="self_cross_count"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.self_cross_count}
                  fmt={fmtInt}
                />
                <MetricRow
                  label="self_cross_volume"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.self_cross_volume}
                  fmt={fmtInt}
                />
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* HF metrics table */}
      {hfAccounts.length > 0 && (
        <Card title="Hedge funds">
          <div className="overflow-x-auto">
            <table className="text-sm tabular">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 text-left font-medium">Metric</th>
                  {hfAccounts.map((a) => (
                    <th
                      key={a}
                      className="py-2 px-3 text-right font-medium font-mono"
                      style={{ color: agentColors[a] }}
                    >
                      {a}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <MetricRow
                  label="total_pnl"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.total_pnl}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="sharpe"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.sharpe}
                  fmt={(v) => fmtPrice(v, 4)}
                  colorize
                />
                <MetricRow
                  label="markout_1"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.markout_1}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_5"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.markout_5}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_20"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.markout_20}
                  fmt={fmtPnl}
                  colorize
                />
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* PnL over time chart */}
      <Card title="PnL over cycles">
        <div className="h-[380px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={pnlRows}
              margin={{ top: 10, right: 30, left: 10, bottom: 10 }}
            >
              <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" />
              <XAxis
                dataKey="cycle"
                type="number"
                domain={[0, episode.num_cycles - 1]}
                allowDecimals={false}
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 11 }}
              />
              <YAxis
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 11 }}
                tickFormatter={(v) => fmtPnl(v, 2)}
                width={70}
              />
              <Tooltip
                formatter={(value: any, name: any) => [fmtPnl(value, 4), name]}
                labelFormatter={(l) => `Cycle ${l}`}
              />
              {[...mmAccounts, ...hfAccounts].map((a) => (
                <Line
                  key={a}
                  type="monotone"
                  dataKey={a}
                  stroke={agentColors[a]}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}

function MetricRow({
  label,
  agents,
  get,
  fmt,
  colorize = false,
}: {
  label: string;
  agents: string[];
  get: (a: string) => number | null | undefined;
  fmt: (v: number | null | undefined) => string;
  colorize?: boolean;
}) {
  return (
    <tr className="border-b border-zinc-900 last:border-0">
      <td className="py-1.5 pr-4 text-zinc-400 text-xs">{label}</td>
      {agents.map((a) => {
        const v = get(a);
        const cls = colorize ? pnlClass(v ?? null) : "text-zinc-200";
        return (
          <td key={a} className={`py-1.5 px-3 text-right ${cls}`}>
            {v === null || v === undefined ? (
              <span className="text-zinc-600">pending</span>
            ) : (
              fmt(v as number)
            )}
          </td>
        );
      })}
    </tr>
  );
}
