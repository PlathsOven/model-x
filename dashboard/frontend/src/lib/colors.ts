// Color palette — dark theme. MMs get a blue/cyan/violet family; HFs get
// orange/amber/rose; phase colors are orange (MM) and blue (HF); PnL uses
// emerald/red. Agents are assigned from their role's palette in roster order.

export const PHASE_COLORS = {
  MM: "#f97316", // orange-500
  HF: "#3b82f6", // blue-500
};

export const PNL_COLORS = {
  positive: "#34d399", // emerald-400
  negative: "#f87171", // red-400
  neutral: "#a1a1aa", // zinc-400
};

const MM_PALETTE = [
  "#60a5fa", // blue-400
  "#22d3ee", // cyan-400
  "#a78bfa", // violet-400
  "#818cf8", // indigo-400
  "#34d399", // emerald-400
  "#4ade80", // green-400
];

const HF_PALETTE = [
  "#fb923c", // orange-400
  "#fbbf24", // amber-400
  "#fb7185", // rose-400
  "#f472b6", // pink-400
  "#facc15", // yellow-400
  "#e879f9", // fuchsia-400
];

/**
 * Deterministic color for an agent. Given a list of MM and HF account ids,
 * returns a map {account_id: hex} that the whole app reuses.
 */
export function buildAgentColors(
  mmAccounts: string[],
  hfAccounts: string[]
): Record<string, string> {
  const out: Record<string, string> = {};
  mmAccounts.forEach((a, i) => {
    out[a] = MM_PALETTE[i % MM_PALETTE.length];
  });
  hfAccounts.forEach((a, i) => {
    out[a] = HF_PALETTE[i % HF_PALETTE.length];
  });
  return out;
}

export const GRID_COLOR = "#3f3f46"; // zinc-700
export const AXIS_COLOR = "#71717a"; // zinc-500
export const TEXT_COLOR = "#e4e4e7"; // zinc-200
