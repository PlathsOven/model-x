# ModelX

A points-based prediction exchange where Large Language Models (and humans) trade derivative contracts that settle against real-world quantitative outcomes — CPI prints, corporate earnings, temperature readings, anything you can put a number on. Two roles: **Market Makers** post sealed two-sided quotes; **Hedge Funds** see the resulting orderbook and send market orders. The cycle repeats. At settlement you enter the true value, and the engine scores every participant.

Run with `run_live.py` — it drives N contracts concurrently on a real-time wall-clock schedule, pulls headlines from Google News RSS and price bars from yfinance between cycles, persists to SQLite, and advances every market on each global tick. Settle each market manually with `settle.py` when the real-world value is known.

## Prerequisites

- Python 3.11 or newer
- An OpenRouter API key (sign up at <https://openrouter.ai>) — only needed if any of your participants are LLMs

## Setup

### 1. Get the code

```bash
git clone <repo-url> model-x
cd model-x
```

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or install manually:

```bash
pip install httpx pyyaml feedparser yfinance
```

- `httpx` — required for OpenRouter API calls
- `pyyaml` — recommended but optional (there's a built-in fallback YAML parser)
- `feedparser` — required for live news mode (Google News RSS parsing)
- `yfinance` — required for live news mode (real-time price data)

If you'd rather not use a venv on macOS, append `--break-system-packages` to the pip command.

### 3. Set your API key

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
```

You can skip this if every participant in your config is `model: human`.

## Configure who plays

Edit `agents.yaml` in the repo root:

```yaml
agents:
  - name: claude-sonnet
    model: anthropic/claude-sonnet-4
    role: MM

  - name: gpt-4o
    model: openai/gpt-4o
    role: MM

  - name: deepseek-r1
    model: deepseek/deepseek-r1
    role: HF
```

Each entry has three fields:

- **`name`** — your label for the participant (shown in scoring and traces)
- **`model`** — an OpenRouter model id (browse <https://openrouter.ai/models>), or the literal string `human` to play yourself
- **`role`** — `MM` for market maker (posts two-sided quotes) or `HF` for hedge fund (sends market orders)

Add or remove participants by editing this file. The script has no hardcoded agent names — anything you put here becomes the cast of the next run.

## Configure the contracts

Edit `contracts.yaml` in the repo root. This file is the primary admin surface — every contract you add here becomes a live exchange instance the moment you (re)start the runner.

```yaml
phase_duration_seconds: 1800   # 30 min — global wall-clock tick

contracts:
  - id: sp500-hourly
    name: "S&P 500 close price"
    description: "What will the S&P 500 close at today?"
    settlement_date: "2026-04-11 16:00:00T-04:00"
    multiplier: 0.01
    position_limit: 100
    max_size: 50
    search_terms:
      - "S&P 500"
      - "US stock market"
      - "Wall Street"
    price_ticker: "^GSPC"
    news_sources:
      - reuters.com
      - cnbc.com
      - bloomberg.com
    max_headlines_per_cycle: 10
```

Field reference:

- **`phase_duration_seconds`** (top level, global) — how long each MM/HF phase lasts. **All contracts advance on the same global wall-clock tick.** A 1800s value lines ticks up to clock boundaries (00:00, 00:30, 01:00, …); a 60s value would tick once a minute. Set this to a few seconds when smoke-testing locally, then increase for production runs.
- **`id`** — short slug used everywhere (account ids, dashboard URLs, settlement CLI). Must be unique.
- **`name`** / **`description`** — display strings shown to agents and on the dashboard.
- **`settlement_date`** — when the real-world value is expected. When the timestamp is reached, the market enters `PENDING_SETTLEMENT` and stops trading.
- **`multiplier`** — scales final P&L. Use this to normalize across contracts with very different price scales.
- **`position_limit`** — absolute cap on each agent's net position (the engine partially fills orders that would exceed this).
- **`max_size`** — maximum order size shown to agents in their context.
- **`search_terms`** — list of queries sent to Google News RSS. Each term is searched independently; results are merged and deduplicated. More terms = broader coverage, but watch for noise.
- **`price_ticker`** — a yfinance ticker symbol. The system pulls the last 24 fifteen-minute OHLCV bars before each cycle. Common tickers: `^GSPC` (S&P 500), `^DJI` (Dow), `^IXIC` (Nasdaq), `GC=F` (Gold), `CL=F` (Oil), `^VIX` (VIX), `AAPL` (individual stocks). Set to `null` or omit to skip price data.
- **`news_sources`** — allowed news domains. Headlines from other sources are filtered out. Use this to control quality — major financial outlets tend to produce cleaner, more relevant headlines.
- **`max_headlines_per_cycle`** — cap on headlines shown per cycle. Lower values keep the agent context focused; higher values give more breadth.

Every agent in `agents.yaml` participates in **every** contract simultaneously, with isolated positions and cash per contract.

## Run

```bash
python3 run_live.py
```

Optional flags:

- `--contract contracts.yaml` — contract + news config (default: `contracts.yaml`)
- `--agents agents.yaml` — agents config (default: `agents.yaml`)
- `--db modelx.db` — SQLite file used for state, scoring and dashboard (default: `modelx.db`)

The supervisor prints the next wall-clock tick time and waits. On every tick, **every active contract** advances one phase (MM or HF) in parallel, all LLM calls fan out concurrently with `asyncio.gather`, and progress is persisted to the DB. You can `Ctrl-C` and restart at any time — the runner reloads each market's progress from the DB and resumes mid-cycle if needed (positions, cash, info log, and the residual orderbook all rebuild from stored fills).

Before each MM phase, the system:

1. Fetches headlines from Google News RSS matching the contract's `search_terms`, filtered to only allowed `news_sources`, and only articles published since the previous cycle
2. Downloads the latest 15-minute OHLCV bars from yfinance for the contract's `price_ticker` (if configured)
3. Formats both into a single info block that agents see in their context

When a contract hits its settlement datetime it transitions to `PENDING_SETTLEMENT` and stops trading; other contracts keep running.

**Note:** `run_live.py` does not support `model: human` in agents.yaml — human input would block the async event loop.

## Settle a market

When you know the real-world value:

```bash
python3 settle.py --market cpi-yoy-may-2025 --value 2.8
```

Optional flags:

- `--db modelx.db` — SQLite db (default: `modelx.db`)
- `--force` — settle a market that isn't in `PENDING_SETTLEMENT` yet

This writes the settlement value to the contract row, computes `score_mm` / `score_hf` for every participant, persists per-market lifetime stats to the `agent_lifetime_stats` table, and prints a final P&L table. After settlement the market shows up under the **Lifetime** tab in the dashboard, aggregated across every other market the same agent has settled in.

Example of what agents receive:

```
=== PRICE DATA (^GSPC, 15min bars) ===
2026-04-09 09:30:00: O=5502.31 H=5510.44 L=5498.12 C=5508.77 V=142835000
2026-04-09 09:45:00: O=5508.77 H=5512.00 L=5505.20 C=5511.33 V=98421000
...

=== HEADLINES (since 2026-04-09 14:00 UTC) ===
[Reuters] S&P 500 hits session high as tech rallies (14:32)
[CNBC] Fed officials signal patience on rate cuts (14:18)
[Bloomberg] US jobless claims fall to three-month low (14:05)
```

**Choosing good search terms:** Start broad (e.g. `"S&P 500"`, `"US stock market"`) and add specific terms for events you expect to matter (e.g. `"Fed rate decision"`, `"US jobs report"`). The system deduplicates across terms, so overlap is fine.

**If news or price fetches fail:** The system degrades gracefully. Network errors, missing tickers, or empty RSS feeds produce fallback text ("No new headlines since last cycle." / "Price data unavailable") and the market continues trading.

## Dashboard

A web dashboard visualizes everything live: per-market timeseries, orderbook, fills, scores, and the cross-market Lifetime tab.

```bash
# in one terminal, start the runner
python3 run_live.py --db modelx.db

# in another terminal, start the dashboard backend (reads modelx.db)
python3 dashboard/server.py --db modelx.db --port 8000

# in a third terminal, start the dashboard frontend
cd dashboard/frontend && npm install && npm run dev
```

The dashboard polls every 2 seconds and live-updates as the runner writes new fills. A market selector dropdown at the top lets you switch between markets; the existing Time Series, Orderbook, Trade Log, Metrics, and Positions tabs all rescope to the selected market. The new **Lifetime** tab is global — it aggregates per-agent stats across every settled market in the database (you'll see it populate after you run `settle.py` on at least one market).

> **Note:** the dashboard server is a long-running Python process that imports `modelx` once at startup. If you upgrade the code (e.g. add a column to a model dataclass), restart the dashboard backend so it re-imports the new classes — otherwise you'll see errors like `Account.__init__() got an unexpected keyword argument 'market_id'`.

## How a cycle works

```
[Enter] open MM phase of cycle 0...     <- press Enter
=== Cycle 0 MM phase ===
  claude-sonnet: bid 5@2.65 / ask 5@2.75
  gpt-4o: bid 5@2.70 / ask 5@2.80

[Enter] close MM phase of cycle 0...    <- press Enter
  MM fills: 0
  MM mark: 2.7250
  Residual book (4 levels):
    Asks (lowest to highest):
      5 @ 2.75  (claude-sonnet)
      5 @ 2.80  (gpt-4o)
    Bids (highest to lowest):
      5 @ 2.70  (gpt-4o)
      5 @ 2.65  (claude-sonnet)

[Enter] open HF phase of cycle 0...     <- press Enter
=== Cycle 0 HF phase ===
  deepseek-r1: buy 3

[Enter] close HF phase of cycle 0...    <- press Enter
  HF fills: 1
    deepseek-r1 buys 3@2.75 from claude-sonnet
  HF mark: 2.7500

  End of cycle 0 — positions:
    claude-sonnet: -3
    deepseek-r1: 3
    gpt-4o: 0
```

The four triggers, in order:

1. **Open MM phase** — every market maker is asked for a sealed two-sided quote (bid + ask + sizes). They can also skip, which counts against their uptime metric. No MM sees another MM's quote.
2. **Close MM phase** — the matching engine looks for crossing quotes between MMs and fills them at the midpoint. Whatever's left forms the visible orderbook for the HF phase. The mark-to-market price prints.
3. **Open HF phase** — every hedge fund sees the residual orderbook, the contract, their position, and the information revealed so far, then either buys with size, sells with size, or passes.
4. **Close HF phase** — HFs walk the book best-to-worst. If demand exceeds liquidity at a price, the available contracts split pro-rata across HFs. Position limits are enforced. Mark-to-market prints.

End-of-cycle positions print. The next cycle opens.

## Settlement

In live mode, settlement is decoupled from the run loop. When a market reaches its `num_cycles`, it stops trading and enters `PENDING_SETTLEMENT`. When you have the real-world value, run:

```bash
python3 settle.py --market cpi-yoy-may-2025 --value 2.8
```

The engine writes the value to the contract row, computes per-agent P&L, persists lifetime stats, and prints a final scoring table.

A short position makes money if settlement comes in below where they sold; a long position makes money if settlement comes in above where they bought. The engine works out the rest.

## Reading the scoring output

Two tables print after settlement: one for market makers, one for hedge funds.

### MM scores

| Field | What it means |
|---|---|
| `total_pnl` | Final P&L in points (positive = profit) |
| `sharpe` | Risk-adjusted return — mean per-cycle P&L change divided by its standard deviation. Higher = more consistent |
| `volume` | Total contracts traded across all cycles |
| `volume_share` | Their share of total market volume. Note that both sides of every fill count, so shares across all participants sum above 1.0 |
| `pnl_bps` | P&L per contract traded, in basis points — "edge per trade" |
| `uptime` | Fraction of cycles where the MM submitted a quote. 1.0 = quoted every cycle |
| `consensus` | 1.0 minus the share of their volume that traded against another MM. High consensus = they were alone at their price |
| `markout_1` / `markout_5` / `markout_20` | Average per-contract P&L move 1, 5, and 20 cycles after each fill, from the MM's perspective. Positive = trades that aged well |
| `avg_abs_position` | Average absolute open position — a risk-taking proxy |
| `self_cross_count` / `self_cross_volume` | How many times (and how many contracts) the MM traded against itself by quoting a bid above its own ask. Diagnostic for confused models |

### HF scores

| Field | What it means |
|---|---|
| `total_pnl` | Final P&L in points |
| `sharpe` | Same as MM |
| `markout_1` / `markout_5` / `markout_20` | Average per-contract P&L move N cycles after each HF fill |

## Playing as a human

Add a `human` entry to `agents.yaml` alongside (or instead of) the LLMs:

```yaml
agents:
  - name: claude-sonnet
    model: anthropic/claude-sonnet-4
    role: MM
  - name: me
    model: human
    role: MM      # or HF
```

When the cycle reaches your turn, you'll see a prompt at the terminal.

**As an MM:**

```
=== MM quote for me (US CPI YoY May 2025) ===
Cycle 3/20
Position: 5  P&L: 0.1234  Limit: ±100
Multiplier: 1.0  Settlement: 2025-06-11
Description: US CPI YoY for May 2025 (BLS release).

Information log:
- (cycle 0) US CPI has printed 2.4%, 2.6%, ...
- (cycle 2) Goldman Sachs economists forecast May CPI at 2.7% YoY...

Trade history:
  cpi-yoy-may-2025:1 HF SELL 3@2.75 (vs deepseek-r1)

Submit a quote? [Y/n]
```

Press `y` (or just Enter) to quote, then type `Bid price`, `Bid size`, `Ask price`, `Ask size` one at a time. Press `n` to skip the cycle.

**As an HF:**

```
=== HF order for me (US CPI YoY May 2025) ===
Cycle 3/20
Position: 0  P&L: 0.0000  Limit: ±100
...

Orderbook:
Asks (lowest to highest):
  5 @ 2.75  (claude-sonnet)
  3 @ 2.78  (gpt-4o)
Bids (highest to lowest):
  4 @ 2.72  (claude-sonnet)
  2 @ 2.70  (gpt-4o)

Side (buy/sell/pass):
```

Type `buy`, `sell`, or `pass`. If you choose to trade, you'll then be asked for `Size`.

LLM agents make their decisions in the same phase, sealed from yours.

## Reasoning traces

After the episode finishes, every LLM agent's full request/response history is written to `episode_traces.json` (or wherever you pointed `--traces`). The structure:

```json
{
  "contract": { "id": "...", "name": "...", "settlement_value": 2.8, ... },
  "num_cycles": 20,
  "info_schedule": { "0": "...", "2": "...", ... },
  "agents": {
    "claude-sonnet": {
      "model": "anthropic/claude-sonnet-4",
      "role": "MM",
      "traces": [
        {
          "phase": "MM",
          "cycle_number": 0,
          "request": "<the full prompt the model saw>",
          "raw_response": "<verbatim text the model returned>",
          "parsed": {
            "bid_price": 2.65,
            "ask_price": 2.75,
            "bid_size": 5,
            "ask_size": 5,
            "reasoning": "<the model's stated reasoning>"
          },
          "decision": { "bid_price": 2.65, "bid_size": 5, ... },
          "error": null
        }
      ]
    }
  }
}
```

The most useful field is `parsed.reasoning` — that's where the model explains *why* it priced where it did. `request` is the full prompt with current state filled in, and `raw_response` is what came back verbatim, in case you want to debug a parse error.

To browse the file:

```bash
python3 -m json.tool episode_traces.json | less
```

To pull one agent's reasoning for a specific cycle (with `jq` installed):

```bash
jq '.agents."claude-sonnet".traces[] | select(.cycle_number == 5) | .parsed.reasoning' episode_traces.json
```

Human players don't generate trace entries.

## Persisting between runs

Everything persists to `modelx.db` by default (every quote, order, fill, cycle state, market, and account). Re-running the same market wipes previous data and starts fresh.

You can open `modelx.db` with any SQLite client (`sqlite3 modelx.db`) and query it directly.

## Tips

- **API budget**: each phase asks every agent once. With 4 agents and 20 cycles, that's ~80 OpenRouter calls per market. Cost depends on the models you pick. In live mode, multiply by the number of simultaneous markets.
- **Smoke testing**: set `phase_duration_seconds: 10` in `contracts.yaml` for quick validation. Switch to 1800 (30 min) or higher for production runs.
- **Adding a contract mid-run**: add a new entry to `contracts.yaml` and restart `run_live.py`. The new contract joins at the next tick; existing contracts resume seamlessly from the DB.
- **Bad model responses**: if an LLM returns malformed JSON, the error prints inline, that agent skips the cycle, and the run continues. In demo mode, the broken response still lands in `episode_traces.json` so you can debug it later.
- **Dashboard after code updates**: if you pull new code that changes model dataclasses, restart the dashboard server (`python3 dashboard/server.py ...`) so it reimports the updated classes.
- **Live news mode during market hours**: for equity contracts, run `run_live.py` while the market is open to get the freshest price bars and most active news flow. Outside hours, yfinance returns the most recent available data.
- **Customizing news contracts**: to trade a different underlying, just update `contracts.yaml` — change the `search_terms`, `price_ticker`, and `multiplier`. For example, gold futures: `price_ticker: "GC=F"`, `search_terms: ["gold price", "precious metals", "gold futures"]`, `multiplier: 0.001`.
