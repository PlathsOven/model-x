// Cross-market lifetime view: aggregates per-agent stats from
// /api/metrics/lifetime, which is populated by settle.py whenever a market
// settles. Until at least one market has settled, the table is empty.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { LifetimeAgent, LifetimeMetrics } from "../types";
import { fmtInt, fmtPnl, fmtPrice, pnlClass } from "../lib/format";
import { Card, EmptyState, SectionHeader } from "./ui";

export function LifetimeMetricsView({
  dataVersion,
}: {
  dataVersion: number;
}) {
  const [data, setData] = useState<LifetimeMetrics | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .metricsLifetime()
      .then((d) => {
        setData(d);
        setErr(null);
      })
      .catch((e) => setErr(e?.message || String(e)));
  }, [dataVersion]);

  const agents = useMemo<LifetimeAgent[]>(() => {
    if (!data) return [];
    return Object.values(data.agents).sort(
      (a, b) => b.total_pnl - a.total_pnl
    );
  }, [data]);

  if (err) {
    return <div className="text-sm text-red-400 font-mono">{err}</div>;
  }
  if (!data) {
    return <div className="text-sm text-zinc-500">Loading…</div>;
  }

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Lifetime metrics"
        subtitle="Aggregated across every settled market in this database"
      />

      {agents.length === 0 ? (
        <EmptyState>
          No settled markets yet. Run{" "}
          <code className="font-mono text-zinc-300">
            python3 settle.py --market &lt;id&gt; --value &lt;v&gt;
          </code>{" "}
          to settle a market and populate lifetime stats.
        </EmptyState>
      ) : (
        <Card title="Aggregate per agent">
          <div className="overflow-x-auto">
            <table className="text-sm tabular w-full">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 text-left font-medium">Agent</th>
                  <th className="py-2 px-3 text-right font-medium">
                    Markets
                  </th>
                  <th className="py-2 px-3 text-right font-medium">
                    Total PnL
                  </th>
                  <th className="py-2 px-3 text-right font-medium">
                    Total Volume
                  </th>
                  <th className="py-2 px-3 text-right font-medium">
                    Avg Sharpe
                  </th>
                  <th className="py-2 px-3 text-right font-medium">Best</th>
                  <th className="py-2 px-3 text-right font-medium">Worst</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr
                    key={a.account_id}
                    className="border-b border-zinc-900 last:border-0 hover:bg-zinc-900/40"
                  >
                    <td className="py-2 pr-4 font-mono text-zinc-200">
                      {a.name}
                    </td>
                    <td className="py-2 px-3 text-right text-zinc-300">
                      {fmtInt(a.markets_traded)}
                    </td>
                    <td className={`py-2 px-3 text-right ${pnlClass(a.total_pnl)}`}>
                      {fmtPnl(a.total_pnl)}
                    </td>
                    <td className="py-2 px-3 text-right text-zinc-300">
                      {fmtInt(a.total_volume)}
                    </td>
                    <td className={`py-2 px-3 text-right ${pnlClass(a.avg_sharpe)}`}>
                      {fmtPrice(a.avg_sharpe, 4)}
                    </td>
                    <td className={`py-2 px-3 text-right ${pnlClass(a.best_market_pnl)}`}>
                      {fmtPnl(a.best_market_pnl)}
                    </td>
                    <td className={`py-2 px-3 text-right ${pnlClass(a.worst_market_pnl)}`}>
                      {fmtPnl(a.worst_market_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Per-market breakdown for each agent */}
      {agents.map((a) => (
        <Card key={a.account_id} title={`${a.name} — per market`}>
          <div className="overflow-x-auto">
            <table className="text-sm tabular w-full">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 text-left font-medium">Market</th>
                  <th className="py-2 px-3 text-left font-medium">Role</th>
                  <th className="py-2 px-3 text-right font-medium">PnL</th>
                  <th className="py-2 px-3 text-right font-medium">Sharpe</th>
                  <th className="py-2 px-3 text-right font-medium">Volume</th>
                </tr>
              </thead>
              <tbody>
                {a.per_market.map((m) => (
                  <tr
                    key={m.market_id}
                    className="border-b border-zinc-900 last:border-0"
                  >
                    <td className="py-1.5 pr-4 font-mono text-zinc-300 text-xs">
                      {m.market_id}
                    </td>
                    <td className="py-1.5 px-3 text-zinc-400 text-xs">
                      {m.role}
                    </td>
                    <td className={`py-1.5 px-3 text-right ${pnlClass(m.total_pnl ?? null)}`}>
                      {fmtPnl(m.total_pnl)}
                    </td>
                    <td className={`py-1.5 px-3 text-right ${pnlClass(m.sharpe ?? null)}`}>
                      {fmtPrice(m.sharpe ?? null, 4)}
                    </td>
                    <td className="py-1.5 px-3 text-right text-zinc-300">
                      {fmtInt(m.volume ?? 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  );
}
