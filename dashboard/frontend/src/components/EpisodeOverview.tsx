import { useEffect, useState } from "react";
import { api } from "../api";
import type { PhaseRow, Episode } from "../types";
import { fmtInt, fmtPnl, pnlClass } from "../lib/format";
import { Card, RoleBadge, SectionHeader, StatPill } from "./ui";

export function EpisodeOverview({
  episode,
  dataVersion,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  marketId?: string | null;
}) {
  const [phases, setPhases] = useState<PhaseRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .phases(marketId)
      .then(setPhases)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  const { contract, stats, accounts, settled, phase_count } = episode;

  // App.tsx guards episode.loaded, so contract should always be non-null
  // here. Defensive bail-out for type safety.
  if (!contract) {
    return null;
  }

  const mmCount = accounts.filter((a) => a.role === "MM").length;
  const hfCount = accounts.filter((a) => a.role === "HF").length;

  const infoEvents = phases.filter((p) => !!p.info);

  return (
    <div className="space-y-6">
      <SectionHeader
        title={contract.name}
        subtitle={contract.description}
        action={
          settled ? (
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-widest text-zinc-500">
                Settlement
              </div>
              <div className="text-lg font-semibold text-emerald-400 tabular">
                {contract.settlement_value?.toFixed(4)}
              </div>
              {contract.settlement_date && (
                <div className="text-xs text-zinc-500">
                  {contract.settlement_date}
                </div>
              )}
            </div>
          ) : (
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-widest text-zinc-500">
                Status
              </div>
              <div className="text-lg font-semibold text-amber-400">
                Unsettled
              </div>
            </div>
          )
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatPill label="Phases" value={fmtInt(phase_count ?? 0)} />
        <StatPill
          label="Agents"
          value={fmtInt(accounts.length)}
          sub={`${mmCount} MM · ${hfCount} HF`}
        />
        <StatPill
          label="Multiplier"
          value={contract.multiplier.toFixed(2)}
          sub={`position limit ±${contract.position_limit}`}
        />
        <StatPill
          label="Total Fills"
          value={fmtInt(stats.total_fills)}
          sub={`${stats.total_volume} contracts`}
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatPill
          label="MM Crosses"
          value={fmtInt(stats.mm_fills)}
          sub="within MM phase"
        />
        <StatPill
          label="HF Fills"
          value={fmtInt(stats.hf_fills)}
          sub="HF vs MM book"
        />
        <StatPill
          label="Info Events"
          value={fmtInt(infoEvents.length)}
          sub={infoEvents.length > 0 ? `phases ${infoEvents.map((p) => p.phase_id.slice(0, 8)).join(", ")}` : "none"}
        />
        <StatPill
          label="Traces"
          value={episode.traces_loaded ? "loaded" : "missing"}
          tone={episode.traces_loaded ? "default" : "negative"}
        />
      </div>

      <Card title="Agent roster">
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium">Role</th>
                <th className="py-2 pr-4 font-medium">Model</th>
                <th className="py-2 pr-4 font-medium text-right">Final Position</th>
                <th className="py-2 pr-2 font-medium text-right">Final PnL</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-zinc-900 last:border-0 hover:bg-zinc-900/40"
                >
                  <td className="py-2 pr-4 font-mono text-zinc-200">{a.name}</td>
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

      {err && (
        <div className="text-sm text-red-400 font-mono">{err}</div>
      )}

      {infoEvents.length > 0 && (
        <Card title="Information schedule">
          <ol className="space-y-3">
            {infoEvents.map((p) => (
              <li
                key={p.phase_id}
                className="flex gap-3 text-sm text-zinc-300"
              >
                <div className="shrink-0 w-28 text-[10px] uppercase tracking-widest text-zinc-500 pt-0.5">
                  {new Date(p.timestamp * 1000).toLocaleString()} ({p.phase_type})
                </div>
                <div className="whitespace-pre-wrap">{p.info}</div>
              </li>
            ))}
          </ol>
        </Card>
      )}
    </div>
  );
}
