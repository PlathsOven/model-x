// Small reusable UI primitives — kept in one file to avoid component sprawl.

import { ReactNode } from "react";

export function Card({
  children,
  title,
  action,
  className = "",
}: {
  children: ReactNode;
  title?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={
        "rounded-lg border border-zinc-800 bg-zinc-900/60 shadow-sm " + className
      }
    >
      {(title || action) && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          {title && <div className="text-sm font-semibold text-zinc-100">{title}</div>}
          {action && <div>{action}</div>}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

export function Badge({
  children,
  tone = "zinc",
}: {
  children: ReactNode;
  tone?: "zinc" | "emerald" | "red" | "orange" | "blue" | "amber" | "violet";
}) {
  const tones: Record<string, string> = {
    zinc: "bg-zinc-800 text-zinc-300 border-zinc-700",
    emerald: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
    red: "bg-red-900/40 text-red-300 border-red-800",
    orange: "bg-orange-900/40 text-orange-300 border-orange-800",
    blue: "bg-blue-900/40 text-blue-300 border-blue-800",
    amber: "bg-amber-900/40 text-amber-300 border-amber-800",
    violet: "bg-violet-900/40 text-violet-300 border-violet-800",
  };
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide " +
        tones[tone]
      }
    >
      {children}
    </span>
  );
}

export function StatPill({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "positive" | "negative";
}) {
  const valueColor =
    tone === "positive"
      ? "text-emerald-400"
      : tone === "negative"
        ? "text-red-400"
        : "text-zinc-100";
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-3">
      <div className="text-[10px] uppercase tracking-widest text-zinc-500">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold tabular ${valueColor}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-zinc-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export function SectionHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-zinc-100">{title}</h1>
        {subtitle && (
          <p className="mt-1 text-sm text-zinc-500">{subtitle}</p>
        )}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/40 px-6 py-10 text-center text-sm text-zinc-500">
      {children}
    </div>
  );
}

export function RoleBadge({ role }: { role: "MM" | "HF" }) {
  return <Badge tone={role === "MM" ? "blue" : "orange"}>{role}</Badge>;
}
