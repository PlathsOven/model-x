import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Newspaper } from "lucide-react";
import { api } from "../api";
import type { Episode, PhaseRow } from "../types";
import { Card, EmptyState, SectionHeader } from "./ui";

interface Headline {
  source: string;
  title: string;
  time: string;
}

interface ParsedInfo {
  price_header: string | null;
  price_block: string | null;
  headlines_since: string | null;
  headlines: Headline[];
  empty_headlines_note: string | null;
}

// Parse the info blob produced by modelx/news.py:build_info_payload.
// Shape: optional "=== PRICE DATA ... ===" block followed by a
// "=== HEADLINES (since ...) ===" block with either headline lines
// "[Source] Title (HH:MM)" or a single "No new headlines..." sentinel.
function parseInfo(info: string): ParsedInfo {
  const lines = info.split("\n");
  const priceLines: string[] = [];
  const headlines: Headline[] = [];
  let mode: "none" | "price" | "headlines" = "none";
  let price_header: string | null = null;
  let headlines_since: string | null = null;
  let empty_headlines_note: string | null = null;

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("=== PRICE DATA")) {
      mode = "price";
      price_header = line.replace(/=/g, "").trim();
      continue;
    }
    if (line.startsWith("=== HEADLINES")) {
      mode = "headlines";
      const m = line.match(/since ([^)]+)\)/);
      headlines_since = m ? m[1] : null;
      continue;
    }
    if (mode === "price") {
      if (line.trim()) priceLines.push(line);
    } else if (mode === "headlines") {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (trimmed.toLowerCase().startsWith("no new headlines")) {
        empty_headlines_note = trimmed;
        continue;
      }
      const m = trimmed.match(/^\[([^\]]*)\]\s+(.+?)\s+\(([^)]+)\)\s*$/);
      if (m) {
        headlines.push({
          source: (m[1] || "").trim() || "Unknown",
          title: m[2].trim(),
          time: m[3].trim(),
        });
      }
    }
  }

  return {
    price_header,
    price_block: priceLines.length ? priceLines.join("\n") : null,
    headlines_since,
    headlines,
    empty_headlines_note,
  };
}

export function NewsView({
  episode: _episode,
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

  const infoEvents = useMemo(
    () => phases.filter((p) => !!p.info).slice().reverse(),
    [phases]
  );

  if (err)
    return <div className="text-sm text-red-400 font-mono">{err}</div>;

  return (
    <div className="space-y-4">
      <SectionHeader
        title="News"
        subtitle="Headlines and market context shown to agents each cycle — most recent first"
      />

      {infoEvents.length === 0 ? (
        <EmptyState>
          <div className="flex flex-col items-center gap-2">
            <Newspaper className="text-zinc-500" />
            <div>No headlines yet.</div>
            <div className="text-xs text-zinc-600">
              News appears here after the first cycle completes.
            </div>
          </div>
        </EmptyState>
      ) : (
        <div className="space-y-3">
          {infoEvents.map((p) => (
            <CycleNews key={p.phase_id} phase={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function CycleNews({ phase }: { phase: PhaseRow }) {
  const parsed = useMemo(() => parseInfo(phase.info ?? ""), [phase.info]);
  const [showPrice, setShowPrice] = useState(false);

  const when = new Date(phase.timestamp * 1000).toLocaleString();
  const count = parsed.headlines.length;

  return (
    <Card>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-zinc-100">{when}</div>
          <div className="text-[10px] uppercase tracking-widest text-zinc-500 mt-0.5">
            {phase.phase_type} phase
            {count > 0 && ` · ${count} headline${count === 1 ? "" : "s"}`}
            {parsed.headlines_since && ` · since ${parsed.headlines_since}`}
          </div>
        </div>
      </div>

      {count > 0 ? (
        <ul className="space-y-2">
          {parsed.headlines.map((h, i) => (
            <li
              key={i}
              className="flex items-start gap-3 p-3 rounded-md border border-zinc-800 bg-zinc-950/40 hover:bg-zinc-900/60 transition-colors"
            >
              <span className="shrink-0 inline-flex items-center rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-300 max-w-[10rem] truncate">
                {h.source}
              </span>
              <div className="flex-1 min-w-0 text-sm text-zinc-100 leading-snug">
                {h.title}
              </div>
              <span className="shrink-0 text-[10px] font-mono text-zinc-500 pt-0.5">
                {h.time}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-sm text-zinc-500 italic">
          {parsed.empty_headlines_note ?? "No headlines parsed for this cycle."}
        </div>
      )}

      {parsed.price_block && (
        <div className="mt-3 pt-3 border-t border-zinc-800">
          <button
            onClick={() => setShowPrice((v) => !v)}
            className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300"
          >
            {showPrice ? (
              <ChevronDown size={12} />
            ) : (
              <ChevronRight size={12} />
            )}
            {parsed.price_header ?? "Price data"}
          </button>
          {showPrice && (
            <pre className="mt-2 p-3 rounded border border-zinc-800 bg-zinc-950 text-[10px] font-mono text-zinc-400 whitespace-pre-wrap max-h-48 overflow-auto">
              {parsed.price_block}
            </pre>
          )}
        </div>
      )}
    </Card>
  );
}
