---
description: Dashboard UI design workflow grounded in user journey, operator needs, and attention hierarchy
---

## /ui-design — Dashboard UI Design

Run before implementing any dashboard UI change that affects layout, interaction patterns, or information hierarchy. The dashboard (`dashboard/frontend/`) is a read-only debug tool for inspecting ModelX episodes — every design decision must make a post-mortem faster, not slower.

---

### 1. Context Load

Read these files fully — do not skim:
- `docs/user-journey.md` — personas, core flows, invariants
- `dashboard/README.md` — view catalogue, backend endpoints, live-update model
- `docs/conventions.md` — established UI patterns (React + TS + Vite + Tailwind + Recharts, local state, no react-router, per-agent colors from `lib/colors.ts`)

If the design task touches a specific view, also read that component file and its data-fetch path (which endpoint, which `dataVersion` dep).

### 2. Persona Grounding

Before proposing anything, restate these dimensions for the primary persona (the operator running `run_live.py` and watching the debug dashboard):

1. **Minimal essential needs** — What does the operator *must* see or do to answer the question they opened the dashboard to answer (usually: "why did this fill happen?" or "is this agent broken?"). Strip away everything that is not load-bearing. Reference the specific view and flow from `dashboard/README.md` and `docs/user-journey.md`.
2. **Desired emotional state** — The operator should feel: confident the dashboard mirrors the DB/traces exactly, in control of what view they're looking at, able to drill from a symptom to a cycle/agent/fill in under five clicks. They should never feel: uncertain whether data is stale, confused about which market is selected, overwhelmed by charts that update faster than they can read.
3. **Intuitive actions** — Click a row in Trade Log to focus that cycle in Time Series. Click a cell to drill into Reasoning for that agent/cycle. Use the market dropdown at the top to switch contexts. Design for these instincts.
4. **Attention hierarchy** — Time Series chart dominates when comparing cycles. Trade Log dominates when inspecting a single fill. Reasoning dominates when asking "why did this agent do this?" Identify where in this hierarchy the current task lives.
5. **Liveness signals** — The sidebar status pill (`● live` / `○ waiting` / `× error`) and the "updated Ns ago" timestamp are the operator's trust anchor. Any change must preserve them.

### 3. Scope the Design Task

Identify:
- **Which view(s)** from `dashboard/README.md` are affected (Overview / Time Series / Trade Log / Orderbook / Metrics / Positions / Reasoning / Lifetime)
- **Which flow(s)** from `docs/user-journey.md` are affected
- **What the operator sees today** vs. **what they should see after this change**
- **What is NOT changing** — explicitly call out adjacent views that must remain untouched
- **Whether this requires a new backend endpoint** in `dashboard/server.py` or a changed response shape

### 4. Design Principles Checklist

Walk the proposed design against each principle. Cite specific decisions for each:

**Information hierarchy**
- [ ] The most important data has the most visual weight (size, contrast, position)
- [ ] Secondary information is accessible but does not compete with primary
- [ ] Nothing is shown that the operator does not need for the current flow
- [ ] Empty/loading states communicate clearly — no blank voids or spinners without context (reuse `EmptyState` from `components/ui.tsx`)

**Cognitive load**
- [ ] The operator can understand the screen state in under 3 seconds
- [ ] No more than one decision is required at a time
- [ ] Related information is spatially grouped; unrelated information is visually separated
- [ ] Labels use exchange-domain language the operator already knows (mark, residual book, cross, fill, markout, consensus)

**Trust & liveness**
- [ ] The operator can always tell whether data is live or stale
- [ ] Status pill + "updated Ns ago" timestamp remain visible from this view
- [ ] Error states are human-readable cards, never raw stack traces
- [ ] If the db is missing or no contracts exist, the waiting screen still fires correctly

**Interaction patterns**
- [ ] Click targets are obvious (cursor change, hover state, or spatial convention)
- [ ] The most common drill-down (row → cycle → agent) requires the fewest clicks
- [ ] Filters and view-local state survive the 2-second poll (live in `useState`, not `useEffect` deps on `dataVersion`)
- [ ] The market dropdown at the top stays visible and selectable

**Visual consistency**
- [ ] Colors, spacing, typography match existing views
- [ ] Per-agent colors come from `lib/colors.ts` — never hardcoded in the component
- [ ] New components reuse existing primitives from `components/ui.tsx` (Card, Badge, StatPill, SectionHeader, RoleBadge, EmptyState)
- [ ] Numeric values use `tabular-nums` and the existing sign-coloring convention

### 5. Propose the Design

Output the design as:
- **Layout** — where it sits in the sidebar nav, sizing, relationship to adjacent views
- **Components** — what React components are needed (new or modified), with one-line purpose each
- **Data flow** — what endpoint feeds this view (new or existing), what `dataVersion` dep it hooks into
- **Interactions** — what the operator can click/hover/type, and what happens when they do
- **States** — empty, loading, connected, error, waiting-for-data
- **Rationale** — for every non-obvious choice, a one-sentence link back to a persona need, flow step, or invariant

**Do NOT write code yet. Pause and wait for explicit approval.**

### 6. Self-Review

Before reporting the design as ready:
- [ ] Every element traces back to a persona need or flow step (no speculative features)
- [ ] No invariant from `docs/user-journey.md` is violated
- [ ] The design works in the empty state (no db file, no contracts yet) as well as the populated state
- [ ] Backend auto-reload via mtime still drives the view (no manual refresh required in the common case)
- [ ] The design is achievable with the current stack (React, TypeScript, Vite, Tailwind, Recharts, Lucide)
- [ ] No write path to `modelx.db` is introduced (dashboard is read-only)

### 7. Handoff

Once the design is approved:
- If implementation is requested, delegate to `/implement` with the approved design as the plan input
- If a spec is needed first, delegate to `/spec` to formalize acceptance criteria
- Update `docs/user-journey.md` if the design changes or adds a flow step
- Update `dashboard/README.md` if the view catalogue, endpoints, or live-update model changed
