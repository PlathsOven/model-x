# ModelX - LLM Prediction Exchange

A read-only local web dashboard for inspecting ModelX episodes. Reads the SQLite
database written by `run_live.py` and the JSON reasoning traces written with
`--traces`, and renders seven cross-referenced views for post-mortem debugging.

The dashboard never writes to either data source. It is safe to start the
dashboard *before* the data exists — every view auto-populates as
`run_live.py` writes new cycles to disk (see [Live updates](#live-updates)).

## Views

- **Overview** — contract metadata, info schedule, agent roster with final
  positions and PnL.
- **Time Series** — carry-forward mark, per-agent MM quote ranges, per-agent HF
  fair-value estimates (from traces), fill scatter colored by phase, settlement
  reference line, info-schedule markers. Clicking a row in Trade Log focuses a
  cycle here.
- **Trade Log** — filterable / sortable table of every fill. Filters by phase,
  cycle range, and participating agent.
- **Orderbook** — per-cycle snapshot: MM quotes, MM crosses, residual book (with
  depth bars), HF orders, HF fills, positions before/after.
- **Performance** — per-agent time series (switchable between PnL, position, and
  cash) on top of the full metrics table from `modelx/scoring.py`. For unsettled
  contracts the settlement-independent fields (volume, uptime, consensus,
  self-cross counts) still render; PnL / Sharpe / markout columns show
  *pending*.
- **News** — headlines and market context shown to agents each cycle, rendered
  as a chronological feed with source badges and times.
- **Reasoning** — table of LLM traces (one row per agent-phase) with quote
  columns; click a row to expand Reasoning, Raw Response, and Prompt. Filter
  by agent and phase.

## Running

### 1. Start the backend (data optional)

```bash
cd dashboard
pip install -r requirements.txt
python3 server.py --db ../modelx.db --traces ../episode_traces.json --port 8000
```

The backend will start successfully even if `--db` does not exist or contains
no contracts yet — every endpoint returns a well-formed empty payload and the
frontend renders a "Waiting for ModelX data…" screen until data appears.

Flags:

- `--db PATH` (default `modelx.db`) — SQLite database written by `run_live.py`.
  May not exist yet.
- `--traces PATH` (default `episode_traces.json`) — optional traces JSON.
  Missing traces degrade gracefully: the Reasoning view shows an empty-state,
  and fair-value overlays disappear from the time-series chart.
- `--port INT` (default `8000`).

### 2. Generate (or regenerate) data

From the repo root, in any other terminal:

```bash
python3 run_live.py --db modelx.db --traces episode_traces.json
```

You can point the dashboard at any prior run's `modelx.db` /
`episode_traces.json` pair, or watch a fresh demo populate live.

### 3. Start the frontend

In a separate terminal:

```bash
cd dashboard/frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api/*` to the FastAPI backend on
`localhost:8000`, so both need to be running.

For a production bundle instead of the dev server:

```bash
npm run build
# dist/ can be served by any static host; keep the backend running for /api.
```

## Live updates

The dashboard is designed to be left running while `run_live.py` writes new
cycles to disk. There is no manual reload step in the common case.

**Backend auto-reload (mtime-driven).** Every API request checks the mtimes of
the configured `--db` and `--traces` files. If either has changed since the
last successful load, the in-memory `AppState` is rebuilt before the request
is served. The reload is `O(num_cycles)` re-running `match_mm_phase` — a
sub-millisecond operation for typical demo runs — and serialized by a
`threading.Lock` so concurrent requests collapse into a single rebuild. If
`--db` is missing or empty, the loader produces a sentinel `AppState` with
`status="db_missing"` or `"no_contracts"` instead of raising, so uvicorn
never crashes on startup.

**Frontend polling.** The frontend polls `GET /api/episode` every 2 seconds.
The response carries a `loaded_at` epoch second value that bumps whenever the
backend successfully reloads. This value is threaded into every view as a
`dataVersion` prop and used as the dependency of each view's data-fetch
`useEffect`, so any view currently mounted automatically re-fetches its own
endpoint when new data is available. View-local UI state (filters, sort
order, layer toggles, focus cycle, agent multi-selects) lives outside the
`dataVersion` deps and survives polls untouched.

**Sidebar status pill.**

- `● live` (green) — last poll succeeded and data is loaded.
- `○ waiting` (amber) — backend is up but no contract has been written yet.
- `× error` (red) — last poll failed (backend down, network error, etc.).

A relative timestamp ("updated 1s ago") underneath the db path makes it
obvious when polling has stalled.

The **Reload data** button in the sidebar is still wired to `POST /api/reload`
as a manual escape hatch, useful if a file is replaced atomically with the
same mtime or if you simply want to force a refetch immediately.

## Backend endpoints

All endpoints are read-only except `POST /api/reload`.

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/episode`              | Contract, accounts (with final pos/PnL), aggregate stats, sources, plus `loaded`, `status`, `loaded_at`, `db_mtime`, `traces_mtime` for live polling. |
| GET  | `/api/cycles`               | Per-cycle row: phase, marks, fill counts, info text. |
| GET  | `/api/fills`                | All fills. Optional `?agent=&phase=&cycle_min=&cycle_max=`. |
| GET  | `/api/quotes`               | All MM quotes. Optional `?cycle_index=`. |
| GET  | `/api/orders`               | All HF orders. Optional `?cycle_index=`. |
| GET  | `/api/orderbook/{cycle}`    | Per-cycle reconstruction (quotes, crosses, residual book, HF orders, fills, positions). |
| GET  | `/api/metrics`              | MM and HF scores from `modelx.scoring`, with unsettled-contract fallback. |
| GET  | `/api/positions`            | Per-agent per-cycle time series (position, cash, mtm PnL, realized PnL). |
| GET  | `/api/timeseries`           | Denormalized chart payload: marks, per-agent quotes + FV, fills, settlement, info markers. |
| GET  | `/api/traces`               | Passthrough of the traces JSON. |
| GET  | `/api/traces/{agent_id}`    | Traces for one agent. |
| POST | `/api/reload`               | Rebuild the in-memory state from disk. |

## Architecture

### Backend reuse

The backend is a single `server.py` file. It deliberately reuses existing
`modelx/` helpers rather than reimplementing anything:

- `modelx.db.connect` / `list_accounts` / `get_contract` / `list_cycle_states`
  / `positions_for_contract` / `positions_before_cycle` / `list_fills_by_contract`
  — SQLite reads.
- `modelx.cycle.load_cycle` — rebuilds a full `Cycle` object from DB rows for
  every stored cycle.
- `modelx.matching.match_mm_phase` — re-invoked per cycle with
  `positions_before_cycle(cycle)` to derive the residual book that HFs saw,
  independent of which phase the cycle stopped in. This avoids `load_cycle`'s
  conditional population of `residual_book`.
- `modelx.scoring.score_mm` / `score_hf` / `_carry_mark_forward` — final
  metrics and the carried-forward mark series.

For unsettled contracts, `score_mm`/`score_hf` raise; the backend wraps them
in `_compute_scores_safe`, which computes only the settlement-independent
fields (volume, volume_share, uptime, consensus, avg_abs_position,
self_cross_*, over_limit_cycles) and returns `None` for
PnL/Sharpe/markout/pnl_bps. The frontend renders "pending" badges in that case.

State lives in a module-level `AppState` dataclass that is rebuilt lazily on
every request whose configured `--db` / `--traces` mtimes have changed. The
mtime check itself is two `os.path.getmtime` calls and short-circuits when
nothing has changed, so steady-state requests have ~zero overhead. Reloads
are serialized by a `threading.Lock` and double-checked. The same load
function is used at startup (via FastAPI's `lifespan` context manager) and
by `POST /api/reload`. The loader never raises; missing or empty data
produces a sentinel `AppState` with a `status` field that the frontend turns
into a waiting screen and a sidebar status pill.

### Multi-market support

The dashboard supports multiple markets via the `markets` table. Each market
gets its own `MarketAppState` and every per-market endpoint accepts an optional
`?market_id=` query param. Without that param, the first market is used.

### Frontend stack

React + TypeScript + Vite + Tailwind + Recharts + Lucide. No react-router —
sidebar navigation is local state. View files live in
`frontend/src/components/`; primitive UI elements (Card, Badge, StatPill,
SectionHeader, RoleBadge, EmptyState) are in `components/ui.tsx`. Per-agent
colors are built once from the MM/HF account lists in `lib/colors.ts`.

## Verification

```bash
# Backend smoke test (assuming ../modelx.db and ../episode_traces.json exist)
curl localhost:8000/api/episode  | jq '{loaded, status, num_cycles, settled, accounts: (.accounts | length)}'
curl localhost:8000/api/cycles   | jq '.[0]'
curl localhost:8000/api/fills    | jq 'length'
curl localhost:8000/api/orderbook/0 | jq '{phase, residual: (.residual_book | length)}'
curl localhost:8000/api/metrics  | jq '.mm'
curl localhost:8000/api/timeseries | jq '{cycles: (.cycles | length), fills: (.fills | length)}'

# Empty-state smoke test (no db file)
rm -f /tmp/dashtest.db
python3 server.py --db /tmp/dashtest.db --port 8765 &
curl -s localhost:8765/api/episode | jq '{loaded, status, num_cycles}'
# {"loaded": false, "status": "db_missing", "num_cycles": 0}

# Frontend type + build check
cd frontend
npx tsc --noEmit
npm run build
```

Cross-check a few numbers against raw SQL to make sure nothing is drifting:

```bash
sqlite3 modelx.db "SELECT phase, COUNT(*) FROM fills GROUP BY phase"
# should match /api/episode.stats.mm_fills / hf_fills
```

## Notes and caveats

- Traces and DB are assumed to come from the same run. Mismatches are not
  cross-validated — if cycles or agents diverge the Reasoning view and
  fair-value overlays will simply look sparse.
- The time-series chart uses the carried-forward mark exactly as
  `modelx/scoring.py` does: prefer `hf_mark`, fall back to `mm_mark`, carry
  the previous value when both are null.
- Recharts bundles to ~600 KB minified (~170 KB gzipped). Acceptable for a
  local dev tool; if this ever matters, move to dynamic imports per route.
- The frontend's polling cadence is hardcoded to 2 seconds in
  `App.tsx` (`POLL_INTERVAL_MS`). Fast enough to feel live during a cycle,
  slow enough to be unobtrusive between cycles.
- SQLite's default rollback-journal mode is safe for concurrent reads while
  `run_live.py` writes — readers see a consistent snapshot as of the last
  commit. If a reload fires mid-commit it may briefly see one fewer cycle;
  the next 2s poll catches up.
