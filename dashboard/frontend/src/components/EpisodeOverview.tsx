import { useEffect, useState } from "react";
import { api } from "../api";
import type { CycleRow, Episode } from "../types";
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
  const [cycles, setCycles] = useState<CycleRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .cycles(marketId)
      .then(setCycles)
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion, marketId]);

  const { contract, stats, accounts, settled, num_cycles } = episode;

  // App.tsx guards episode.loaded, so contract should always be non-null
  // here. Defensive bail-out for type safety.
  if (!contract) {
    return null;
  }

  const mmCount = accounts.filter((a) => a.role === "MM").length;
  const hfCount = accounts.filter((a) => a.role === "HF").length;

  const infoEvents = cycles.filter((c) => !!c.info);

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
        <StatPill label="Cycles" value={fmtInt(num_cycles)} />
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
          sub={infoEvents.length > 0 ? `cycles ${infoEvents.map((c) => c.cycle_index).join(", ")}` : "none"}
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
            {infoEvents.map((c) => (
              <li
                key={c.cycle_index}
                className="flex gap-3 text-sm text-zinc-300"
              >
                <div className="shrink-0 w-16 text-[10px] uppercase tracking-widest text-zinc-500 pt-0.5">
                  Cycle {c.cycle_index}
                </div>
                <div className="whitespace-pre-wrap">{c.info}</div>
              </li>
            ))}
          </ol>
        </Card>
      )}
    </div>
  );
}
