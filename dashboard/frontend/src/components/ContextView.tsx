import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api";
import type { AllTraces, Episode, PhaseRow } from "../types";
import { fmtInt, fmtPnl, pnlClass } from "../lib/format";
import { Card, RoleBadge, SectionHeader } from "./ui";

export function ContextView({
  episode,
  dataVersion,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  marketId?: string | null;
}) {
  const [phases, setPhases] = useState<PhaseRow[]>([]);
  const [traces, setTraces] = useState<AllTraces | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.phases(marketId), api.traces(marketId)])
      .then(([p, t]) => {
        setPhases(p);
        setTraces(t);
      })
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  const { accounts } = episode;
  const infoEvents = useMemo(() => phases.filter((p) => !!p.info), [phases]);

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Context"
        subtitle="Agent roster, information schedule, and prompts"
      />

      {/* Agent Roster */}
      <Card title="Agent roster">
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium">Role</th>
                <th className="py-2 pr-4 font-medium">Model</th>
                <th className="py-2 pr-4 font-medium text-right">
                  Final Position
                </th>
                <th className="py-2 pr-2 font-medium text-right">Final PnL</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-zinc-900 last:border-0 hover:bg-zinc-900/40"
                >
                  <td className="py-2 pr-4 font-mono text-zinc-200">
                    {a.name}
                  </td>
                  <td className="py-2 pr-4">
                    <RoleBadge role={a.role} />
                  </td>
                  <td className="py-2 pr-4 text-zinc-400 font-mono text-xs">
                    {a.model}
                  </td>
                  <td className="py-2 pr-4 text-right text-zinc-200">
                    {fmtInt(a.final_position)}
                  </td>
                  <td
                    className={`py-2 pr-2 text-right ${pnlClass(a.final_pnl)}`}
                  >
                    {fmtPnl(a.final_pnl)}
                  </td>
                </tr>
              ))}
              {accounts.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-zinc-500">
                    No accounts in the database.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Information Schedule */}
      {infoEvents.length > 0 && (
        <Card title="Information schedule">
          <ol className="space-y-3">
            {infoEvents.map((p) => (
              <li
                key={p.phase_id}
                className="flex gap-3 text-sm text-zinc-300"
              >
                <div className="shrink-0 w-28 text-[10px] uppercase tracking-widest text-zinc-500 pt-0.5">
                  {new Date(p.timestamp * 1000).toLocaleString()} (
                  {p.phase_type})
                </div>
                <div className="whitespace-pre-wrap">{p.info}</div>
              </li>
            ))}
          </ol>
        </Card>
      )}

      {/* Agent Prompts */}
      {traces?.loaded && traces.agents && (
        <Card title="Agent prompts">
          <div className="space-y-2">
            {Object.entries(traces.agents)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([agentId, data]) => (
                <AgentPromptSection
                  key={agentId}
                  agentId={agentId}
                  role={data.role}
                  traceEntries={data.traces}
                />
              ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function AgentPromptSection({
  agentId,
  role,
  traceEntries,
}: {
  agentId: string;
  role: "MM" | "HF";
  traceEntries: { phase_id: string; phase: string; timestamp: number; request: string }[];
}) {
  const [open, setOpen] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(() =>
    Math.max(0, traceEntries.length - 1)
  );

  if (traceEntries.length === 0) return null;

  const entry = traceEntries[selectedIdx];

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/40">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 w-full px-4 py-2.5 text-left text-sm hover:bg-zinc-900/40"
      >
        {open ? (
          <ChevronDown size={14} className="text-zinc-500" />
        ) : (
          <ChevronRight size={14} className="text-zinc-500" />
        )}
        <RoleBadge role={role} />
        <span className="font-mono text-zinc-200">{agentId}</span>
        <span className="text-xs text-zinc-500">
          {traceEntries.length} phase{traceEntries.length !== 1 ? "s" : ""}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-2">
          {traceEntries.length > 1 && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
                Phase
              </span>
              <select
                value={selectedIdx}
                onChange={(e) => setSelectedIdx(Number(e.target.value))}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs font-mono text-zinc-100 focus:outline-none focus:border-emerald-500"
              >
                {traceEntries.map((t, i) => (
                  <option key={i} value={i}>
                    {new Date(t.timestamp * 1000).toLocaleTimeString("en-US", {
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    })}{" "}
                    {t.phase}
                  </option>
                ))}
              </select>
            </div>
          )}
          <pre className="p-3 rounded border border-zinc-800 bg-zinc-950 text-[11px] font-mono text-zinc-400 whitespace-pre-wrap max-h-96 overflow-auto">
            {entry?.request ?? "(no prompt)"}
          </pre>
        </div>
      )}
    </div>
  );
}
