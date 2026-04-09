import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Customized,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import type { Episode, Timeseries } from "../types";
import {
  AXIS_COLOR,
  GRID_COLOR,
  PHASE_COLORS,
  buildAgentColors,
} from "../lib/colors";
import { fmtPrice } from "../lib/format";
import { Card, SectionHeader } from "./ui";

interface LayerToggles {
  mark: boolean;
  mmMark: boolean;
  hfMark: boolean;
  mmFills: boolean;
  hfFills: boolean;
  quoteRanges: boolean;
  settlement: boolean;
  info: boolean;
}

// Map (cycle, phase) → numeric x position. MM phase of cycle k is at 2k,
// HF phase at 2k+1, settlement at 2*numCycles. Using a numeric XAxis avoids
// Recharts' category-merging behavior, which reorders / drops ticks when
// Scatter components have their own `data` arrays.
function xIndexFor(cycle: number, phase: "MM" | "HF"): number {
  return cycle * 2 + (phase === "MM" ? 0 : 1);
}

function phaseTickLabel(cycle: number, phase: "MM" | "HF"): string {
  return `${cycle}·${phase === "MM" ? "M" : "H"}`;
}

interface ChartRow {
  xIndex: number;
  phase: "MM" | "HF" | "S";
  cycle: number | null;
  mm_mark: number | null;
  hf_mark: number | null;
  mark: number | null;
  info: string | null;
}

interface FillRow {
  xIndex: number;
  cycle: number;
  price: number;
  size: number;
  buyer: string;
  seller: string;
  is_self_cross: boolean;
  // HF only:
  taker_hf?: string;
  direction?: "up" | "down";
}

export function TimeSeriesChart({
  episode: _episode,
  dataVersion,
  focusCycle,
  onClearFocus,
}: {
  episode: Episode;
  dataVersion: number;
  focusCycle: number | null;
  onClearFocus: () => void;
}) {
  const [ts, setTs] = useState<Timeseries | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toggles, setToggles] = useState<LayerToggles>({
    mark: true,
    mmMark: false,
    hfMark: false,
    mmFills: true,
    hfFills: true,
    quoteRanges: true,
    settlement: true,
    info: true,
  });
  const [hiddenAgents, setHiddenAgents] = useState<Set<string>>(new Set());

  useEffect(() => {
    api
      .timeseries()
      .then(setTs)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion]);

  const agentColors = useMemo(() => {
    if (!ts) return {};
    return buildAgentColors(ts.mm_accounts, ts.hf_accounts);
  }, [ts]);

  const hfAccountSet = useMemo(
    () => new Set(ts?.hf_accounts ?? []),
    [ts]
  );

  // Build chart rows (one per phase tick + settlement), per-MM whisker rows,
  // and fill rows. Everything keys off `xIndex` (numeric) so the XAxis stays
  // ordered no matter what subset of layers / agents is visible.
  const {
    chartRows,
    chartRowByX,
    settleX,
    cyclesWithMmRow,
    whiskerByMm,
    mmFillRows,
    hfFillRows,
    tickValues,
    tickLabels,
    xDomain,
    priceDomain,
  } = useMemo(() => {
    if (!ts) {
      return {
        chartRows: [] as ChartRow[],
        chartRowByX: {} as Record<number, ChartRow>,
        settleX: null as number | null,
        cyclesWithMmRow: new Set<number>(),
        whiskerByMm: {} as Record<string, { xIndex: number; bid: number; ask: number; mid: number }[]>,
        mmFillRows: [] as FillRow[],
        hfFillRows: [] as FillRow[],
        tickValues: [] as number[],
        tickLabels: {} as Record<number, string>,
        xDomain: [-0.5, 0.5] as [number, number],
        priceDomain: [0, 1] as [number, number],
      };
    }

    const rows: ChartRow[] = [];
    const cyclesWithMmRow = new Set<number>();
    const whiskerByMm: Record<string, { xIndex: number; bid: number; ask: number; mid: number }[]> = {};
    for (const a of ts.mm_accounts) whiskerByMm[a] = [];

    let lastMark: number | null = null;
    for (const c of ts.cycles) {
      const mmX = xIndexFor(c.cycle_index, "MM");
      cyclesWithMmRow.add(c.cycle_index);
      if (c.mm_mark != null) lastMark = c.mm_mark;
      rows.push({
        xIndex: mmX,
        phase: "MM",
        cycle: c.cycle_index,
        mm_mark: c.mm_mark ?? null,
        hf_mark: null,
        mark: lastMark,
        info: c.info,
      });
      for (const [acct, q] of Object.entries(c.quotes_by_agent)) {
        whiskerByMm[acct]?.push({
          xIndex: mmX,
          mid: q.mid,
          bid: q.bid_price,
          ask: q.ask_price,
        });
      }

      const hfX = xIndexFor(c.cycle_index, "HF");
      if (c.hf_mark != null) lastMark = c.hf_mark;
      rows.push({
        xIndex: hfX,
        phase: "HF",
        cycle: c.cycle_index,
        mm_mark: null,
        hf_mark: c.hf_mark ?? null,
        mark: lastMark,
        info: null,
      });
    }

    const numCycles = ts.cycles.length;
    let settleX: number | null = null;
    if (ts.settlement != null) {
      settleX = numCycles * 2;
      lastMark = ts.settlement;
      rows.push({
        xIndex: settleX,
        phase: "S",
        cycle: null,
        mm_mark: null,
        hf_mark: null,
        mark: lastMark,
        info: null,
      });
    }

    const tickValues: number[] = [];
    const tickLabels: Record<number, string> = {};
    for (let i = 0; i < numCycles; i++) {
      tickValues.push(i * 2, i * 2 + 1);
      tickLabels[i * 2] = phaseTickLabel(i, "MM");
      tickLabels[i * 2 + 1] = phaseTickLabel(i, "HF");
    }
    if (settleX != null) {
      tickValues.push(settleX);
      tickLabels[settleX] = "Settle";
    }
    const lastTick =
      settleX ?? (numCycles > 0 ? numCycles * 2 - 1 : 0);
    const xDomain: [number, number] = [-0.5, lastTick + 0.5];

    const chartRowByX: Record<number, ChartRow> = {};
    for (const r of rows) chartRowByX[r.xIndex] = r;

    const mmFillRows: FillRow[] = ts.fills
      .filter((f) => f.phase === "MM")
      .map((f) => ({
        xIndex: xIndexFor(f.cycle_index, "MM"),
        cycle: f.cycle_index,
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
          xIndex: xIndexFor(f.cycle_index, "HF"),
          cycle: f.cycle_index,
          price: f.price,
          size: f.size,
          buyer: f.buyer,
          seller: f.seller,
          is_self_cross: f.is_self_cross,
          taker_hf,
          direction: hfIsBuyer ? "up" : "down",
        };
      });

    const prices: number[] = [];
    for (const c of ts.cycles) {
      if (c.mm_mark != null) prices.push(c.mm_mark);
      if (c.hf_mark != null) prices.push(c.hf_mark);
      for (const q of Object.values(c.quotes_by_agent)) {
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
      chartRowByX,
      settleX,
      cyclesWithMmRow,
      whiskerByMm,
      mmFillRows,
      hfFillRows,
      tickValues,
      tickLabels,
      xDomain,
      priceDomain: [min - pad, max + pad] as [number, number],
    };
  }, [ts, hfAccountSet]);

  // Apply per-agent visibility filter to fills + whiskers used for rendering
  // and tooltip display.
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

  // Pre-bucket fills by xIndex for the custom tooltip — Recharts' default
  // tooltip collapses multiple Scatter points per series at the same x to
  // a single entry, so we render the tooltip body ourselves.
  const fillsByX = useMemo(() => {
    const out: Record<number, FillRow[]> = {};
    for (const f of visibleMmFillRows) {
      (out[f.xIndex] ||= []).push(f);
    }
    for (const f of visibleHfFillRows) {
      (out[f.xIndex] ||= []).push(f);
    }
    return out;
  }, [visibleMmFillRows, visibleHfFillRows]);

  if (err) {
    return (
      <div className="text-sm text-red-400 font-mono whitespace-pre-wrap">
        {err}
      </div>
    );
  }
  if (!ts) return <div className="text-sm text-zinc-500">Loading…</div>;

  const infoMap = ts.info_by_cycle || {};
  const focusXIndex =
    focusCycle != null && cyclesWithMmRow.has(focusCycle)
      ? xIndexFor(focusCycle, "MM")
      : null;

  const visibleMmAccounts = ts.mm_accounts.filter(
    (a) => !hiddenAgents.has(a)
  );

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Time series"
        subtitle="Marks · fills · quote whiskers · info events"
        action={
          focusCycle !== null && (
            <button
              onClick={onClearFocus}
              className="text-xs text-zinc-400 hover:text-zinc-100 border border-zinc-700 rounded px-2 py-1"
            >
              clear focus on cycle {focusCycle}
            </button>
          )
        }
      />

      <Card>
        <div className="h-[540px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart
              data={chartRows}
              margin={{ top: 20, right: 30, left: 10, bottom: 10 }}
            >
              <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" />
              <XAxis
                type="number"
                dataKey="xIndex"
                domain={xDomain}
                ticks={tickValues}
                tickFormatter={(v: number) => tickLabels[v] ?? ""}
                interval={0}
                allowDecimals={false}
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 10 }}
                label={{
                  value: "cycle · phase",
                  position: "insideBottom",
                  fill: AXIS_COLOR,
                  offset: -5,
                  fontSize: 11,
                }}
              />
              <YAxis
                domain={priceDomain}
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 11 }}
                tickFormatter={(v) => fmtPrice(v, 3)}
                width={70}
              />
              <Tooltip
                cursor={{ stroke: AXIS_COLOR, strokeDasharray: "3 3" }}
                content={(props: any) => (
                  <ChartTooltip
                    active={props.active}
                    label={props.label}
                    chartRowByX={chartRowByX}
                    fillsByX={fillsByX}
                    infoMap={infoMap}
                    settleX={settleX}
                    agentColors={agentColors}
                    hfAccountSet={hfAccountSet}
                  />
                )}
              />

              {/* Settlement reference line (horizontal at settlement value) */}
              {toggles.settlement && ts.settlement != null && (
                <ReferenceLine
                  y={ts.settlement}
                  stroke="#34d399"
                  strokeDasharray="6 3"
                  label={{
                    value: `settlement ${fmtPrice(ts.settlement, 3)}`,
                    fill: "#34d399",
                    fontSize: 11,
                    position: "insideTopRight",
                  }}
                />
              )}

              {/* Info schedule vertical lines (anchored to each cycle's MM tick) */}
              {toggles.info &&
                ts.info_cycles
                  .filter((c) => cyclesWithMmRow.has(c))
                  .map((c) => (
                    <ReferenceLine
                      key={`info-${c}`}
                      x={xIndexFor(c, "MM")}
                      stroke="#fbbf24"
                      strokeDasharray="2 4"
                      strokeOpacity={0.6}
                    />
                  ))}

              {/* Focus cycle highlight (anchored to MM tick) */}
              {focusXIndex !== null && (
                <ReferenceLine
                  x={focusXIndex}
                  stroke="#f43f5e"
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                />
              )}

              {/* Per-MM quote whiskers — drawn via Customized so we can read
                  the chart's xScale/yScale and offset multiple MMs horizontally
                  at the same tick. */}
              {toggles.quoteRanges && (
                <Customized
                  component={(cprops: any) => {
                    const xAxisMap = cprops.xAxisMap;
                    const yAxisMap = cprops.yAxisMap;
                    if (!xAxisMap || !yAxisMap) return null;
                    const xAxis: any = Object.values(xAxisMap)[0];
                    const yAxis: any = Object.values(yAxisMap)[0];
                    if (!xAxis?.scale || !yAxis?.scale) return null;
                    const xScale = xAxis.scale;
                    const yScale = yAxis.scale;
                    const numMms = visibleMmAccounts.length;
                    const elements: any[] = [];
                    visibleMmAccounts.forEach((acct, mmIdx) => {
                      const offsetPx =
                        numMms > 1 ? (mmIdx - (numMms - 1) / 2) * 6 : 0;
                      const color = agentColors[acct];
                      for (const w of whiskerByMm[acct] || []) {
                        const cxRaw = xScale(w.xIndex);
                        if (cxRaw == null || Number.isNaN(cxRaw)) continue;
                        const cx = cxRaw + offsetPx;
                        const yBid = yScale(w.bid);
                        const yAsk = yScale(w.ask);
                        elements.push(
                          <g key={`whisker-${acct}-${w.xIndex}`}>
                            <line
                              x1={cx}
                              y1={yBid}
                              x2={cx}
                              y2={yAsk}
                              stroke={color}
                              strokeWidth={2}
                              strokeLinecap="round"
                            />
                            <line
                              x1={cx - 4}
                              y1={yBid}
                              x2={cx + 4}
                              y2={yBid}
                              stroke={color}
                              strokeWidth={2}
                              strokeLinecap="round"
                            />
                            <line
                              x1={cx - 4}
                              y1={yAsk}
                              x2={cx + 4}
                              y2={yAsk}
                              stroke={color}
                              strokeWidth={2}
                              strokeLinecap="round"
                            />
                          </g>
                        );
                      }
                    });
                    return <g>{elements}</g>;
                  }}
                />
              )}

              {/* Individual mark layers (faint) */}
              {toggles.mmMark && (
                <Line
                  type="linear"
                  dataKey="mm_mark"
                  stroke="#a78bfa"
                  strokeWidth={1}
                  strokeOpacity={0.5}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                  name="mm_mark"
                />
              )}
              {toggles.hfMark && (
                <Line
                  type="linear"
                  dataKey="hf_mark"
                  stroke="#22d3ee"
                  strokeWidth={1}
                  strokeOpacity={0.5}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                  name="hf_mark"
                />
              )}

              {/* Primary carry-forward mark line (one point per phase tick) */}
              {toggles.mark && (
                <Line
                  type="stepAfter"
                  dataKey="mark"
                  stroke="#ffffff"
                  strokeWidth={2.5}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                  name="mark (carry-forward)"
                />
              )}

              {/* Fill scatters */}
              {toggles.mmFills && visibleMmFillRows.length > 0 && (
                <Scatter
                  name="MM fill"
                  data={visibleMmFillRows}
                  fill={PHASE_COLORS.MM}
                  dataKey="price"
                  isAnimationActive={false}
                  shape={(props: any) => {
                    const r = 3 + Math.sqrt(props.payload.size);
                    return (
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={r}
                        fill={PHASE_COLORS.MM}
                        fillOpacity={0.75}
                        stroke={PHASE_COLORS.MM}
                      />
                    );
                  }}
                />
              )}
              {toggles.hfFills &&
                ts.hf_accounts.map((acct) => {
                  if (hiddenAgents.has(acct)) return null;
                  const fills = visibleHfFillRows.filter(
                    (f) => f.taker_hf === acct
                  );
                  if (fills.length === 0) return null;
                  const color = agentColors[acct];
                  return (
                    <Scatter
                      key={`hf-fill-${acct}`}
                      name={`${acct} taker`}
                      data={fills}
                      fill={color}
                      dataKey="price"
                      isAnimationActive={false}
                      shape={(props: any) => {
                        const { cx, cy, payload } = props;
                        const r = 4 + Math.sqrt(payload.size);
                        const points =
                          payload.direction === "up"
                            ? `${cx},${cy - r} ${cx - r},${cy + r} ${cx + r},${cy + r}`
                            : `${cx},${cy + r} ${cx - r},${cy - r} ${cx + r},${cy - r}`;
                        return (
                          <polygon
                            points={points}
                            fill={color}
                            fillOpacity={0.9}
                            stroke={color}
                            strokeWidth={1.5}
                          />
                        );
                      }}
                    />
                  );
                })}
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* Layer legend + toggles */}
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <LayerToggle
            label="Carry-fwd mark"
            color="#ffffff"
            on={toggles.mark}
            onClick={() => setToggles((t) => ({ ...t, mark: !t.mark }))}
          />
          <LayerToggle
            label="mm_mark"
            color="#a78bfa"
            on={toggles.mmMark}
            onClick={() => setToggles((t) => ({ ...t, mmMark: !t.mmMark }))}
          />
          <LayerToggle
            label="hf_mark"
            color="#22d3ee"
            on={toggles.hfMark}
            onClick={() => setToggles((t) => ({ ...t, hfMark: !t.hfMark }))}
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
            label="Quote whiskers"
            color="#a1a1aa"
            on={toggles.quoteRanges}
            onClick={() =>
              setToggles((t) => ({ ...t, quoteRanges: !t.quoteRanges }))
            }
          />
          <LayerToggle
            label="Settlement"
            color="#34d399"
            on={toggles.settlement}
            dashed
            onClick={() =>
              setToggles((t) => ({ ...t, settlement: !t.settlement }))
            }
          />
          <LayerToggle
            label="Info events"
            color="#fbbf24"
            on={toggles.info}
            dashed
            onClick={() => setToggles((t) => ({ ...t, info: !t.info }))}
          />
        </div>
      </Card>

      {/* Agent color legend (clickable to toggle visibility) */}
      <Card
        title="Agents"
        action={
          hiddenAgents.size > 0 ? (
            <button
              onClick={() => setHiddenAgents(new Set())}
              className="text-xs text-zinc-400 hover:text-zinc-100 border border-zinc-700 rounded px-2 py-1"
            >
              show all ({hiddenAgents.size} hidden)
            </button>
          ) : null
        }
      >
        <div className="flex flex-wrap gap-3 text-xs">
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
        </div>
      </Card>
    </div>
  );
}

function ChartTooltip({
  active,
  label,
  chartRowByX,
  fillsByX,
  infoMap,
  settleX,
  agentColors,
  hfAccountSet,
}: {
  active: boolean | undefined;
  label: number | string | undefined;
  chartRowByX: Record<number, ChartRow>;
  fillsByX: Record<number, FillRow[]>;
  infoMap: Record<string, string>;
  settleX: number | null;
  agentColors: Record<string, string>;
  hfAccountSet: Set<string>;
}) {
  if (!active || label == null) return null;
  const xIdx = typeof label === "number" ? label : Number(label);
  if (Number.isNaN(xIdx)) return null;
  const row = chartRowByX[xIdx];
  if (!row) return null;

  let header: string;
  if (settleX != null && xIdx === settleX) {
    header = "Settlement";
  } else if (row.cycle == null) {
    header = "—";
  } else {
    const phaseLabel = row.phase === "MM" ? "MM phase" : "HF phase";
    const info = infoMap[String(row.cycle)];
    const infoSuffix = info && row.phase === "MM" ? " · info released" : "";
    header = `Cycle ${row.cycle} — ${phaseLabel}${infoSuffix}`;
  }

  const fills = fillsByX[xIdx] || [];

  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/95 px-3 py-2 text-xs font-mono shadow-lg max-w-md">
      <div className="text-zinc-100 mb-1">{header}</div>
      {row.mark != null && (
        <div className="text-zinc-300">
          mark (carry-fwd): {fmtPrice(row.mark, 4)}
        </div>
      )}
      {row.mm_mark != null && (
        <div className="text-zinc-400">
          mm_mark: {fmtPrice(row.mm_mark, 4)}
        </div>
      )}
      {row.hf_mark != null && (
        <div className="text-zinc-400">
          hf_mark: {fmtPrice(row.hf_mark, 4)}
        </div>
      )}
      {row.info && row.phase === "MM" && (
        <div className="text-amber-300 mt-1 whitespace-pre-wrap">
          info: {row.info}
        </div>
      )}
      {fills.length > 0 && (
        <div className="mt-1.5 border-t border-zinc-800 pt-1.5">
          <div className="text-zinc-500 mb-1">
            {fills.length} fill{fills.length > 1 ? "s" : ""}
          </div>
          {fills.map((f, i) => {
            const buyerColor = agentColors[f.buyer] || "#a1a1aa";
            const sellerColor = agentColors[f.seller] || "#a1a1aa";
            const hfIsBuyer = hfAccountSet.has(f.buyer);
            // Highlight the HF taker side for HF-phase fills.
            const arrow =
              row.phase === "HF" ? (hfIsBuyer ? "←" : "→") : "↔";
            return (
              <div
                key={`${f.xIndex}-${i}`}
                className="text-zinc-300 flex items-center gap-1"
              >
                <span style={{ color: buyerColor }}>{f.buyer}</span>
                <span className="text-zinc-500">{arrow}</span>
                <span style={{ color: sellerColor }}>{f.seller}</span>
                <span className="text-zinc-500">@</span>
                <span className="text-zinc-100">{fmtPrice(f.price, 4)}</span>
                <span className="text-zinc-500">×</span>
                <span className="text-zinc-100">{f.size}</span>
              </div>
            );
          })}
        </div>
      )}
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
