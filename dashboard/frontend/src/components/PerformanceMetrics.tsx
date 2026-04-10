import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Episode, Metrics, PositionsResponse } from "../types";
import { buildAgentColors } from "../lib/colors";
import {
  fmtBps,
  fmtInt,
  fmtPct,
  fmtPnl,
  fmtPrice,
  pnlClass,
} from "../lib/format";
import { Plot, DARK_LAYOUT, PLOTLY_CONFIG } from "../lib/plotly-theme";
import { Badge, Card, SectionHeader } from "./ui";

export function PerformanceMetrics({
  episode,
  dataVersion,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  marketId?: string | null;
}) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.metrics(marketId), api.positions(marketId)])
      .then(([m, p]) => {
        setMetrics(m);
        setPositions(p);
      })
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

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

  // Build per-phase PnL chart data.
  const pnlTraces = useMemo(() => {
    if (!positions) return [];
    const agents = Object.keys(positions.agents);
    if (agents.length === 0) return [];

    return agents.map((a) => {
      const points = positions.agents[a];
      return {
        x: points.map((p) => new Date(p.timestamp * 1000)),
        y: points.map((p) => p.pnl_realized ?? p.pnl_mtm),
        type: "scatter" as const,
        mode: "lines" as const,
        name: a,
        line: { color: agentColors[a], width: 2 },
        connectgaps: true,
        hovertemplate: `${a}<br>PnL: %{y:.4f}<br>%{x}<extra></extra>`,
      };
    });
  }, [positions, agentColors]);

  const pnlLayout = useMemo(
    () => ({
      ...DARK_LAYOUT,
      xaxis: {
        ...DARK_LAYOUT.xaxis,
        type: "date" as const,
      },
      yaxis: {
        ...DARK_LAYOUT.yaxis,
        tickformat: ".2f",
      },
      showlegend: true,
      legend: {
        ...DARK_LAYOUT.legend,
        orientation: "h" as const,
        x: 0,
        y: -0.15,
        xanchor: "left" as const,
        yanchor: "top" as const,
      },
      margin: { t: 20, r: 30, b: 60, l: 60 },
    }),
    []
  );

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  if (!metrics || !positions)
    return <div className="text-sm text-zinc-500">Loading...</div>;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Performance metrics"
        subtitle={
          metrics.settled
            ? "Final scores computed from modelx.scoring"
            : "Live scores — contract not yet settled"
        }
        action={
          metrics.settled ? (
            <Badge tone="emerald">settled</Badge>
          ) : (
            <Badge tone="amber">live</Badge>
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
                  label="markout_2"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.markout_2}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_10"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.markout_10}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_40"
                  agents={mmAccounts}
                  get={(a) => metrics.mm[a]?.markout_40}
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
                  label="markout_2"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.markout_2}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_10"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.markout_10}
                  fmt={fmtPnl}
                  colorize
                />
                <MetricRow
                  label="markout_40"
                  agents={hfAccounts}
                  get={(a) => metrics.hf[a]?.markout_40}
                  fmt={fmtPnl}
                  colorize
                />
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* PnL over time chart */}
      <Card title="PnL over time">
        <div style={{ width: "100%", height: 380 }}>
          <Plot
            data={pnlTraces as any}
            layout={pnlLayout as any}
            config={PLOTLY_CONFIG as any}
            useResizeHandler
            style={{ width: "100%", height: "100%" }}
          />
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
              <span className="text-zinc-600">---</span>
            ) : (
              fmt(v as number)
            )}
          </td>
        );
      })}
    </tr>
  );
}
