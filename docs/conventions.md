# Conventions

## File Organization

- **Target file size: <300 lines.** Documented convention; some existing files exceed this (e.g. `run_live.py`, `modelx/market_runner.py`). They can be trimmed in a follow-up `/refactor` session.
- **One concept per file.** A matching module, an agent class, a db helper, a dashboard view — each in its own file.
- **Tests colocated by subsystem** under `tests/test_<subsystem>.py`.
- **Default exports are avoided.** Named exports make refactors and finds safer.
- **Barrel files are avoided.** Import from the concrete file.

## Schema Source of Truth

- **Engine data shapes:** `modelx/models.py` (plain `@dataclass`). Every persisted object and every in-memory engine object flows through these. **Read `models.py` before modifying any feature that touches persistence or the dashboard boundary.**
- **SQLite DDL:** `modelx/db.py`. All `CREATE TABLE` statements live there. Row-to-dataclass hydration helpers live there too.
- **Dashboard wire shapes (Python → JSON):** `dashboard/server.py` response builders. These assemble plain dicts from `modelx/` dataclasses — they are the on-wire contract.
- **Dashboard TypeScript mirror:** `dashboard/frontend/src/types.ts`. Hand-maintained to match the JSON shapes produced by `dashboard/server.py`. When the Python dataclass changes, update the server response shape and `types.ts` in the same commit.

## Patterns Used

- **Plain dataclasses** at every internal boundary. No Pydantic, no ORMs. Validation is by trust — YAML configs are parsed with a safe loader, LLM responses are parsed defensively with fallback to "skip this agent this cycle".
- **stdlib `sqlite3`** for all DB access. Connection-per-call via `modelx.db.connect()`. No ORM.
- **`httpx`** for all outbound HTTP (OpenRouter, news fetches if they ever go HTTP). Never `requests`.
- **`asyncio.gather`** for per-agent fan-out inside a phase. One phase = one concurrent wave of LLM calls.
- **Per-market `MarketRunner`** owning the per-market state across ticks. The supervisor holds no per-market state; it just iterates runners.
- **Resume-from-DB on startup.** `run_live.py` reads the latest cycle state for every active market and reconstructs positions/cash/info from stored fills before taking the next tick.
- **Mtime-driven dashboard reload.** `dashboard/server.py` checks `os.path.getmtime(db)` on every request; when the mtime advances it rebuilds `AppState` under a lock. No SSE, no push — the frontend polls every 2s.
- **View-local UI state in the frontend.** Filters, sort order, focused cycle, agent multi-selects live in `useState` in the view component. Only data-fetch `useEffect` deps include `dataVersion` (so polling doesn't clobber user state).
- **Per-agent colors from a single source.** `dashboard/frontend/src/lib/colors.ts` builds the MM/HF color map once from the accounts list; components consume from there.
- **Conventional commits** — `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`. One logical change per commit.
- **Surgical staging** — `git add path1 path2`, never `git add .` or `git add -A`.

## Patterns Avoided

- **Pydantic.** Plain dataclasses only at engine boundaries. The codebase's validation philosophy is "parse defensively, skip the cycle on malformed input, keep running."
- **ORMs / query builders.** Direct stdlib `sqlite3` with parameterized queries. If a query is awkward, a helper goes in `modelx/db.py`.
- **`requests`.** `httpx` only.
- **Sync HTTP inside async.** If it's an IO call inside an async function, it must be `async` (or explicitly offloaded).
- **Shared dict singletons across modules.** Singletons are explicit dataclasses with clear init/rebuild (e.g. `dashboard/server.py`'s `AppState`).
- **Writes from the dashboard.** `dashboard/` is read-only with respect to `modelx.db`. New dashboard features must not mutate engine state.
- **Magic numbers.** Hoist to a named constant, or add a comment explaining the value.
- **`# noqa` / `@ts-ignore` without a written reason.** If you must silence a check, say why.

## Commit Message Format

```
<type>: <summary in under 72 chars>

<optional body explaining why, not what — the diff shows what>
```

Types: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`. One logical change per commit. Never bypass hooks with `--no-verify`. Never auto-push unless explicitly asked.

## Build & Test Commands

- **Run the exchange:** `python3 run_live.py` (respects `--contract`, `--agents`, `--db`, `--traces` flags)
- **Settle a market:** `python3 settle.py --market <id> --value <float>` (with optional `--force`)
- **Engine syntax check:** `python3 -m compileall modelx/ -q`
- **Dashboard backend syntax check:** `python3 -m compileall dashboard/ -q`
- **Engine tests:** `python3 -m pytest tests/ -x -q`
- **Dashboard frontend typecheck:** `cd dashboard/frontend && npx tsc --noEmit`
- **Dashboard frontend build:** `cd dashboard/frontend && npm run build`

No lint/format tooling is installed (no ruff, black, prettier, eslint). Adding one is a follow-up entry in `tasks/todo.md`.
