import { Fragment, useEffect, useMemo, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api";
import type { AllTraces, Episode, TraceEntry } from "../types";
import { fmtPrice, stripMarketPrefix } from "../lib/format";
import { Card, EmptyState, RoleBadge, SectionHeader } from "./ui";

type PhaseFilter = "ALL" | "MM" | "HF";

type FlatTrace = TraceEntry & {
  agent_id: string;
  agent_name: string;
  role: "MM" | "HF";
};

interface Sides {
  bid_size: string;
  bid_price: string;
  ask_price: string;
  ask_size: string;
}

// Derive the four quote columns from a trace. For MM, both sides come from
// the parsed / decision bid & ask fields. For HF (market orders), only the
// size side lights up: buy → Bid Size, sell → Ask Size; prices always dash.
function sidesFromTrace(t: FlatTrace): Sides {
  const dash = "—";
  if (t.error) {
    return { bid_size: dash, bid_price: dash, ask_price: dash, ask_size: dash };
  }
  const d: any = t.decision ?? t.parsed ?? {};
  if (t.phase === "MM") {
    return {
      bid_size:
        d.bid_size != null && !Number.isNaN(Number(d.bid_size))
          ? String(Math.trunc(Number(d.bid_size)))
          : dash,
      bid_price:
        d.bid_price != null && !Number.isNaN(Number(d.bid_price))
          ? fmtPrice(Number(d.bid_price), 4)
          : dash,
      ask_price:
        d.ask_price != null && !Number.isNaN(Number(d.ask_price))
          ? fmtPrice(Number(d.ask_price), 4)
          : dash,
      ask_size:
        d.ask_size != null && !Number.isNaN(Number(d.ask_size))
          ? String(Math.trunc(Number(d.ask_size)))
          : dash,
    };
  }
  const size =
    d.size != null && !Number.isNaN(Number(d.size))
      ? String(Math.trunc(Number(d.size)))
      : dash;
  if (d.side === "buy") {
    return { bid_size: size, bid_price: dash, ask_price: dash, ask_size: dash };
  }
  if (d.side === "sell") {
    return { bid_size: dash, bid_price: dash, ask_price: dash, ask_size: size };
  }
  return { bid_size: dash, bid_price: dash, ask_price: dash, ask_size: dash };
}

export function ReasoningTraces({
  episode,
  dataVersion,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  marketId?: string | null;
}) {
  const [traces, setTraces] = useState<AllTraces | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState<string>("ALL");
  const [phaseFilter, setPhaseFilter] = useState<PhaseFilter>("ALL");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    api
      .traces(marketId)
      .then(setTraces)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  const nameById = useMemo(() => {
    const m: Record<string, string> = {};
    for (const a of episode.accounts) m[a.id] = a.name;
    return m;
  }, [episode.accounts]);

  const flat: FlatTrace[] = useMemo(() => {
    if (!traces || !traces.agents) return [];
    const out: FlatTrace[] = [];
    for (const [agentId, data] of Object.entries(traces.agents)) {
      for (const tr of data.traces || []) {
        out.push({
          ...tr,
          agent_id: agentId,
          agent_name: nameById[agentId] ?? stripMarketPrefix(agentId),
          role: data.role,
        });
      }
    }
    // Newest first, then MM before HF within the same timestamp, then by name.
    out.sort((a, b) => {
      if (a.timestamp !== b.timestamp) return b.timestamp - a.timestamp;
      if (a.phase !== b.phase) return a.phase === "MM" ? -1 : 1;
      return a.agent_id.localeCompare(b.agent_id);
    });
    return out;
  }, [traces, nameById]);

  const filtered = useMemo(() => {
    return flat.filter((t) => {
      if (agentFilter !== "ALL" && t.agent_id !== agentFilter) return false;
      if (phaseFilter !== "ALL" && t.phase !== phaseFilter) return false;
      return true;
    });
  }, [flat, agentFilter, phaseFilter]);

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  if (!traces)
    return <div className="text-sm text-zinc-500">Loading…</div>;

  if (!traces.loaded || !traces.agents) {
    return (
      <div className="space-y-4">
        <SectionHeader
          title="Reasoning traces"
          subtitle="Per-agent LLM request / response / decision history"
        />
        <EmptyState>
          <div className="flex flex-col items-center gap-2">
            <AlertCircle className="text-amber-400" />
            <div>No traces recorded yet.</div>
            <div className="text-xs text-zinc-600">
              Traces appear here after the first MM or HF phase completes.
            </div>
          </div>
        </EmptyState>
      </div>
    );
  }

  const agentIds = Object.keys(traces.agents).sort();
  const toggle = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Reasoning traces"
        subtitle={`${filtered.length} of ${flat.length} trace entries — click a row to expand`}
      />

      <Card>
        <div className="space-y-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
              Agent
            </span>
            <button
              onClick={() => setAgentFilter("ALL")}
              className={filterBtn(agentFilter === "ALL")}
            >
              ALL
            </button>
            {agentIds.map((a) => (
              <button
                key={a}
                onClick={() => setAgentFilter(a)}
                className={filterBtn(agentFilter === a) + " font-mono"}
              >
                {nameById[a] ?? stripMarketPrefix(a)}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
              Phase
            </span>
            {(["ALL", "MM", "HF"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPhaseFilter(p)}
                className={filterBtn(phaseFilter === p)}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {filtered.length === 0 ? (
        <EmptyState>No traces match the current filters.</EmptyState>
      ) : (
        <Card className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm tabular">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pl-3 pr-1 w-6" />
                  <th className="py-2 px-2 font-medium whitespace-nowrap">
                    Timestamp
                  </th>
                  <th className="py-2 px-2 font-medium whitespace-nowrap">
                    Name
                  </th>
                  <th className="py-2 px-2 font-medium whitespace-nowrap">
                    Model
                  </th>
                  <th className="py-2 px-2 font-medium whitespace-nowrap">
                    Role
                  </th>
                  <th className="py-2 px-2 font-medium text-right whitespace-nowrap">
                    Bid Size
                  </th>
                  <th className="py-2 px-2 font-medium text-right whitespace-nowrap">
                    Bid Price
                  </th>
                  <th className="py-2 px-2 font-medium text-right whitespace-nowrap">
                    Ask Price
                  </th>
                  <th className="py-2 px-2 pr-3 font-medium text-right whitespace-nowrap">
                    Ask Size
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t, idx) => {
                  const key = `${t.agent_id}-${t.phase_id}-${t.phase}-${idx}`;
                  const isOpen = expanded.has(key);
                  const sides = sidesFromTrace(t);
                  const hasError = !!t.error;
                  return (
                    <Fragment key={key}>
                      <tr
                        onClick={() => toggle(key)}
                        className={
                          "cursor-pointer border-b border-zinc-900 hover:bg-zinc-800/40 " +
                          (hasError ? "bg-red-950/20" : "")
                        }
                      >
                        <td className="py-1.5 pl-3 pr-1 text-zinc-500">
                          {isOpen ? (
                            <ChevronDown size={14} />
                          ) : (
                            <ChevronRight size={14} />
                          )}
                        </td>
                        <td className="py-1.5 px-2 text-zinc-300 whitespace-nowrap text-xs">
                          {new Date(t.timestamp * 1000).toLocaleString()}
                        </td>
                        <td className="py-1.5 px-2 font-mono text-zinc-100 whitespace-nowrap">
                          {t.agent_name}
                        </td>
                        <td className="py-1.5 px-2 font-mono text-zinc-500 text-xs whitespace-nowrap">
                          {t.model}
                        </td>
                        <td className="py-1.5 px-2">
                          <RoleBadge role={t.role} />
                        </td>
                        <td className="py-1.5 px-2 text-right text-zinc-200 tabular-nums">
                          {sides.bid_size}
                        </td>
                        <td className="py-1.5 px-2 text-right text-zinc-200 tabular-nums">
                          {sides.bid_price}
                        </td>
                        <td className="py-1.5 px-2 text-right text-zinc-200 tabular-nums">
                          {sides.ask_price}
                        </td>
                        <td className="py-1.5 px-2 pr-3 text-right text-zinc-200 tabular-nums">
                          {sides.ask_size}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-zinc-800 bg-zinc-950/60">
                          <td />
                          <td colSpan={8} className="py-3 pr-4">
                            <TraceDetail trace={t} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function filterBtn(active: boolean): string {
  return (
    "px-2 py-1 rounded border " +
    (active
      ? "border-emerald-500 bg-emerald-900/30 text-emerald-300"
      : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
  );
}

function TraceDetail({ trace }: { trace: FlatTrace }) {
  const hasError = !!trace.error;
  const reasoning = (trace.parsed as any)?.reasoning as string | undefined;
  return (
    <div className="space-y-3">
      {hasError && (
        <div className="rounded border border-red-800 bg-red-950/40 p-3 text-sm">
          <div className="flex items-center gap-2 text-red-300 font-semibold mb-1">
            <AlertCircle size={14} /> Error
          </div>
          <pre className="text-xs font-mono text-red-200 whitespace-pre-wrap">
            {trace.error}
          </pre>
        </div>
      )}

      {reasoning && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-zinc-500 mb-1">
            Reasoning
          </div>
          <div className="text-sm text-zinc-200 whitespace-pre-wrap leading-relaxed">
            {reasoning}
          </div>
        </div>
      )}

      <Collapsible label="Raw response">
        <pre className="p-3 rounded border border-zinc-800 bg-zinc-950 text-[11px] font-mono text-zinc-400 whitespace-pre-wrap max-h-72 overflow-auto">
          {trace.raw_response ?? "(null)"}
        </pre>
      </Collapsible>

      <Collapsible label="Prompt">
        <pre className="p-3 rounded border border-zinc-800 bg-zinc-950 text-[11px] font-mono text-zinc-400 whitespace-pre-wrap max-h-96 overflow-auto">
          {trace.request || "(no prompt)"}
        </pre>
      </Collapsible>
    </div>
  );
}

function Collapsible({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {label}
      </button>
      {open && <div className="mt-1">{children}</div>}
    </div>
  );
}
