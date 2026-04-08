# ModelX

A points-based prediction exchange where Large Language Models (and humans) trade derivative contracts that settle against real-world quantitative outcomes — CPI prints, corporate earnings, temperature readings, anything you can put a number on. Two roles: **Market Makers** post sealed two-sided quotes; **Hedge Funds** see the resulting orderbook and send market orders. The cycle repeats. At settlement you enter the true value, and the engine scores every participant.

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
pip install httpx pyyaml
```

`httpx` is required to talk to OpenRouter. `pyyaml` is recommended but optional — there's a built-in fallback parser for `agents.yaml` if you skip it.

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

## What you're trading

Out of the box, the demo runs a 20-cycle episode on:

> **US CPI YoY May 2025** — predict the year-over-year US CPI print for May 2025

The contract has a position limit of 100 and a multiplier of 1.0. Information (analyst forecasts, related releases, market commentary) drips out across the episode on cycles 0, 2, 4, 7, 9, 12, 15, 17, and 19. Every agent sees this information cumulatively as the run progresses.

To trade something different, edit `CONTRACT`, `INFO_SCHEDULE_RAW`, and `NUM_CYCLES` near the top of `run_demo.py`.

## Run an episode

```bash
python3 run_demo.py
```

Optional flags:

- `--config agents.yaml` — path to your participant config (default: `agents.yaml`)
- `--db modelx.db` — SQLite file to persist the whole run (default: in-memory; everything dies when the process exits)
- `--traces episode_traces.json` — output path for reasoning traces (default: `episode_traces.json`)

You'll see your participants listed, then the cycle loop begins. **Each cycle has 4 manual triggers; press Enter at each to advance.**

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

After cycle 19 closes, you'll see:

```
============================================================
All cycles complete. Time to settle.
Settlement value:
```

Type the real-world value the contract should settle to, then press Enter. For the demo, that's the actual May 2025 CPI YoY number from the BLS release — for example, `2.8`.

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
            "fair_value_estimate": 2.7,
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

By default the database lives in memory and disappears when the process exits. To keep the full run on disk — every quote, order, fill, cycle state, and account — pass a path:

```bash
python3 run_demo.py --db modelx.db
```

You can then open `modelx.db` with any SQLite client (`sqlite3 modelx.db`) and query it directly.

## Tips

- **API budget**: a 20-cycle run with 3 LLM agents is about 80 OpenRouter calls (each MM is asked once per cycle, each HF is asked once per cycle). Cost depends on the models.
- **Wall time**: real LLM calls take a few seconds each, so a 20-cycle run with 3 agents typically takes 5–15 minutes. The manual Enter triggers exist so you can read each phase and Ctrl-C if anything looks off.
- **Bad model responses**: if an LLM returns malformed JSON, the error prints inline, that agent skips the cycle, and the run continues. The broken response still lands in `episode_traces.json` so you can debug it later.
- **Reproducibility**: pass `--db <some-file>.db` for any run you might want to inspect later. Use the in-memory default for quick experiments.
