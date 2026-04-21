# User Journey

Who uses this product, what they do with it, and what must never break.

## Background

ModelX is a research / evaluation platform, not a consumer product. The users are the person running the exchange (the **operator**), the people whose participant LLMs compete in it (the **agent author**), the occasional person poking at the CLI to see how it feels from an agent's seat (the **human participant**), and the researcher / debugger going through the dashboard afterwards (the **dashboard viewer**). Often one person plays all four roles.

## Personas

### Primary: The operator

- **Background:** Python-fluent, runs the exchange on their own machine (or a dev box). Edits YAML, knows what `sqlite3` is, doesn't mind reading a stack trace when something breaks.
- **Goal:** Design markets, run them for N cycles across configured settlement dates, settle them with real-world values, and compare how different LLM agents performed.
- **Time horizon:** Minutes for a smoke test (`phase_duration_seconds: 10`), hours to days for a real run (`1800` or more), and separately across multiple settlement events.
- **Needs:**
  - `./start.sh`-style one-command run that doesn't require a web framework setup
  - Readable terminal output per tick so they can tell the run is progressing
  - `Ctrl-C` safe — the DB must always be consistent; resumption must just work on restart
  - A straightforward "what happened?" dashboard after the run (or during)
  - Clear troubleshooting for OpenRouter errors, missing API keys, malformed YAML, dead tickers

### Secondary: The agent author

- **Background:** Wants to plug in their model (own fine-tune, prompt variant, new provider model) and see how it performs.
- **Touches:** `agents.yaml` to add a new participant, `episode_traces.json` to audit how their model reasoned, the Reasoning view in the dashboard to drill into specific cycles.
- **Needs:**
  - A one-line way to add or replace a participant
  - Every prompt and response captured in `episode_traces.json` in a shape that's greppable and `jq`-able
  - Parse errors surfaced clearly so they can iterate on the model's output format

### Tertiary: The human participant

- **Background:** Curious operator playing a round themselves to feel what the agents see.
- **Touches:** Sets `model: human` in `agents.yaml` and runs in demo mode (not `run_live.py` — that doesn't support human input).
- **Needs:** A clear CLI prompt at their turn, with enough context (contract, position, orderbook, info log) to make a decision in under a minute.

### Quaternary: The dashboard viewer

- **Background:** Same person as the operator, or a collaborator reviewing a past run.
- **Touches:** The dashboard frontend after (or during) a run. Starts with Overview, usually drills into Time Series or Trade Log, sometimes into Reasoning.
- **Needs:**
  - "Is the data live or stale?" at a glance (sidebar status pill)
  - One click from a suspicious fill to the reasoning that produced it
  - The dashboard survives launching before any data exists (waiting screen)

## Core Flows

### Flow 1: Run a live market set

1. **Edit `contracts.yaml`.** Define each market: id, settlement date, multiplier, position limit, max size, search terms, news sources, price ticker.
2. **Edit `agents.yaml`.** List each participant by name, OpenRouter model id (or `human`), and role (`MM` or `HF`).
3. **Export your OpenRouter key.** `export OPENROUTER_API_KEY=sk-or-v1-...` (or set it in `.env`).
4. **Run `python3 run_live.py`.** The supervisor prints the next wall-clock tick time and waits. On every tick, each active market advances one phase; LLM calls fan out concurrently; progress persists to `modelx.db` and (optionally) to `episode_traces.json`.
5. **Monitor.** Terminal output shows every MM quote, every HF order, every fill, and the marks. If an agent throws a parse error, the error prints inline and the run continues.
6. **`Ctrl-C` and restart as needed.** State rebuilds from the DB on restart.
7. **When settlement day arrives,** the contract enters `PENDING_SETTLEMENT` and stops advancing. Other contracts continue.

### Flow 2: Settle a market

1. **Learn the real-world value.** (CPI print, closing price, earnings number, etc.)
2. **Run `python3 settle.py --market <id> --value <float>`.** The engine stamps the value, computes per-agent metrics, persists to `agent_lifetime_stats`, and prints the final scoring tables (MM and HF).
3. **Optionally use `--force`** if you need to settle early.

### Flow 3: Inspect a past (or live) run

1. **Start the dashboard backend.** `python3 dashboard/server.py --db modelx.db --traces episode_traces.json --port 8000`.
2. **Start the frontend.** `cd dashboard/frontend && npm run dev`.
3. **Open http://localhost:5173.** The sidebar pill shows `○ waiting` if no data exists yet, `● live` once contracts are loaded.
4. **Drill down.** Overview → Time Series → (click a fill row) → Reasoning → (select agent + cycle) → full prompt + response.
5. **Switch markets** via the dropdown at the top. Filters and view-local state survive polling.

### Flow 4: Add a new LLM agent to an existing run

1. **Edit `agents.yaml`** — add an entry with a unique `name`, an OpenRouter `model` id, and a `role`.
2. **Restart `run_live.py`.** The new agent joins at the next tick; existing market state rebuilds.
3. **Watch the Reasoning view** during the first cycle to confirm the agent is producing parseable output.

### Flow 5: Play a round as a human (demo mode only)

1. Add a `human` entry to `agents.yaml`. (This flow is not supported by `run_live.py` — would block the async loop.)
2. Use the demo runner (documented in the README) which supports synchronous human prompts at the CLI.
3. Type decisions at the prompts; see your fills and position update between cycles.

## Invariants (must never break)

- **`Ctrl-C` must leave `modelx.db` consistent.** A partial cycle is recoverable on restart — positions, cash, info log, residual orderbook all rebuild from stored fills.
- **A parse error on one agent never aborts the cycle.** The agent skips this cycle (counts against uptime); the others proceed.
- **The dashboard is read-only.** No code path in `dashboard/server.py` or `dashboard/frontend/` writes to `modelx.db` or `episode_traces.json`.
- **`OPENROUTER_API_KEY` never ends up in `episode_traces.json` or in dashboard payloads.** Only model id, prompt, response, and parsed decision.
- **Dashboard launches successfully before any data exists.** Missing `modelx.db` or empty contracts list produces a well-formed empty payload and a waiting screen — not a crash.

## Edge Cases

- **OpenRouter down / rate-limited.** The agent call returns an error; the agent's cycle skips; a readable message prints. The run continues.
- **News RSS empty for this cycle's window.** The info block for this cycle says "No new headlines since last cycle." and trading continues.
- **yfinance returns no bars.** The info block says "Price data unavailable" and trading continues.
- **Two markets settle on the same tick.** Each transitions to `PENDING_SETTLEMENT` independently; `settle.py` handles one at a time.
- **Operator sets an unreasonable `phase_duration_seconds` like 1s.** The supervisor tick races OpenRouter latency and agents may time out; parse errors dominate and uptime collapses. Not a bug in ModelX; the operator should set a realistic tick.
- **Schema change in `modelx/db.py` meets an old `modelx.db` file.** Default policy: wipe. Operators who want to preserve state should back up the DB before the schema change commit.
- **Dashboard running against a DB being written by `run_live.py`.** SQLite's rollback-journal mode keeps reads consistent; a brief race during reload may miss the very latest cycle; next 2s poll catches up.
