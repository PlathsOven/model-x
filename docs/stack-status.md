# ModelX — Stack Component Registry

> **Purpose:** Single source of truth for which components are production-ready,
> which are mocked, and how they connect. Update this file whenever a component
> changes status.
>
> **Updated by `/doc-sync`** (see `.claude/commands/doc-sync.md` step 4).
> **Rate of change:** frequent — every status transition. Contrast with
> `docs/architecture.md`, which changes slowly (structural map only).

## Status Key

| Badge | Meaning |
|-------|---------|
| `PROD` | Production code, fully operational |
| `MOCK` | Running on hardcoded / simulated data |
| `STUB` | Empty function body / unimplemented |
| `OFF` | Not yet built or not running |

---

## Engine (`modelx/`)

| Component | File(s) | Status | Depends On | Notes |
|-----------|---------|--------|------------|-------|
| **Core dataclasses** | `modelx/models.py` | `PROD` | — | `Account`, `Contract`, `Quote`, `Order`, `Fill`, `CycleState`, `Phase` |
| **SQLite persistence** | `modelx/db.py` | `PROD` | stdlib `sqlite3` | All DDL + CRUD helpers in one file |
| **MM self-matching** | `modelx/matching.py` | `PROD` | — | Crossing quotes at midpoint, pro-rata on ties |
| **HF pro-rata matching** | `modelx/matching.py` | `PROD` | — | Book-walking, pro-rata at each level, position-limit partial fills |
| **Scoring** | `modelx/scoring.py` | `PROD` | — | `score_mm`, `score_hf`, `_carry_mark_forward`, all markout definitions |
| **Phase orchestration** | `modelx/phase.py` | `PROD` | Agents, matching, db | Builds context, fans out LLM calls, parses responses, persists |
| **Market runner** | `modelx/market_runner.py` | `PROD` | phase, db | Per-market advancement loop, owns `MarketState` across ticks |
| **Supervisor** | `modelx/supervisor.py` | `PROD` | market_runner | Global tick loop, fans markets out in parallel |
| **Config loading** | `modelx/config.py` | `PROD` | `pyyaml` (optional) | Parses `agents.yaml` and `contracts.yaml`, includes mini-parser fallback |
| **News + price ingest** | `modelx/news.py` | `PROD` | `feedparser`, `yfinance` | Google News RSS + 15-min OHLCV bars, graceful degrade on failure |

## Agents (`modelx/agents/`)

| Component | File(s) | Status | Depends On | Notes |
|-----------|---------|--------|------------|-------|
| **Agent base interface** | `modelx/agents/base.py` | `PROD` | — | Abstract `quote(ctx)` / `order(ctx)` async methods |
| **OpenRouter agent** | `modelx/agents/openrouter.py` | `PROD` | `httpx`, `OPENROUTER_API_KEY` | Supports rotating multi-key pool |
| **Prompts** | `modelx/agents/prompts.py` | `PROD` | — | MM and HF role prompts |
| **Human CLI agent** | *(not in repo in this build)* | `OFF` | — | README references `model: human` but `run_live.py` does not support it — human input would block the async loop |

## Dashboard (`dashboard/`)

| Component | File(s) | Status | Depends On | Notes |
|-----------|---------|--------|------------|-------|
| **Dashboard backend** | `dashboard/server.py` | `PROD` | `modelx/` helpers, FastAPI, uvicorn | Read-only, mtime-driven auto-reload, serves 11 endpoints |
| **Dashboard frontend** | `dashboard/frontend/src/*` | `PROD` | Vite, React, TS, Tailwind, Recharts, Lucide | 2s polling, `dataVersion` for per-view invalidation |
| **Overview view** | `dashboard/frontend/src/components/Overview.tsx` | `PROD` | `/api/episode` | Contract metadata + agent roster + final PnL |
| **Time Series view** | `dashboard/frontend/src/components/TimeSeries.tsx` | `PROD` | `/api/timeseries` | Marks, quote ranges, HF FV overlays, fill scatter, settlement line, info markers |
| **Trade Log view** | `dashboard/frontend/src/components/TradeLog.tsx` | `PROD` | `/api/fills` | Filter / sort, row click focuses Time Series |
| **Orderbook view** | `dashboard/frontend/src/components/Orderbook.tsx` | `PROD` | `/api/orderbook/{cycle}` | Per-cycle snapshot with depth bars |
| **Metrics view** | `dashboard/frontend/src/components/Metrics.tsx` | `PROD` | `/api/metrics` | All `modelx/scoring.py` metrics with pending badges for unsettled |
| **Positions view** | `dashboard/frontend/src/components/Positions.tsx` | `PROD` | `/api/positions` | Per-agent time series (position / PnL / cash switchable) |
| **Reasoning view** | `dashboard/frontend/src/components/Reasoning.tsx` | `PROD` | `/api/traces` | Per-agent prompts + responses, filter by phase/cycle |
| **Lifetime view** | `dashboard/frontend/src/components/Lifetime.tsx` | `PROD` | `agent_lifetime_stats` via `/api/*` | Aggregate per-agent stats across every settled market |
| **UI primitives** | `dashboard/frontend/src/components/ui.tsx` | `PROD` | Tailwind | Card, Badge, StatPill, SectionHeader, RoleBadge, EmptyState |
| **Per-agent color map** | `dashboard/frontend/src/lib/colors.ts` | `PROD` | — | Built once from account lists |

## Root scripts

| Component | File(s) | Status | Depends On | Notes |
|-----------|---------|--------|------------|-------|
| **Live runner** | `run_live.py` | `PROD` | supervisor, db, YAML configs | Drives every active market on one global wall-clock tick |
| **Settlement CLI** | `settle.py` | `PROD` | db, scoring | Stamps settlement value, computes metrics, writes lifetime stats |

## Tests (`tests/`)

| Component | File(s) | Status | Notes |
|-----------|---------|--------|-------|
| Agent tests | `tests/test_agents.py` | `PROD` | OpenRouter mocking + prompt shape |
| Cycle tests | `tests/test_cycle.py` | `PROD` | Phase advancement end-to-end |
| DB tests | `tests/test_db.py` | `PROD` | Schema + CRUD |
| Matching tests | `tests/test_matching.py` | `PROD` | MM crosses, HF pro-rata, position limits |
| News tests | `tests/test_news.py` | `PROD` | RSS parsing, source filter, yfinance wrapping |
| Scoring tests | `tests/test_scoring.py` | `PROD` | All metrics, carry-forward mark, markouts |

---

## Connection Map

```
┌────────────────────────────────────────────────────────────────────┐
│  OPERATOR TERMINAL                                                 │
│                                                                    │
│  run_live.py ──┬──► SUPERVISOR ─► per-market MarketRunner          │
│                │                   │                               │
│                │                   ▼                               │
│                │        ┌──────────────────────┐                   │
│                │        │  phase.py            │                   │
│                │        │   1. build ctx        │                  │
│                │        │   2. asyncio.gather   │────► OpenRouter  │
│                │        │      per agent        │      (httpx)     │
│                │        │   3. parse responses  │                  │
│                │        │   4. match_mm / hf    │                  │
│                │        │   5. persist fills    │                  │
│                │        └──────────────────────┘                   │
│                │                   │                               │
│                │                   ▼                               │
│                │             ┌─────────────┐                       │
│                │             │  modelx.db  │ ◄── (disposable)      │
│                │             └─────────────┘                       │
│                │                                                   │
│                └──── info ingest (news.py) ◄── Google News RSS     │
│                                               + yfinance           │
│                                                                    │
│  settle.py ──► reads db, computes scores, writes lifetime stats    │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ reads (mtime-driven)
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│  DASHBOARD (separate processes)                                    │
│                                                                    │
│  dashboard/server.py  (FastAPI, read-only, port 8000)              │
│          │                                                         │
│          │ /api/episode, /api/timeseries, /api/metrics, ...        │
│          ▼                                                         │
│  dashboard/frontend/  (Vite dev server, port 5173, polls every 2s) │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

## MOCK → PROD Upgrade Path

| # | What | Current | To Become | Blocked By |
|---|------|---------|-----------|------------|
| 1 | **Human agent in live mode** | `run_live.py` does not support `model: human` — would block the async loop | Async-safe human input (separate prompt thread?) | Design decision on UX for async CLI prompting |
| 2 | **Lint / format tooling** | None | `ruff` + `ruff format` across Python, `prettier` across TS | Owner willing to run it |
| 3 | **Schema migrations** | None — schema changes wipe the DB | Explicit migration script in `modelx/db.py` | Demand (first time schema change breaks existing ops) |
