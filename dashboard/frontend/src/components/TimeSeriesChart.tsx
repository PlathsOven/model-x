import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
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
  fairValues: boolean;
  settlement: boolean;
  info: boolean;
}

export function TimeSeriesChart({
  episode,
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
    fairValues: true,
    settlement: true,
    info: true,
  });

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

  // Build per-cycle rows for the primary line layers (mark + per-agent FV +
  // per-agent quote bid/ask/mid). Scatter data is kept separate.
  const { chartRows, mmFillRows, hfFillRows, priceDomain } = useMemo(() => {
    if (!ts) {
      return {
        chartRows: [] as any[],
        mmFillRows: [] as any[],
        hfFillRows: [] as any[],
        priceDomain: [0, 1] as [number, number],
      };
    }
    const rows = ts.cycles.map((c) => {
      const row: Record<string, any> = {
        cycle: c.cycle_index,
        mark: c.mark,
        mm_mark: c.mm_mark ?? null,
        hf_mark: c.hf_mark ?? null,
        info: c.info,
      };
      for (const [acct, q] of Object.entries(c.quotes_by_agent)) {
        row[`bid_${acct}`] = q.bid_price;
        row[`ask_${acct}`] = q.ask_price;
      }
      for (const [acct, fv] of Object.entries(c.fv_by_agent)) {
        row[`fv_${acct}`] = fv;
      }
      return row;
    });

    const mmFills = ts.fills
      .filter((f) => f.phase === "MM")
      .map((f) => ({
        cycle: f.cycle_index,
        price: f.price,
        size: f.size,
        buyer: f.buyer,
        seller: f.seller,
        phase: f.phase,
        is_self_cross: f.is_self_cross,
      }));
    const hfFills = ts.fills
      .filter((f) => f.phase === "HF")
      .map((f) => ({
        cycle: f.cycle_index,
        price: f.price,
        size: f.size,
        buyer: f.buyer,
        seller: f.seller,
        phase: f.phase,
      }));

    const prices: number[] = [];
    for (const c of ts.cycles) {
      if (c.mark != null) prices.push(c.mark);
      for (const q of Object.values(c.quotes_by_agent)) {
        prices.push(q.bid_price, q.ask_price);
      }
      for (const v of Object.values(c.fv_by_agent)) prices.push(v);
    }
    for (const f of ts.fills) prices.push(f.price);
    if (ts.settlement != null) prices.push(ts.settlement);
    const min = prices.length ? Math.min(...prices) : 0;
    const max = prices.length ? Math.max(...prices) : 1;
    const pad = (max - min) * 0.1 || 0.1;
    return {
      chartRows: rows,
      mmFillRows: mmFills,
      hfFillRows: hfFills,
      priceDomain: [min - pad, max + pad] as [number, number],
    };
  }, [ts]);

  if (err) {
    return (
      <div className="text-sm text-red-400 font-mono whitespace-pre-wrap">
        {err}
      </div>
    );
  }
  if (!ts) return <div className="text-sm text-zinc-500">Loading…</div>;

  const infoMap = ts.info_by_cycle || {};

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Time series"
        subtitle="Marks · fills · quote ranges · fair-value estimates · info events"
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
                dataKey="cycle"
                type="number"
                domain={[0, Math.max(0, episode.num_cycles - 1)]}
                allowDecimals={false}
                stroke={AXIS_COLOR}
                tick={{ fill: AXIS_COLOR, fontSize: 11 }}
                label={{
                  value: "cycle",
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
                formatter={(value: any, name: any) => {
                  if (typeof value === "number") return [fmtPrice(value, 4), name];
                  return [value, name];
                }}
                labelFormatter={(label: any) => {
                  const info = infoMap[String(label)];
                  return info ? `Cycle ${label} · info released` : `Cycle ${label}`;
                }}
              />

              {/* Settlement reference line */}
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

              {/* Info schedule vertical lines */}
              {toggles.info &&
                ts.info_cycles.map((c) => (
                  <ReferenceLine
                    key={`info-${c}`}
                    x={c}
                    stroke="#fbbf24"
                    strokeDasharray="2 4"
                    strokeOpacity={0.6}
                  />
                ))}

              {/* Focus cycle highlight */}
              {focusCycle !== null && (
                <ReferenceLine
                  x={focusCycle}
                  stroke="#f43f5e"
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                />
              )}

              {/* Per-agent bid/ask lines (MM quote ranges) */}
              {toggles.quoteRanges &&
                ts.mm_accounts.flatMap((acct) => [
                  <Line
                    key={`bid-${acct}`}
                    type="linear"
                    dataKey={`bid_${acct}`}
                    stroke={agentColors[acct]}
                    strokeWidth={1}
                    strokeOpacity={0.35}
                    dot={{ r: 2, fill: agentColors[acct], fillOpacity: 0.5 }}
                    connectNulls={false}
                    isAnimationActive={false}
                    name={`${acct} bid`}
                    legendType="none"
                  />,
                  <Line
                    key={`ask-${acct}`}
                    type="linear"
                    dataKey={`ask_${acct}`}
                    stroke={agentColors[acct]}
                    strokeWidth={1}
                    strokeOpacity={0.35}
                    dot={{ r: 2, fill: agentColors[acct], fillOpacity: 0.5 }}
                    connectNulls={false}
                    isAnimationActive={false}
                    name={`${acct} ask`}
                    legendType="none"
                  />,
                ])}

              {/* Per-agent fair-value markers */}
              {toggles.fairValues &&
                [...ts.mm_accounts, ...ts.hf_accounts].map((acct) => (
                  <Line
                    key={`fv-${acct}`}
                    type="monotone"
                    dataKey={`fv_${acct}`}
                    stroke={agentColors[acct]}
                    strokeDasharray="4 2"
                    strokeWidth={1}
                    dot={{ r: 3, fill: agentColors[acct] }}
                    connectNulls={false}
                    isAnimationActive={false}
                    name={`${acct} fv`}
                  />
                ))}

              {/* Individual mark layers (faint) */}
              {toggles.mmMark && (
                <Line
                  type="stepAfter"
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
                  type="stepAfter"
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

              {/* Primary carry-forward mark line */}
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
              {toggles.mmFills && (
                <Scatter
                  name="MM fill"
                  data={mmFillRows}
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
              {toggles.hfFills && (
                <Scatter
                  name="HF fill"
                  data={hfFillRows}
                  fill={PHASE_COLORS.HF}
                  dataKey="price"
                  isAnimationActive={false}
                  shape={(props: any) => {
                    const r = 3 + Math.sqrt(props.payload.size);
                    return (
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={r}
                        fill={PHASE_COLORS.HF}
                        fillOpacity={0.75}
                        stroke={PHASE_COLORS.HF}
                      />
                    );
                  }}
                />
              )}
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
            label="Quote ranges"
            color="#a1a1aa"
            on={toggles.quoteRanges}
            onClick={() =>
              setToggles((t) => ({ ...t, quoteRanges: !t.quoteRanges }))
            }
          />
          <LayerToggle
            label="Fair values"
            color="#a1a1aa"
            on={toggles.fairValues}
            dashed
            onClick={() =>
              setToggles((t) => ({ ...t, fairValues: !t.fairValues }))
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

      {/* Agent color legend */}
      <Card title="Agents">
        <div className="flex flex-wrap gap-3 text-xs">
          {ts.mm_accounts.map((a) => (
            <AgentChip key={a} id={a} color={agentColors[a]} role="MM" />
          ))}
          {ts.hf_accounts.map((a) => (
            <AgentChip key={a} id={a} color={agentColors[a]} role="HF" />
          ))}
        </div>
      </Card>
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
}: {
  id: string;
  color: string;
  role: "MM" | "HF";
}) {
  return (
    <div className="flex items-center gap-2 px-2 py-1 rounded border border-zinc-800 bg-zinc-950/50">
      <span
        className="inline-block w-3 h-3 rounded-sm"
        style={{ background: color }}
      />
      <span className="font-mono text-zinc-200">{id}</span>
      <span className="text-[10px] uppercase tracking-widest text-zinc-500">
        {role}
      </span>
    </div>
  );
}
