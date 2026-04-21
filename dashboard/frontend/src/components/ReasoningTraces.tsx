import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api";
import type { AllTraces, Episode, TraceEntry } from "../types";
import { Badge, Card, EmptyState, RoleBadge, SectionHeader } from "./ui";

type PhaseFilter = "ALL" | "MM" | "HF";

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

  useEffect(() => {
    api
      .traces(marketId)
      .then(setTraces)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  // Flatten all traces with agent metadata attached
  type FlatTrace = TraceEntry & {
    agent_id: string;
    role: "MM" | "HF";
  };

  const flat: FlatTrace[] = useMemo(() => {
    if (!traces || !traces.agents) return [];
    const out: FlatTrace[] = [];
    for (const [agentId, data] of Object.entries(traces.agents)) {
      for (const tr of data.traces || []) {
        out.push({ ...tr, agent_id: agentId, role: data.role });
      }
    }
    out.sort((a, b) => {
      if (a.timestamp !== b.timestamp)
        return a.timestamp - b.timestamp;
      // MM before HF within the same timestamp
      if (a.phase !== b.phase) return a.phase === "MM" ? -1 : 1;
      return a.agent_id.localeCompare(b.agent_id);
    });
    return out;
  }, [traces]);

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

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Reasoning traces"
        subtitle={`${filtered.length} of ${flat.length} trace entries`}
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
                {a}
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

      <div className="space-y-3">
        {filtered.map((t, idx) => (
          <TraceCard key={`${t.agent_id}-${t.phase_id}-${t.phase}-${idx}`} trace={t} />
        ))}
        {filtered.length === 0 && (
          <EmptyState>No traces match the current filters.</EmptyState>
        )}
      </div>
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

function TraceCard({
  trace,
}: {
  trace: TraceEntry & { agent_id: string; role: "MM" | "HF" };
}) {
  const [showRaw, setShowRaw] = useState(false);
  const hasError = !!trace.error;
  const parsed = trace.parsed || {};
  const decision = trace.decision || {};
  const reasoning = (parsed as any).reasoning;

  return (
    <div
      className={
        "rounded-lg border bg-zinc-900/60 " +
        (hasError ? "border-red-800/70 bg-red-950/20" : "border-zinc-800")
      }
    >
      <div className="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <Badge tone="violet">{new Date(trace.timestamp * 1000).toLocaleString()}</Badge>
          <Badge tone={trace.phase === "MM" ? "orange" : "blue"}>{trace.phase}</Badge>
          <RoleBadge role={trace.role} />
          <span className="text-sm font-mono text-zinc-200 truncate">
            {trace.agent_id}
          </span>
          <span className="text-[10px] text-zinc-500 font-mono truncate">
            {trace.model}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-3">
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

        <div className="flex flex-wrap gap-4 text-sm tabular">
          <div className="flex-1 min-w-[12rem]">
            <div className="text-[10px] uppercase tracking-widest text-zinc-500 mb-1">
              Parsed
            </div>
            <DecisionBlock data={parsed} />
          </div>
          <div className="flex-1 min-w-[12rem]">
            <div className="text-[10px] uppercase tracking-widest text-zinc-500 mb-1">
              Decision
            </div>
            <DecisionBlock data={decision} highlighted />
          </div>
        </div>

        <div>
          <button
            onClick={() => setShowRaw((v) => !v)}
            className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200"
          >
            {showRaw ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Raw response
          </button>
          {showRaw && (
            <pre className="mt-1 p-3 rounded border border-zinc-800 bg-zinc-950 text-[11px] font-mono text-zinc-400 whitespace-pre-wrap max-h-72 overflow-auto">
              {trace.raw_response ?? "(null)"}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function DecisionBlock({
  data,
  highlighted = false,
}: {
  data: any;
  highlighted?: boolean;
}) {
  if (!data || Object.keys(data).length === 0) {
    return <div className="text-xs text-zinc-600">(none)</div>;
  }
  const entries = Object.entries(data).filter(([k]) => k !== "reasoning");
  return (
    <div
      className={
        "rounded border px-3 py-2 font-mono text-xs " +
        (highlighted
          ? "border-emerald-800 bg-emerald-950/20"
          : "border-zinc-800 bg-zinc-950/50")
      }
    >
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4 py-0.5">
          <span className="text-zinc-500">{k}</span>
          <span className={highlighted ? "text-emerald-200" : "text-zinc-200"}>
            {typeof v === "number" ? v : JSON.stringify(v)}
          </span>
        </div>
      ))}
      {entries.length === 0 && (
        <div className="text-zinc-600">(empty)</div>
      )}
    </div>
  );
}
