import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Episode, FillRow } from "../types";
import { fmtInt, fmtPrice } from "../lib/format";
import { Badge, Card, SectionHeader } from "./ui";

type PhaseFilter = "ALL" | "MM" | "HF";
type SortKey = "cycle_index" | "price" | "size";

export function TradeLog({
  episode,
  dataVersion,
  onCycleClick,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  onCycleClick: (cycle: number) => void;
  marketId?: string | null;
}) {
  const lastCycle = Math.max(0, episode.num_cycles - 1);
  const [fills, setFills] = useState<FillRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [agents, setAgents] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<PhaseFilter>("ALL");
  const [cycleMin, setCycleMin] = useState<number>(0);
  const [cycleMax, setCycleMax] = useState<number>(lastCycle);
  const [sortKey, setSortKey] = useState<SortKey>("cycle_index");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  useEffect(() => {
    api
      .fills(undefined, marketId)
      .then(setFills)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  // Auto-extend cycleMax when new cycles arrive, but only if the user hasn't
  // narrowed the range below the previous tail (i.e. don't clobber filters).
  useEffect(() => {
    setCycleMax((prev) => (prev < lastCycle ? lastCycle : prev));
  }, [lastCycle]);

  const allAgents = useMemo(() => episode.accounts.map((a) => a.id), [episode]);

  const filtered = useMemo(() => {
    if (!fills) return [];
    return fills
      .filter((f) => {
        if (phase !== "ALL" && f.phase !== phase) return false;
        if (f.cycle_index < cycleMin || f.cycle_index > cycleMax) return false;
        if (agents.size > 0) {
          if (!agents.has(f.buyer) && !agents.has(f.seller)) return false;
        }
        return true;
      })
      .sort((a, b) => {
        const s = sortDir === "asc" ? 1 : -1;
        return (a[sortKey] - b[sortKey]) * s;
      });
  }, [fills, phase, cycleMin, cycleMax, agents, sortKey, sortDir]);

  const toggleAgent = (id: string) => {
    setAgents((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const setSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  if (!fills)
    return <div className="text-sm text-zinc-500">Loading…</div>;

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Trade log"
        subtitle={`${filtered.length} of ${fills.length} fills`}
      />

      <Card>
        <div className="space-y-3">
          {/* Phase filter */}
          <div className="flex items-center gap-2 text-xs">
            <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
              Phase
            </span>
            {(["ALL", "MM", "HF"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPhase(p)}
                className={
                  "px-2 py-1 rounded border " +
                  (phase === p
                    ? "border-emerald-500 bg-emerald-900/30 text-emerald-300"
                    : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
                }
              >
                {p}
              </button>
            ))}
          </div>

          {/* Cycle range */}
          <div className="flex items-center gap-3 text-xs">
            <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
              Cycles
            </span>
            <input
              type="number"
              min={0}
              max={lastCycle}
              value={cycleMin}
              onChange={(e) => setCycleMin(Number(e.target.value))}
              className="w-16 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 tabular"
            />
            <span className="text-zinc-600">to</span>
            <input
              type="number"
              min={0}
              max={lastCycle}
              value={cycleMax}
              onChange={(e) => setCycleMax(Number(e.target.value))}
              className="w-16 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 tabular"
            />
          </div>

          {/* Agent multiselect */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
              Agents
            </span>
            {allAgents.map((a) => (
              <button
                key={a}
                onClick={() => toggleAgent(a)}
                className={
                  "px-2 py-1 rounded border font-mono " +
                  (agents.has(a)
                    ? "border-emerald-500 bg-emerald-900/30 text-emerald-300"
                    : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
                }
              >
                {a}
              </button>
            ))}
            {agents.size > 0 && (
              <button
                onClick={() => setAgents(new Set())}
                className="px-2 py-1 rounded border border-zinc-800 text-zinc-500 hover:text-zinc-200"
              >
                clear
              </button>
            )}
          </div>
        </div>
      </Card>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                <HeaderCell
                  label="Cycle"
                  active={sortKey === "cycle_index"}
                  dir={sortDir}
                  onClick={() => setSort("cycle_index")}
                />
                <th className="py-2 pr-4 font-medium">Phase</th>
                <th className="py-2 pr-4 font-medium">Buyer</th>
                <th className="py-2 pr-4 font-medium">Seller</th>
                <HeaderCell
                  label="Price"
                  active={sortKey === "price"}
                  dir={sortDir}
                  onClick={() => setSort("price")}
                  align="right"
                />
                <HeaderCell
                  label="Size"
                  active={sortKey === "size"}
                  dir={sortDir}
                  onClick={() => setSort("size")}
                  align="right"
                />
                <th className="py-2 pr-2 font-medium text-center">Self?</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => (
                <tr
                  key={f.id}
                  className="border-b border-zinc-900 last:border-0 hover:bg-zinc-900/40 cursor-pointer"
                  onClick={() => onCycleClick(f.cycle_index)}
                >
                  <td className="py-1.5 pr-4 text-zinc-300">{f.cycle_index}</td>
                  <td className="py-1.5 pr-4">
                    <Badge tone={f.phase === "MM" ? "orange" : "blue"}>
                      {f.phase}
                    </Badge>
                  </td>
                  <td className="py-1.5 pr-4 font-mono text-zinc-200">{f.buyer}</td>
                  <td className="py-1.5 pr-4 font-mono text-zinc-200">{f.seller}</td>
                  <td className="py-1.5 pr-4 text-right text-zinc-100">
                    {fmtPrice(f.price)}
                  </td>
                  <td className="py-1.5 pr-4 text-right text-zinc-100">
                    {fmtInt(f.size)}
                  </td>
                  <td className="py-1.5 pr-2 text-center">
                    {f.is_self_cross ? (
                      <Badge tone="red">self</Badge>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-zinc-500">
                    No fills match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function HeaderCell({
  label,
  active,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <th
      onClick={onClick}
      className={
        "py-2 pr-4 font-medium cursor-pointer select-none hover:text-zinc-200 " +
        (align === "right" ? "text-right" : "text-left")
      }
    >
      {label}
      {active && <span className="ml-1 text-emerald-400">{dir === "asc" ? "↑" : "↓"}</span>}
    </th>
  );
}
