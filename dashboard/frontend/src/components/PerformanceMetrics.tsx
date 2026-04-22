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
  stripMarketPrefix,
} from "../lib/format";
import { Plot, DARK_LAYOUT, PLOTLY_CONFIG } from "../lib/plotly-theme";
import { Badge, Card, SectionHeader } from "./ui";

type Role = "MM" | "HF";
type ChartMetric = "pnl" | "position" | "cash";

const CHART_METRIC_LABEL: Record<ChartMetric, string> = {
  pnl: "PnL",
  position: "Position",
  cash: "Cash",
};

interface MetricColumn {
  key: string;
  label: string;
  get: (a: string) => number | null | undefined;
  fmt: (v: number | null | undefined) => string;
  colorize?: boolean;
}

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

  const hasMm = mmAccounts.length > 0;
  const hasHf = hfAccounts.length > 0;

  const [role, setRole] = useState<Role>(hasMm ? "MM" : "HF");
  useEffect(() => {
    if (role === "MM" && !hasMm && hasHf) setRole("HF");
    else if (role === "HF" && !hasHf && hasMm) setRole("MM");
  }, [role, hasMm, hasHf]);

  const [chartMetric, setChartMetric] = useState<ChartMetric>("pnl");

  // Build per-phase chart traces — ordered MM first, then HF, with
  // legendgroup so Plotly renders the legend as two labelled sections.
  const chartTraces = useMemo(() => {
    if (!positions) return [];

    const ordered: { id: string; role: Role }[] = [
      ...mmAccounts.map((id) => ({ id, role: "MM" as const })),
      ...hfAccounts.map((id) => ({ id, role: "HF" as const })),
    ];

    return ordered
      .filter(({ id }) => positions.agents[id])
      .map(({ id, role }) => {
        const points = positions.agents[id];
        const groupTitle =
          role === "MM" ? "Market makers" : "Hedge funds";
        const yVals = points.map((p) => {
          if (chartMetric === "position") return p.position;
          if (chartMetric === "cash") return p.cash;
          return p.pnl_realized ?? p.pnl_mtm;
        });
        const hoverFmt = chartMetric === "position" ? ":.0f" : ":.4f";
        const label = CHART_METRIC_LABEL[chartMetric];
        return {
          x: points.map((p) => new Date(p.timestamp * 1000)),
          y: yVals,
          type: "scatter" as const,
          mode: "lines" as const,
          name: stripMarketPrefix(id),
          legendgroup: role,
          legendgrouptitle: { text: groupTitle },
          line: { color: agentColors[id], width: 2 },
          connectgaps: true,
          hovertemplate: `${stripMarketPrefix(id)}<br>${label}: %{y${hoverFmt}}<br>%{x}<extra></extra>`,
        };
      });
  }, [positions, agentColors, mmAccounts, hfAccounts, chartMetric]);

  const chartLayout = useMemo(
    () => ({
      ...DARK_LAYOUT,
      xaxis: {
        ...DARK_LAYOUT.xaxis,
        type: "date" as const,
      },
      yaxis: {
        ...DARK_LAYOUT.yaxis,
        tickformat: chartMetric === "position" ? "d" : ".2f",
      },
      showlegend: true,
      legend: {
        ...DARK_LAYOUT.legend,
        orientation: "h" as const,
        x: 0,
        y: -0.15,
        xanchor: "left" as const,
        yanchor: "top" as const,
        tracegroupgap: 12,
      },
      // Preserve user UI state (legend hidden/shown, zoom, pan) across 2s
      // polling. Tied to marketId + metric so switching resets fresh.
      uirevision: `perf-${chartMetric}-${marketId ?? "default"}`,
      margin: { t: 20, r: 30, b: 80, l: 60 },
    }),
    [marketId, chartMetric]
  );

  const mmColumns: MetricColumn[] = useMemo(
    () => [
      {
        key: "total_pnl",
        label: "Total PnL",
        get: (a) => metrics?.mm[a]?.total_pnl,
        fmt: fmtPnl,
        colorize: true,
      },
      {
        key: "sharpe",
        label: "Sharpe",
        get: (a) => metrics?.mm[a]?.sharpe,
        fmt: (v) => fmtPrice(v, 4),
        colorize: true,
      },
      {
        key: "volume",
        label: "Volume",
        get: (a) => metrics?.mm[a]?.volume,
        fmt: fmtInt,
      },
      {
        key: "volume_share",
        label: "Volume share",
        get: (a) => metrics?.mm[a]?.volume_share,
        fmt: (v) => fmtPct(v, 1),
      },
      {
        key: "notional",
        label: "Notional",
        get: (a) => metrics?.mm[a]?.notional,
        fmt: (v) => fmtPrice(v, 2),
      },
      {
        key: "notional_share",
        label: "Notional share",
        get: (a) => metrics?.mm[a]?.notional_share,
        fmt: (v) => fmtPct(v, 1),
      },
      {
        key: "pnl_bps",
        label: "PnL bps",
        get: (a) => metrics?.mm[a]?.pnl_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
      {
        key: "uptime",
        label: "Uptime",
        get: (a) => metrics?.mm[a]?.uptime,
        fmt: (v) => fmtPct(v, 0),
      },
      {
        key: "consensus",
        label: "Consensus",
        get: (a) => metrics?.mm[a]?.consensus,
        fmt: (v) => fmtPct(v, 1),
      },
      {
        key: "markout_2_bps",
        label: "Markout 2",
        get: (a) => metrics?.mm[a]?.markout_2_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
      {
        key: "markout_10_bps",
        label: "Markout 10",
        get: (a) => metrics?.mm[a]?.markout_10_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
      {
        key: "markout_40_bps",
        label: "Markout 40",
        get: (a) => metrics?.mm[a]?.markout_40_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
      {
        key: "avg_abs_position",
        label: "Avg |pos|",
        get: (a) => metrics?.mm[a]?.avg_abs_position,
        fmt: (v) => fmtPrice(v, 2),
      },
      {
        key: "self_cross_count",
        label: "Self-cross ct",
        get: (a) => metrics?.mm[a]?.self_cross_count,
        fmt: fmtInt,
      },
      {
        key: "self_cross_volume",
        label: "Self-cross vol",
        get: (a) => metrics?.mm[a]?.self_cross_volume,
        fmt: fmtInt,
      },
    ],
    [metrics]
  );

  const hfColumns: MetricColumn[] = useMemo(
    () => [
      {
        key: "total_pnl",
        label: "Total PnL",
        get: (a) => metrics?.hf[a]?.total_pnl,
        fmt: fmtPnl,
        colorize: true,
      },
      {
        key: "sharpe",
        label: "Sharpe",
        get: (a) => metrics?.hf[a]?.sharpe,
        fmt: (v) => fmtPrice(v, 4),
        colorize: true,
      },
      {
        key: "markout_2_bps",
        label: "Markout 2",
        get: (a) => metrics?.hf[a]?.markout_2_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
      {
        key: "markout_10_bps",
        label: "Markout 10",
        get: (a) => metrics?.hf[a]?.markout_10_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
      {
        key: "markout_40_bps",
        label: "Markout 40",
        get: (a) => metrics?.hf[a]?.markout_40_bps,
        fmt: (v) => fmtBps(v, 1),
        colorize: true,
      },
    ],
    [metrics]
  );

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  if (!metrics || !positions)
    return <div className="text-sm text-zinc-500">Loading...</div>;

  const activeAccounts = role === "MM" ? mmAccounts : hfAccounts;
  const activeColumns = role === "MM" ? mmColumns : hfColumns;

  return (
    <div className="space-y-6">
      {/* Performance over time */}
      <Card
        title={`${CHART_METRIC_LABEL[chartMetric]} over time`}
        action={
          <ChartMetricToggle metric={chartMetric} onChange={setChartMetric} />
        }
      >
        <div style={{ width: "100%", height: 380 }}>
          <Plot
            data={chartTraces as any}
            layout={chartLayout as any}
            config={PLOTLY_CONFIG as any}
            useResizeHandler
            style={{ width: "100%", height: "100%" }}
          />
        </div>
      </Card>

      {/* Performance metrics */}
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

      <Card
        title={role === "MM" ? "Market makers" : "Hedge funds"}
        action={
          <RoleToggle
            role={role}
            onChange={setRole}
            mmEnabled={hasMm}
            hfEnabled={hasHf}
          />
        }
      >
        {activeAccounts.length === 0 ? (
          <div className="text-sm text-zinc-500">
            No {role === "MM" ? "market makers" : "hedge funds"} in this market.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="text-sm tabular">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 text-left font-medium sticky left-0 bg-zinc-900/60 z-10">
                    Model
                  </th>
                  {activeColumns.map((c) => (
                    <th
                      key={c.key}
                      className="py-2 px-3 text-right font-medium whitespace-nowrap"
                    >
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {activeAccounts.map((a) => (
                  <tr
                    key={a}
                    className="border-b border-zinc-900 last:border-0 hover:bg-zinc-900/40"
                  >
                    <td className="py-1.5 pr-4 sticky left-0 bg-zinc-900/60">
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                          style={{ background: agentColors[a] }}
                        />
                        <span className="font-mono text-zinc-200 text-xs">
                          {stripMarketPrefix(a)}
                        </span>
                      </div>
                    </td>
                    {activeColumns.map((c) => {
                      const v = c.get(a);
                      const cls = c.colorize
                        ? pnlClass(v ?? null)
                        : "text-zinc-200";
                      return (
                        <td
                          key={c.key}
                          className={`py-1.5 px-3 text-right ${cls}`}
                        >
                          {v === null || v === undefined ? (
                            <span className="text-zinc-600">---</span>
                          ) : (
                            c.fmt(v as number)
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function ChartMetricToggle({
  metric,
  onChange,
}: {
  metric: ChartMetric;
  onChange: (m: ChartMetric) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Chart metric"
      className="inline-flex rounded-md border border-zinc-700 bg-zinc-950 p-0.5"
    >
      {(["pnl", "position", "cash"] as const).map((m) => (
        <button
          key={m}
          role="tab"
          aria-selected={metric === m}
          onClick={() => onChange(m)}
          className={
            "px-3 py-1 text-xs font-medium rounded transition-colors " +
            (metric === m
              ? "bg-zinc-700 text-zinc-100"
              : "text-zinc-400 hover:text-zinc-100")
          }
        >
          {CHART_METRIC_LABEL[m]}
        </button>
      ))}
    </div>
  );
}

function RoleToggle({
  role,
  onChange,
  mmEnabled,
  hfEnabled,
}: {
  role: Role;
  onChange: (r: Role) => void;
  mmEnabled: boolean;
  hfEnabled: boolean;
}) {
  return (
    <div
      role="tablist"
      aria-label="Agent role"
      className="inline-flex rounded-md border border-zinc-700 bg-zinc-950 p-0.5"
    >
      <ToggleButton
        label="MM"
        active={role === "MM"}
        disabled={!mmEnabled}
        onClick={() => onChange("MM")}
      />
      <ToggleButton
        label="HF"
        active={role === "HF"}
        disabled={!hfEnabled}
        onClick={() => onChange("HF")}
      />
    </div>
  );
}

function ToggleButton({
  label,
  active,
  disabled,
  onClick,
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      disabled={disabled}
      onClick={onClick}
      className={
        "px-3 py-1 text-xs font-medium rounded transition-colors " +
        (active
          ? "bg-zinc-700 text-zinc-100"
          : disabled
            ? "text-zinc-600 cursor-not-allowed"
            : "text-zinc-400 hover:text-zinc-100")
      }
    >
      {label}
    </button>
  );
}
