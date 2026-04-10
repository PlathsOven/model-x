import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Episode, PositionsResponse } from "../types";
import { buildAgentColors } from "../lib/colors";
import { fmtInt, fmtPnl } from "../lib/format";
import { Plot, DARK_LAYOUT, PLOTLY_CONFIG } from "../lib/plotly-theme";
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

  // When new agents appear, include them in the visible selection by default.
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

  const mmAccounts = episode.accounts
    .filter((a) => a.role === "MM")
    .map((a) => a.id);
  const hfAccounts = episode.accounts
    .filter((a) => a.role === "HF")
    .map((a) => a.id);
  const agentColors = useMemo(
    () => buildAgentColors(mmAccounts, hfAccounts),
    [episode]
  );

  const visible = episode.accounts
    .filter((a) => selected.has(a.id))
    .map((a) => a.id);

  const fmt =
    metric === "position" ? fmtInt : metric === "pnl" ? fmtPnl : fmtPnl;

  // Build Plotly traces.
  const traces = useMemo(() => {
    if (!pos) return [];
    return visible
      .filter((a) => pos.agents[a])
      .map((a) => {
        const points = pos.agents[a];
        const yVals = points.map((p) => {
          if (metric === "position") return p.position;
          if (metric === "pnl") return p.pnl_realized ?? p.pnl_mtm;
          return p.cash;
        });
        return {
          x: points.map((p) => new Date(p.timestamp * 1000)),
          y: yVals,
          type: "scatter" as const,
          mode: "lines" as const,
          name: a,
          line: { color: agentColors[a], width: 2 },
          hovertemplate: `${a}<br>${metric}: %{y${metric === "position" ? ":.0f" : ":.4f"}}<br>%{x}<extra></extra>`,
        };
      });
  }, [pos, visible, metric, agentColors]);

  const layout = useMemo(
    () => ({
      ...DARK_LAYOUT,
      xaxis: {
        ...DARK_LAYOUT.xaxis,
        type: "date" as const,
      },
      yaxis: {
        ...DARK_LAYOUT.yaxis,
        tickformat: metric === "position" ? "d" : ".2f",
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
    [metric]
  );

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
    return <div className="text-sm text-zinc-500">Loading...</div>;

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
        <div style={{ width: "100%", height: 500 }}>
          <Plot
            data={traces as any}
            layout={layout as any}
            config={PLOTLY_CONFIG as any}
            useResizeHandler
            style={{ width: "100%", height: "100%" }}
          />
        </div>
      </Card>
    </div>
  );
}
