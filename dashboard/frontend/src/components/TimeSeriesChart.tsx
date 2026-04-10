import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Episode, Timeseries } from "../types";
import { PHASE_COLORS, buildAgentColors } from "../lib/colors";
import { fmtPrice } from "../lib/format";
import { Plot, DARK_LAYOUT, PLOTLY_CONFIG } from "../lib/plotly-theme";

interface LayerToggles {
  mark: boolean;
  mmFills: boolean;
  hfFills: boolean;
  quoteRanges: boolean;
  settlement: boolean;
  info: boolean;
}

// Format an epoch-ms timestamp as "HH:MM" in local time.
function formatTime(epochMs: number): string {
  const d = new Date(epochMs);
  return d.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

interface ChartRow {
  xIndex: number;
  phase: "MM" | "HF" | "S";
  phaseId: string | null;
  mark: number | null;
  info: string | null;
}

interface FillRow {
  xIndex: number;
  phaseId: string;
  price: number;
  size: number;
  buyer: string;
  seller: string;
  is_self_cross: boolean;
  taker_hf?: string;
  direction?: "up" | "down";
}

export function TimeSeriesChart({
  episode: _episode,
  dataVersion,
  focusPhaseId,
  onClearFocus,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  focusPhaseId: string | null;
  onClearFocus: () => void;
  marketId?: string | null;
}) {
  const [ts, setTs] = useState<Timeseries | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toggles, setToggles] = useState<LayerToggles>({
    mark: true,
    mmFills: true,
    hfFills: true,
    quoteRanges: true,
    settlement: true,
    info: true,
  });
  const [hiddenAgents, setHiddenAgents] = useState<Set<string>>(new Set());

  useEffect(() => {
    api
      .timeseries(marketId)
      .then(setTs)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  const agentColors = useMemo(() => {
    if (!ts) return {};
    return buildAgentColors(ts.mm_accounts, ts.hf_accounts);
  }, [ts]);

  const hfAccountSet = useMemo(
    () => new Set(ts?.hf_accounts ?? []),
    [ts]
  );

  // Build chart rows, per-MM whisker rows, and fill rows.
  const {
    chartRows,
    settleX: _settleX,
    phaseIdSet,
    whiskerByMm,
    mmFillRows,
    hfFillRows,
    priceDomain,
  } = useMemo(() => {
    if (!ts) {
      return {
        chartRows: [] as ChartRow[],
        settleX: null as number | null,
        phaseIdSet: new Set<string>(),
        whiskerByMm: {} as Record<
          string,
          { xIndex: number; bid: number; ask: number; mid: number }[]
        >,
        mmFillRows: [] as FillRow[],
        hfFillRows: [] as FillRow[],
        priceDomain: [0, 1] as [number, number],
      };
    }

    const phaseX: Record<string, number> = {};
    const rows: ChartRow[] = [];
    const phaseIdSet = new Set<string>();
    const whiskerByMm: Record<
      string,
      { xIndex: number; bid: number; ask: number; mid: number }[]
    > = {};
    for (const a of ts.mm_accounts) whiskerByMm[a] = [];

    let lastMark: number | null = null;
    for (const p of ts.phases) {
      const xMs = p.timestamp * 1000;
      phaseIdSet.add(p.phase_id);
      phaseX[p.phase_id] = xMs;

      if (p.mark != null) lastMark = p.mark;
      const phaseType = p.phase_type as "MM" | "HF";
      rows.push({
        xIndex: xMs,
        phase: phaseType,
        phaseId: p.phase_id,
        mark: lastMark,
        info: p.info ?? null,
      });

      if (phaseType === "MM" && p.quotes_by_agent) {
        for (const [acct, q] of Object.entries(p.quotes_by_agent)) {
          whiskerByMm[acct]?.push({
            xIndex: xMs,
            mid: q.mid,
            bid: q.bid_price,
            ask: q.ask_price,
          });
        }
      }
    }

    let settleX: number | null = null;
    if (ts.settlement != null) {
      const lastRow = rows[rows.length - 1];
      const lastX = lastRow?.xIndex ?? 0;
      settleX = lastX + 60_000;
      lastMark = ts.settlement;
      rows.push({
        xIndex: settleX,
        phase: "S",
        phaseId: null,
        mark: lastMark,
        info: null,
      });
    }

    const mmFillRows: FillRow[] = ts.fills
      .filter((f) => f.phase === "MM")
      .map((f) => ({
        xIndex: phaseX[f.phase_id] ?? f.timestamp * 1000,
        phaseId: f.phase_id,
        price: f.price,
        size: f.size,
        buyer: f.buyer,
        seller: f.seller,
        is_self_cross: f.is_self_cross,
      }));
    const hfFillRows: FillRow[] = ts.fills
      .filter((f) => f.phase === "HF")
      .map((f) => {
        const hfIsBuyer = hfAccountSet.has(f.buyer);
        const taker_hf = hfIsBuyer ? f.buyer : f.seller;
        return {
          xIndex: phaseX[f.phase_id] ?? f.timestamp * 1000,
          phaseId: f.phase_id,
          price: f.price,
          size: f.size,
          buyer: f.buyer,
          seller: f.seller,
          is_self_cross: f.is_self_cross,
          taker_hf,
          direction: hfIsBuyer ? ("up" as const) : ("down" as const),
        };
      });

    const prices: number[] = [];
    for (const p of ts.phases) {
      if (p.mark != null) prices.push(p.mark);
      for (const q of Object.values(p.quotes_by_agent)) {
        prices.push(q.bid_price, q.ask_price);
      }
    }
    for (const f of ts.fills) prices.push(f.price);
    if (ts.settlement != null) prices.push(ts.settlement);
    const min = prices.length ? Math.min(...prices) : 0;
    const max = prices.length ? Math.max(...prices) : 1;
    const pad = (max - min) * 0.1 || 0.1;

    return {
      chartRows: rows,
      settleX,
      phaseIdSet,
      whiskerByMm,
      mmFillRows,
      hfFillRows,
      priceDomain: [min - pad, max + pad] as [number, number],
    };
  }, [ts, hfAccountSet]);

  // Apply per-agent visibility filter.
  const visibleMmFillRows = useMemo(
    () =>
      mmFillRows.filter(
        (f) => !hiddenAgents.has(f.buyer) && !hiddenAgents.has(f.seller)
      ),
    [mmFillRows, hiddenAgents]
  );
  const visibleHfFillRows = useMemo(
    () =>
      hfFillRows.filter(
        (f) => !hiddenAgents.has(f.buyer) && !hiddenAgents.has(f.seller)
      ),
    [hfFillRows, hiddenAgents]
  );

  // Compute focus x position.
  const focusXIndex = (() => {
    if (focusPhaseId == null || !phaseIdSet.has(focusPhaseId)) return null;
    const row = chartRows.find((r) => r.phaseId === focusPhaseId);
    return row?.xIndex ?? null;
  })();

  // Build Plotly traces.
  const { traces, layout } = useMemo(() => {
    const traces: any[] = [];

    // --- Mark line (step-after / carry-forward) ---
    if (toggles.mark && chartRows.length > 0) {
      const markX: number[] = [];
      const markY: (number | null)[] = [];
      const markText: string[] = [];
      for (const r of chartRows) {
        markX.push(r.xIndex);
        markY.push(r.mark);
        const label =
          r.phase === "S"
            ? "Settlement"
            : `${formatTime(r.xIndex)} ${r.phase === "MM" ? "MM" : "HF"}`;
        markText.push(
          `${label}<br>Mark: ${r.mark != null ? fmtPrice(r.mark, 4) : "---"}`
        );
      }
      traces.push({
        x: markX,
        y: markY,
        type: "scatter",
        mode: "lines",
        name: "Mark",
        line: { color: "#ffffff", width: 2.5, shape: "hv" },
        connectgaps: true,
        hovertemplate: "%{text}<extra></extra>",
        text: markText,
      });
    }

    // --- MM fills scatter ---
    if (toggles.mmFills && visibleMmFillRows.length > 0) {
      traces.push({
        x: visibleMmFillRows.map((f) => f.xIndex),
        y: visibleMmFillRows.map((f) => f.price),
        type: "scatter",
        mode: "markers",
        name: "MM fills",
        marker: {
          color: PHASE_COLORS.MM,
          size: visibleMmFillRows.map((f) => 6 + Math.sqrt(f.size) * 2),
          symbol: "circle",
          opacity: 0.75,
          line: { color: PHASE_COLORS.MM, width: 1 },
        },
        hovertemplate: visibleMmFillRows.map(
          (f) =>
            `MM fill<br>${f.buyer} <-> ${f.seller}<br>Price: ${fmtPrice(f.price, 4)}<br>Size: ${f.size}<extra></extra>`
        ),
      });
    }

    // --- HF fills scatter (per agent, up/down triangles) ---
    if (toggles.hfFills && ts) {
      for (const acct of ts.hf_accounts) {
        if (hiddenAgents.has(acct)) continue;
        const fills = visibleHfFillRows.filter((f) => f.taker_hf === acct);
        if (fills.length === 0) continue;
        const color = agentColors[acct];
        traces.push({
          x: fills.map((f) => f.xIndex),
          y: fills.map((f) => f.price),
          type: "scatter",
          mode: "markers",
          name: `${acct} taker`,
          marker: {
            color: color,
            size: fills.map((f) => 8 + Math.sqrt(f.size) * 2),
            symbol: fills.map((f) =>
              f.direction === "up" ? "triangle-up" : "triangle-down"
            ),
            opacity: 0.9,
            line: { color: color, width: 1.5 },
          },
          hovertemplate: fills.map(
            (f) =>
              `${acct} ${f.direction === "up" ? "BUY" : "SELL"}<br>${f.buyer} -> ${f.seller}<br>Price: ${fmtPrice(f.price, 4)}<br>Size: ${f.size}<extra></extra>`
          ),
        });
      }
    }

    // --- Quote whiskers (per-MM bid/ask ranges) ---
    if (toggles.quoteRanges && ts) {
      const visibleMmAccounts = ts.mm_accounts.filter(
        (a) => !hiddenAgents.has(a)
      );
      for (const acct of visibleMmAccounts) {
        const whiskers = whiskerByMm[acct] || [];
        if (whiskers.length === 0) continue;
        const color = agentColors[acct];
        // Draw bid-ask range as a candlestick-like trace using error bars on the mid.
        traces.push({
          x: whiskers.map((w) => w.xIndex),
          y: whiskers.map((w) => w.mid),
          type: "scatter",
          mode: "markers",
          name: `${acct} quotes`,
          marker: {
            color: color,
            size: 3,
            symbol: "line-ew",
            line: { color: color, width: 1.5 },
          },
          error_y: {
            type: "data",
            symmetric: false,
            array: whiskers.map((w) => w.ask - w.mid),
            arrayminus: whiskers.map((w) => w.mid - w.bid),
            color: color,
            thickness: 2,
            width: 4,
          },
          hovertemplate: whiskers.map(
            (w) =>
              `${acct} quotes<br>Ask: ${fmtPrice(w.ask, 4)}<br>Mid: ${fmtPrice(w.mid, 4)}<br>Bid: ${fmtPrice(w.bid, 4)}<extra></extra>`
          ),
        });
      }
    }

    // --- Build layout shapes for reference lines ---
    const shapes: any[] = [];
    const annotations: any[] = [];

    // Settlement line (horizontal dashed emerald)
    if (toggles.settlement && ts?.settlement != null) {
      shapes.push({
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: ts.settlement,
        y1: ts.settlement,
        line: { color: "#10b981", width: 1.5, dash: "dash" },
      });
      annotations.push({
        xref: "paper",
        x: 1,
        yref: "y",
        y: ts.settlement,
        text: `settlement ${fmtPrice(ts.settlement, 3)}`,
        showarrow: false,
        font: { color: "#10b981", size: 11 },
        xanchor: "right",
        yanchor: "bottom",
      });
    }

    // Info event vertical lines (amber dashed)
    if (toggles.info && ts) {
      for (const pid of ts.info_phases) {
        if (!phaseIdSet.has(pid)) continue;
        const row = chartRows.find((r) => r.phaseId === pid);
        if (!row) continue;
        shapes.push({
          type: "line",
          xref: "x",
          x0: row.xIndex,
          x1: row.xIndex,
          yref: "paper",
          y0: 0,
          y1: 1,
          line: { color: "#f59e0b", width: 1, dash: "dot" },
          opacity: 0.6,
        });
      }
    }

    // Focus highlight (vertical dashed red)
    if (focusXIndex !== null) {
      shapes.push({
        type: "line",
        xref: "x",
        x0: focusXIndex,
        x1: focusXIndex,
        yref: "paper",
        y0: 0,
        y1: 1,
        line: { color: "#f43f5e", width: 1.5, dash: "dash" },
      });
    }

    // Build tick values and labels for x-axis.
    const layout: any = {
      ...DARK_LAYOUT,
      xaxis: {
        ...DARK_LAYOUT.xaxis,
        type: "date",
        tickfont: { size: 10, color: "#71717a" },
      },
      yaxis: {
        ...DARK_LAYOUT.yaxis,
        range: priceDomain,
        tickformat: ".3f",
      },
      shapes,
      annotations,
      showlegend: false,
      margin: { t: 10, r: 30, b: 40, l: 60 },
    };

    return { traces, layout };
  }, [
    chartRows,
    toggles,
    visibleMmFillRows,
    visibleHfFillRows,
    ts,
    agentColors,
    hiddenAgents,
    whiskerByMm,
    phaseIdSet,
    focusXIndex,
    priceDomain,
  ]);

  if (err) {
    return (
      <div className="text-sm text-red-400 font-mono whitespace-pre-wrap">
        {err}
      </div>
    );
  }
  if (!ts) return <div className="text-sm text-zinc-500">Loading...</div>;

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0">
        <Plot
          data={traces}
          layout={layout}
          config={PLOTLY_CONFIG as any}
          useResizeHandler
          style={{ width: "100%", height: "100%" }}
        />
      </div>

      {/* Layer toggles + agent chips — compact row pinned below chart */}
      <div className="flex flex-wrap items-center gap-2 text-xs px-1 pt-2 shrink-0">
        <LayerToggle
          label="Mark"
          color="#ffffff"
          on={toggles.mark}
          onClick={() => setToggles((t) => ({ ...t, mark: !t.mark }))}
        />
        <LayerToggle
          label="MM fills"
          color={PHASE_COLORS.MM}
          on={toggles.mmFills}
          onClick={() => setToggles((t) => ({ ...t, mmFills: !t.mmFills }))}
        />
        <LayerToggle
          label="HF fills"
          color={PHASE_COLORS.HF}
          on={toggles.hfFills}
          onClick={() => setToggles((t) => ({ ...t, hfFills: !t.hfFills }))}
        />
        <LayerToggle
          label="Whiskers"
          color="#a1a1aa"
          on={toggles.quoteRanges}
          onClick={() =>
            setToggles((t) => ({ ...t, quoteRanges: !t.quoteRanges }))
          }
        />
        <LayerToggle
          label="Settlement"
          color="#10b981"
          on={toggles.settlement}
          dashed
          onClick={() =>
            setToggles((t) => ({ ...t, settlement: !t.settlement }))
          }
        />
        <LayerToggle
          label="Info"
          color="#f59e0b"
          on={toggles.info}
          dashed
          onClick={() => setToggles((t) => ({ ...t, info: !t.info }))}
        />

        <span className="w-px h-4 bg-zinc-700 mx-1" />

        {ts.mm_accounts.map((a) => (
          <AgentChip
            key={a}
            id={a}
            color={agentColors[a]}
            role="MM"
            hidden={hiddenAgents.has(a)}
            onClick={() =>
              setHiddenAgents((s) => {
                const next = new Set(s);
                if (next.has(a)) next.delete(a);
                else next.add(a);
                return next;
              })
            }
          />
        ))}
        {ts.hf_accounts.map((a) => (
          <AgentChip
            key={a}
            id={a}
            color={agentColors[a]}
            role="HF"
            hidden={hiddenAgents.has(a)}
            onClick={() =>
              setHiddenAgents((s) => {
                const next = new Set(s);
                if (next.has(a)) next.delete(a);
                else next.add(a);
                return next;
              })
            }
          />
        ))}

        {hiddenAgents.size > 0 && (
          <button
            onClick={() => setHiddenAgents(new Set())}
            className="text-xs text-zinc-400 hover:text-zinc-100 border border-zinc-700 rounded px-2 py-1"
          >
            show all
          </button>
        )}

        {focusPhaseId !== null && (
          <>
            <span className="w-px h-4 bg-zinc-700 mx-1" />
            <button
              onClick={onClearFocus}
              className="text-xs text-red-400 hover:text-red-200 border border-red-800 rounded px-2 py-1"
            >
              clear focus
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function LayerToggle({
  label,
  color,
  on,
  dashed = false,
  onClick,
}: {
  label: string;
  color: string;
  on: boolean;
  dashed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "flex items-center gap-2 rounded border px-2 py-1 transition " +
        (on
          ? "border-zinc-700 bg-zinc-800 text-zinc-100"
          : "border-zinc-800 bg-zinc-900 text-zinc-500 hover:text-zinc-300")
      }
    >
      <span
        className="inline-block w-5 h-[2px]"
        style={{
          background: on ? color : "transparent",
          borderTop: dashed && on ? `2px dashed ${color}` : undefined,
        }}
      />
      {label}
    </button>
  );
}

function AgentChip({
  id,
  color,
  role,
  hidden,
  onClick,
}: {
  id: string;
  color: string;
  role: "MM" | "HF";
  hidden: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={hidden ? "click to show" : "click to hide"}
      className={
        "flex items-center gap-2 px-2 py-1 rounded border transition " +
        (hidden
          ? "border-zinc-800 bg-zinc-950/30 opacity-40 hover:opacity-70"
          : "border-zinc-800 bg-zinc-950/50 hover:border-zinc-700")
      }
    >
      <span
        className="inline-block w-3 h-3 rounded-sm"
        style={{ background: color }}
      />
      <span
        className={
          "font-mono " +
          (hidden ? "text-zinc-500 line-through" : "text-zinc-200")
        }
      >
        {id}
      </span>
      <span className="text-[10px] uppercase tracking-widest text-zinc-500">
        {role}
      </span>
    </button>
  );
}
