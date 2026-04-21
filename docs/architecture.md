# Architecture

## System Overview

ModelX is a points-based prediction exchange where Large Language Models (and optionally humans) trade linear derivative contracts that settle against real-world quantitative outcomes. It runs as a single Python process (`run_live.py`) that drives N contracts concurrently on a real-time wall-clock schedule, persists every quote/order/fill to a single SQLite file, and is inspected through a read-only web dashboard. Settlement is decoupled — a separate CLI (`settle.py`) stamps the true value when it is known and computes final scores. There is no server/client split, no real-time clock abstraction, and no ORM.

## Component Map

| Lane | Purpose |
|------|---------|
| `modelx/` | Core engine — dataclasses, SQLite persistence, matching, scoring, agent interface, cycle/phase orchestration |
| `modelx/agents/` | Agent implementations (OpenRouter LLM client, base interface, prompts) |
| `dashboard/` | Read-only FastAPI + React dashboard — post-mortem / live inspector over `modelx.db` + traces |
| `tests/` | pytest test suite colocated by subsystem |
| Root scripts | `run_live.py` (supervisor/runner), `settle.py` (settlement CLI) |
| Root configs | `agents.yaml` (participants), `contracts.yaml` (markets + news/price configuration) |

## Data Flow (Cycle Lifecycle)

Every cycle per active contract:

1. **News + price ingest** — headlines pulled from Google News RSS (`modelx/news.py`), 15-minute OHLCV bars from yfinance, formatted into a single info block for agent context.
2. **MM phase open** — each MM agent is asked for a sealed two-sided quote (`modelx/phase.py` + `modelx/agents/`). Responses fan out concurrently with `asyncio.gather`.
3. **MM phase close** — matching engine finds crossing quotes between MMs and fills them at midpoint, pro-rata if multiple MMs cross at the same level (`modelx/matching.py`). Mark-to-market = VWAP of remaining orderbook. Residual book is visible to HFs.
4. **HF phase open** — each HF agent sees the residual book + contract + position + info log, returns buy/sell/pass (also `asyncio.gather`).
5. **HF phase close** — HF orders processed simultaneously, pro-rata across available MM liquidity at each level, book-walking best-to-worst. Position limits enforced by partial fill (`modelx/matching.py`). Mark-to-market = VWAP of HF trade prices.
6. **Persist** — all phases, quotes, orders, fills, and cycle state written to SQLite (`modelx/db.py`).
7. **Settlement date check** — when a contract's `settlement_date` has passed, it transitions to `PENDING_SETTLEMENT` and stops accepting new cycles. `settle.py` is run manually with the real-world value; it writes `settlement_value`, computes per-agent metrics via `modelx/scoring.py`, and persists lifetime stats.

The supervisor (`modelx/supervisor.py`) coordinates all active markets on a single global wall-clock tick; each market owns a `MarketRunner` that advances one phase per tick. A `Ctrl-C` restart reloads each market's progress from the DB and resumes mid-cycle (positions, cash, info log, and the residual orderbook all rebuild from stored fills).

## Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Auto-loaded agent instructions for Claude Code — market structure, matching rules, scoring definitions |
| `README.md` | Operator's guide — prerequisites, setup, running, configs, dashboard launch, troubleshooting |
| `docs/architecture.md` | This file — system map |
| `docs/conventions.md` | Patterns used, patterns avoided, schema source of truth |
| `docs/decisions.md` | Append-only decision log |
| `docs/user-journey.md` | Personas + core flows (operator, agent author, human CLI participant, dashboard viewer) |
| `docs/product.md` | Theory — what ModelX measures, why LLM-vs-LLM prediction matters |
| `docs/stack-status.md` | Component PROD/MOCK/STUB/OFF registry |
| `docs/using-agents.md` | Operator playbook for coding this repo with AI agents |
| `tasks/todo.md` | Active work tracker |
| `tasks/lessons.md` | Self-improvement loop — lessons learned from corrections |
| `tasks/progress.md` | Mid-session handoff notes |
| `.claude/commands/*.md` | Claude Code slash commands |
| `.claude/settings.json` | Claude Code hooks (optional; when present, runs verification on Stop) |
| `agents.yaml` | Participant roster — `name`, `model` (OpenRouter id or `human`), `role` (`MM`/`HF`) |
| `contracts.yaml` | Market definitions — id, name, description, settlement_date, multiplier, position_limit, max_size, search_terms, price_ticker, news_sources, max_headlines_per_cycle; plus global `phase_duration_seconds` |
| `run_live.py` | Supervisor entrypoint — drives N contracts on one wall-clock tick, fans out agents, persists to DB |
| `settle.py` | Settlement CLI — stamps real-world value, computes final scores, writes lifetime stats |
| `modelx/models.py` | **Canonical dataclasses for all engine data shapes** (`Account`, `Contract`, `Quote`, `Order`, `Fill`, `CycleState`, etc.). Read before any feature crossing the dashboard API boundary. |
| `modelx/db.py` | SQLite persistence — DDL, connection helpers, CRUD for every dataclass, `positions_before_cycle` / `positions_for_contract` helpers |
| `modelx/matching.py` | MM self-matching (crossing quotes at midpoint, pro-rata) + HF pro-rata matching (book-walking, position-limit partial fills) |
| `modelx/phase.py` | Per-phase orchestration — build agent context, fan out LLM calls, parse responses, run matching |
| `modelx/market_runner.py` | Per-market advancement loop — owns one `MarketState` across ticks |
| `modelx/supervisor.py` | Global supervisor — one tick advances every active market in parallel |
| `modelx/config.py` | `AgentSpec`, `GlobalConfig`, `MarketConfig` dataclasses + YAML loading helpers (including the mini-parser fallback) |
| `modelx/news.py` | Google News RSS ingest + yfinance bar fetch + info-block formatting |
| `modelx/scoring.py` | `score_mm` / `score_hf` / `_carry_mark_forward` — final metrics (PnL, Sharpe, volume, uptime, consensus, markouts, self-cross) |
| `modelx/agents/base.py` | Abstract agent interface — `quote(ctx)` / `order(ctx)` async methods |
| `modelx/agents/openrouter.py` | `OpenRouterAgent` — httpx client, response parsing, trace capture |
| `modelx/agents/prompts.py` | System prompts for MM and HF roles |
| `dashboard/server.py` | FastAPI read-only server over `modelx.db` + `episode_traces.json`. Mtime-driven auto-reload, sentinel states for missing/empty data. |
| `dashboard/frontend/src/App.tsx` | Top-level dashboard shell — sidebar nav, status pill, 2-second polling |
| `dashboard/frontend/src/api.ts` | HTTP client for the backend endpoints |
| `dashboard/frontend/src/types.ts` | **Canonical TypeScript interfaces for dashboard payloads.** Must mirror what `dashboard/server.py` serializes from the `modelx/` dataclasses. |
| `dashboard/frontend/src/components/*.tsx` | Views (Overview, TimeSeries, TradeLog, Orderbook, Metrics, Positions, Reasoning, Lifetime) + UI primitives in `components/ui.tsx` |
| `dashboard/frontend/src/lib/colors.ts` | Per-agent color assignments — single source, derived from account lists |
| `modelx.db` | SQLite state file (gitignored). Disposable per-run state — re-running a market wipes its previous data. |
| `episode_traces.json` | Full request/response history for every LLM agent call — populated by `run_live.py --traces` |
| `requirements.txt` | Root-level Python deps (`httpx`, `feedparser`, `yfinance`, `python-dotenv`) |
| `dashboard/requirements.txt` | Dashboard backend deps (FastAPI, uvicorn) |
| `dashboard/frontend/package.json` | Dashboard frontend deps (React, Vite, TypeScript, Tailwind, Recharts, Lucide) |
| `tests/test_*.py` | pytest suites — `test_agents.py`, `test_cycle.py`, `test_db.py`, `test_matching.py`, `test_news.py`, `test_scoring.py` |

## Key Design Decisions

1. **Single-process, CLI-first.** No FastAPI server on the engine side, no WebSockets, no real-time clock abstraction. One `run_live.py` supervisor owns the wall-clock tick.
2. **SQLite over a bigger DB.** Single file, portable, zero operational overhead. `modelx.db` is treated as disposable per-run state, not a long-lived store.
3. **Plain dataclasses over Pydantic / ORMs.** Engine-internal shapes are dataclasses in `modelx/models.py`; dashboard wire shapes are plain dicts produced by `dashboard/server.py`; TypeScript mirror in `types.ts` is hand-maintained. No validation layer at the Python boundary — YAML is parsed with a safe loader and trusted.
4. **OpenRouter as the single LLM gateway.** All LLMs reached via OpenRouter so the same `httpx` client works for Anthropic / OpenAI / DeepSeek / etc. No per-provider SDKs.
5. **`asyncio.gather` for agent fan-out.** One phase sends one request per agent, awaits all concurrently. Parse errors on one agent skip only that agent's decision; the cycle proceeds.
6. **Read-only dashboard.** `dashboard/server.py` never writes to `modelx.db`. Mtime-driven auto-reload means the operator can leave it running while `run_live.py` writes new cycles.
7. **Manual settlement.** Settlement is decoupled from the run loop. A market enters `PENDING_SETTLEMENT` when its `settlement_date` passes; `settle.py` stamps the value and computes scores when the operator has it.
8. **Resume from DB.** Restart-safety is a product feature, not a nice-to-have. `run_live.py` rebuilds positions, cash, info log, and residual orderbook from stored fills so `Ctrl-C` at any point is safe.

See `docs/decisions.md` for the full reasoning behind each.

## Boundaries & Contracts

- **Engine dataclass boundary:** all engine-internal shapes defined in `modelx/models.py` (dataclasses). Persistence helpers in `modelx/db.py` convert between dataclass and SQLite row tuple.
- **Dashboard API boundary:** `dashboard/server.py` serializes engine state (loaded via `modelx/db.py` helpers) into JSON. `dashboard/frontend/src/types.ts` mirrors those JSON shapes. When one changes, check the other — the Python dataclass is upstream.
- **SQLite schema boundary:** all DDL lives in `modelx/db.py`. Treat schema changes carefully — existing `modelx.db` files will either break or need explicit migration. Document the migration choice in the commit message.
- **Directional import rule:** `dashboard/` imports `modelx/` (for `db`, `cycle`, `matching`, `scoring` helpers). `modelx/` must never import `dashboard/`.
