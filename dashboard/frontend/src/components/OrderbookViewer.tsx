import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../api";
import type { Episode, Orderbook } from "../types";
import { fmtInt, fmtPrice } from "../lib/format";
import { Badge, Card, EmptyState, SectionHeader } from "./ui";

export function OrderbookViewer({
  episode,
  dataVersion,
  initialCycle = 0,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  initialCycle?: number;
  marketId?: string | null;
}) {
  const lastCycle = Math.max(0, episode.num_cycles - 1);
  const [cycle, setCycle] = useState<number>(
    Math.max(0, Math.min(initialCycle, lastCycle))
  );
  const [ob, setOb] = useState<Orderbook | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Re-fetch on every data-version change AND on every cycle change. The
  // useEffect dep on dataVersion makes the orderbook live-updating: each
  // poll that bumps the backend's loaded_at re-pulls the current cycle.
  useEffect(() => {
    if (episode.num_cycles === 0) {
      setOb(null);
      return;
    }
    setErr(null);
    api
      .orderbook(cycle, marketId)
      .then(setOb)
      .catch((e) => setErr(e?.message || String(e)));
  }, [cycle, dataVersion, episode.num_cycles, marketId]);

  // Auto-clamp cycle if the dataset shrank under us (e.g. user pointed at
  // a different db that has fewer cycles).
  useEffect(() => {
    setCycle((c) => Math.max(0, Math.min(c, lastCycle)));
  }, [lastCycle]);

  const clamp = (c: number) => Math.max(0, Math.min(c, lastCycle));

  // Empty state — contract loaded but no cycles yet (or contract has zero
  // recorded cycles). The slider would have max=-1 and break Recharts.
  if (episode.num_cycles === 0) {
    return (
      <div className="space-y-4">
        <SectionHeader
          title="Orderbook viewer"
          subtitle="Per-cycle snapshot — quotes, MM crosses, residual book, HF orders, HF fills"
        />
        <EmptyState>
          No cycles yet. Once <code className="font-mono">run_demo.py</code>{" "}
          writes its first cycle, the orderbook will appear here automatically.
        </EmptyState>
      </div>
    );
  }

  // Group residual book by side for the depth viz
  const { bids, asks, maxDepth } = useMemo(() => {
    if (!ob) return { bids: [], asks: [], maxDepth: 0 };
    const bids = ob.residual_book
      .filter((l) => l.side === "bid")
      .sort((a, b) => b.price - a.price);
    const asks = ob.residual_book
      .filter((l) => l.side === "ask")
      .sort((a, b) => a.price - b.price);
    const max = Math.max(
      1,
      ...bids.map((l) => l.size),
      ...asks.map((l) => l.size)
    );
    return { bids, asks, maxDepth: max };
  }, [ob]);

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Orderbook viewer"
        subtitle="Per-cycle snapshot — quotes, MM crosses, residual book, HF orders, HF fills"
      />

      <Card>
        <div className="flex items-center gap-4">
          <button
            onClick={() => setCycle((c) => clamp(c - 1))}
            disabled={cycle === 0}
            className="p-2 rounded border border-zinc-800 bg-zinc-900 hover:bg-zinc-800 disabled:opacity-30"
          >
            <ChevronLeft size={16} />
          </button>
          <div className="flex-1">
            <input
              type="range"
              min={0}
              max={lastCycle}
              value={cycle}
              onChange={(e) => setCycle(Number(e.target.value))}
              className="w-full accent-emerald-500"
            />
          </div>
          <div className="font-mono text-sm tabular text-zinc-200 min-w-[5rem] text-right">
            cycle {cycle} / {lastCycle}
          </div>
          <button
            onClick={() => setCycle((c) => clamp(c + 1))}
            disabled={cycle >= lastCycle}
            className="p-2 rounded border border-zinc-800 bg-zinc-900 hover:bg-zinc-800 disabled:opacity-30"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </Card>

      {err && (
        <div className="text-sm text-red-400 font-mono">{err}</div>
      )}

      {!ob && !err && (
        <div className="text-sm text-zinc-500">Loading cycle {cycle}…</div>
      )}

      {ob && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card title="Phase">
              <div className="text-lg font-semibold tabular">{ob.phase}</div>
            </Card>
            <Card title="MM mark">
              <div className="text-lg font-semibold tabular">
                {fmtPrice(ob.mm_mark, 4)}
              </div>
            </Card>
            <Card title="HF mark">
              <div className="text-lg font-semibold tabular">
                {fmtPrice(ob.hf_mark, 4)}
              </div>
            </Card>
          </div>

          <Card title="MM quotes submitted">
            <table className="w-full text-sm tabular">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 font-medium text-right">Bid</th>
                  <th className="py-2 pr-4 font-medium text-right">Bid size</th>
                  <th className="py-2 pr-4 font-medium text-right">Ask</th>
                  <th className="py-2 pr-2 font-medium text-right">Ask size</th>
                </tr>
              </thead>
              <tbody>
                {ob.quotes.map((q) => (
                  <tr key={q.account_id} className="border-b border-zinc-900 last:border-0">
                    <td className="py-1.5 pr-4 font-mono text-zinc-200">
                      {q.account_id}
                    </td>
                    <td className="py-1.5 pr-4 text-right text-emerald-400">
                      {fmtPrice(q.bid_price)}
                    </td>
                    <td className="py-1.5 pr-4 text-right text-zinc-300">
                      {fmtInt(q.bid_size)}
                    </td>
                    <td className="py-1.5 pr-4 text-right text-red-400">
                      {fmtPrice(q.ask_price)}
                    </td>
                    <td className="py-1.5 pr-2 text-right text-zinc-300">
                      {fmtInt(q.ask_size)}
                    </td>
                  </tr>
                ))}
                {ob.quotes.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-zinc-500">
                      No quotes submitted.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </Card>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card title="MM crosses (MM phase fills)">
              <FillsTable fills={ob.mm_fills} phase="MM" />
            </Card>
            <Card title="HF fills (HF phase fills)">
              <FillsTable fills={ob.hf_fills} phase="HF" />
            </Card>
          </div>

          <Card title="Residual book (HFs saw this)">
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="text-[10px] uppercase tracking-widest text-emerald-400 mb-2">
                  Bids
                </div>
                <table className="w-full text-sm tabular">
                  <tbody>
                    {bids.map((l, i) => (
                      <tr key={`bid-${i}`} className="relative">
                        <td className="py-1 pr-2 w-16 text-right text-emerald-300">
                          {fmtInt(l.size)}
                        </td>
                        <td className="py-1 pr-2 font-mono">
                          <div className="relative h-5 flex items-center justify-end">
                            <div
                              className="absolute right-0 top-0 bottom-0 bg-emerald-600/20 rounded-l"
                              style={{ width: `${(l.size / maxDepth) * 100}%` }}
                            />
                            <span className="relative z-10 pr-2 text-zinc-200">
                              {fmtPrice(l.price)}
                            </span>
                          </div>
                        </td>
                        <td className="py-1 pl-2 text-xs text-zinc-500 font-mono">
                          {l.account_id}
                        </td>
                      </tr>
                    ))}
                    {bids.length === 0 && (
                      <tr>
                        <td colSpan={3} className="py-4 text-center text-zinc-500 text-xs">
                          no bids
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div>
                <div className="text-[10px] uppercase tracking-widest text-red-400 mb-2">
                  Asks
                </div>
                <table className="w-full text-sm tabular">
                  <tbody>
                    {asks.map((l, i) => (
                      <tr key={`ask-${i}`}>
                        <td className="py-1 pr-2 font-mono">
                          <div className="relative h-5 flex items-center">
                            <div
                              className="absolute left-0 top-0 bottom-0 bg-red-600/20 rounded-r"
                              style={{ width: `${(l.size / maxDepth) * 100}%` }}
                            />
                            <span className="relative z-10 pl-2 text-zinc-200">
                              {fmtPrice(l.price)}
                            </span>
                          </div>
                        </td>
                        <td className="py-1 pl-2 w-16 text-left text-red-300">
                          {fmtInt(l.size)}
                        </td>
                        <td className="py-1 pl-2 text-xs text-zinc-500 font-mono">
                          {l.account_id}
                        </td>
                      </tr>
                    ))}
                    {asks.length === 0 && (
                      <tr>
                        <td colSpan={3} className="py-4 text-center text-zinc-500 text-xs">
                          no asks
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </Card>

          <Card title="HF orders submitted">
            <table className="w-full text-sm tabular">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 font-medium">Side</th>
                  <th className="py-2 pr-2 font-medium text-right">Size</th>
                </tr>
              </thead>
              <tbody>
                {ob.orders.map((o) => (
                  <tr key={o.account_id} className="border-b border-zinc-900 last:border-0">
                    <td className="py-1.5 pr-4 font-mono text-zinc-200">
                      {o.account_id}
                    </td>
                    <td className="py-1.5 pr-4">
                      <Badge tone={o.side === "buy" ? "emerald" : "red"}>
                        {o.side}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-2 text-right text-zinc-100">
                      {fmtInt(o.size)}
                    </td>
                  </tr>
                ))}
                {ob.orders.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-4 text-center text-zinc-500">
                      No HF orders submitted.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </Card>

          <Card title="Positions after cycle">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm tabular">
              {Object.entries(ob.positions_after)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([acct, pos]) => {
                  const before = ob.positions_before[acct] ?? 0;
                  const delta = pos - before;
                  return (
                    <div
                      key={acct}
                      className="flex items-baseline justify-between border border-zinc-800 rounded px-3 py-2 bg-zinc-950/40"
                    >
                      <span className="font-mono text-zinc-300 text-xs">{acct}</span>
                      <span className="text-zinc-100 font-medium">
                        {fmtInt(pos)}{" "}
                        {delta !== 0 && (
                          <span
                            className={
                              delta > 0 ? "text-emerald-400" : "text-red-400"
                            }
                          >
                            {delta > 0 ? `+${delta}` : delta}
                          </span>
                        )}
                      </span>
                    </div>
                  );
                })}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function FillsTable({
  fills,
  phase,
}: {
  fills: Orderbook["mm_fills"];
  phase: "MM" | "HF";
}) {
  return (
    <table className="w-full text-sm tabular">
      <thead>
        <tr className="text-left text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
          <th className="py-2 pr-4 font-medium">Buyer</th>
          <th className="py-2 pr-4 font-medium">Seller</th>
          <th className="py-2 pr-4 font-medium text-right">Price</th>
          <th className="py-2 pr-2 font-medium text-right">Size</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((f) => (
          <tr key={f.id} className="border-b border-zinc-900 last:border-0">
            <td className="py-1.5 pr-4 font-mono text-zinc-200">{f.buyer}</td>
            <td className="py-1.5 pr-4 font-mono text-zinc-200">{f.seller}</td>
            <td className="py-1.5 pr-4 text-right text-zinc-100">
              {fmtPrice(f.price)}
            </td>
            <td className="py-1.5 pr-2 text-right text-zinc-100">
              {fmtInt(f.size)}
            </td>
          </tr>
        ))}
        {fills.length === 0 && (
          <tr>
            <td colSpan={4} className="py-4 text-center text-zinc-500">
              No {phase} fills.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
