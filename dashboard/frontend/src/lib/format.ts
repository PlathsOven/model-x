// Small formatting helpers used across tables and charts.

export function fmtPrice(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function fmtPnl(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}`;
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Math.trunc(v).toString();
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtBps(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)} bps`;
}

export function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "text-zinc-400";
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-red-400";
  return "text-zinc-300";
}

export function stripMarketPrefix(accountId: string): string {
  const idx = accountId.indexOf(":");
  return idx >= 0 ? accountId.slice(idx + 1) : accountId;
}

export function formatSettlementDate(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const iso = raw.replace(" ", "T");
  const d = new Date(iso);
  if (isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
