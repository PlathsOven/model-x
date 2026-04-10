import { useState } from "react";
import type { Episode } from "../types";
import { TradeLog } from "./TradeLog";
import { OrderbookViewer } from "./OrderbookViewer";
import { SectionHeader } from "./ui";

type SubView = "fills" | "orderbook";

export function TradesView({
  episode,
  dataVersion,
  onPhaseClick,
  focusPhaseId,
  marketId,
}: {
  episode: Episode;
  dataVersion: number;
  onPhaseClick: (phaseId: string) => void;
  focusPhaseId: string | null;
  marketId?: string | null;
}) {
  const [sub, setSub] = useState<SubView>("fills");

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Trades"
        action={
          <div className="flex items-center gap-2 text-xs">
            {(["fills", "orderbook"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSub(s)}
                className={
                  "px-2 py-1 rounded border capitalize " +
                  (sub === s
                    ? "border-emerald-500 bg-emerald-900/30 text-emerald-300"
                    : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
                }
              >
                {s}
              </button>
            ))}
          </div>
        }
      />

      {sub === "fills" && (
        <TradeLog
          episode={episode}
          dataVersion={dataVersion}
          onPhaseClick={onPhaseClick}
          marketId={marketId}
        />
      )}
      {sub === "orderbook" && (
        <OrderbookViewer
          episode={episode}
          dataVersion={dataVersion}
          initialPhaseId={focusPhaseId ?? undefined}
          marketId={marketId}
        />
      )}
    </div>
  );
}
