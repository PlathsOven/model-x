# Product — ModelX Theory

> This document contains the product theory for ModelX. `README.md` is the operator's guide; this file is the "why".

## The Problem

Comparing LLMs on language tasks (MMLU, coding benchmarks, chat arenas) is well-trodden. Comparing LLMs on **positional economic decisions** — "what does this price? how much should I trade? what does my position look like after I see the next headline?" — is not. Yet these are the decisions that power real trading desks, market-making firms, and any business that has to commit capital based on imperfect information.

ModelX builds a sandbox where LLMs compete on exactly that task. Contracts settle against real-world quantitative outcomes (CPI prints, index closes, earnings numbers). Participants are assigned a role — **Market Maker** (posts two-sided quotes) or **Hedge Fund** (sends market orders). Every cycle they receive news and price data, decide, and are scored against settlement.

The output is a reproducible, auditable comparison of LLMs on a decision class that matters outside the lab.

---

## The Two Roles

**Market Makers** face the classic MM problem: quote tight enough to win volume, wide enough to survive adverse selection. Their score is a blend of PnL, volume, markouts (how their trades aged), and consensus (did they quote in line with peers, or alone?).

**Hedge Funds** face the classic taker problem: when do you pay the spread? When is the current mark mispriced? Their score is PnL and markouts — did their trades age well?

Roles are mutually exclusive per agent. An agent in `agents.yaml` is either `MM` or `HF` for the duration of the contract. Agents compete against every other agent in the same role (for shared metrics) and across both roles (for absolute PnL).

---

## The Cycle

Every cycle has two phases:

1. **MM phase** — every MM is asked for a sealed two-sided quote (bid + ask + sizes). No MM sees another MM's quote. At phase close, the matching engine finds crossing quotes between MMs and fills them at midpoint. Whatever's left forms the visible residual orderbook. Mark-to-market = VWAP of remaining orderbook.
2. **HF phase** — every HF sees the residual book + contract + their position + the info log, then buys with size / sells with size / passes. At phase close, HF orders process simultaneously — pro-rata at each price level, book-walking best-to-worst, with position limits enforced by partial fill. Mark-to-market = VWAP of HF fills.

The two phases repeat until `settlement_date`. At that point the contract enters `PENDING_SETTLEMENT` and stops trading. When the operator runs `settle.py` with the real-world value, positions are marked to settlement and PnL is finalized.

---

## What the Agents See

Each agent gets a minimal, neutral context at decision time:

- **Contract:** id, name, description, multiplier, position limit, settlement date.
- **Their role + their current position + their P&L so far.**
- **Information log** accumulated so far: headlines from Google News RSS filtered to allowed `news_sources`, and 15-minute OHLCV bars from yfinance for the contract's `price_ticker`. Both are bounded per cycle by `max_headlines_per_cycle`.
- **Trade history** for this market.
- **For HFs only:** the residual orderbook from this cycle's MM phase.

The agent returns a structured decision (quote or order). If parsing fails, that agent skips the cycle and the run continues; the skip counts against their uptime metric.

All of this is captured in `episode_traces.json` — every prompt, every raw response, every parsed decision, every parse error. Post-run analysis is done against this file.

---

## Scoring

Scoring lives in `modelx/scoring.py`. The full definitions are in `CLAUDE.md`, but the intuition:

- **Total PnL** — the basic measure. Positive = made money. Negative = lost money.
- **Sharpe** — consistency. Same PnL spread across many small wins beats the same PnL from one lucky call.
- **Volume share (MM)** — did you actually quote tight enough to win volume?
- **PnL bps (MM)** — PnL as basis points of notional traded. How good is your edge per unit of capital committed?
- **Uptime (MM)** — did you participate every cycle, or skip?
- **Consensus (MM)** — did you quote near other MMs, or alone? Very low consensus can mean either brilliance or confusion.
- **Markouts (MM, HF)** — did your trades age well? PnL move at 2 / 10 / 40 phases after each fill, from your perspective. Exposed in both point form and bps of notional; the dashboard renders the bps form.
- **Self-cross counts (MM)** — how often did you quote a bid above your own ask? High values mean the model is confused about its own state.

Multipliers normalize scores across contracts of wildly different price scales (a CPI contract and an S&P close contract can both settle at "5500" but that means very different things). Apply `multiplier` in `contracts.yaml` to bring them to comparable magnitudes.

---

## What ModelX is Not

- It is not a live-money trading system. No real trades clear. Points are accounting entries.
- It is not a backtesting framework. Headlines and price bars are fetched live; runs are not reproducible down to the tick unless you cache the news / price fetches yourself.
- It is not a benchmark leaderboard. There is no central registry. Every operator runs their own markets with their own agent pool.
- It does not execute on behalf of anyone. A human participant types their decisions at the CLI (in demo mode); an LLM participant responds via OpenRouter. The engine only adjudicates.

---

## When ModelX is the Right Tool

- Comparing LLMs on economic reasoning rather than language ability.
- Researching how agent behavior changes with role, information, or position constraints.
- Benchmarking new models on real-world-anchored decision tasks.
- Teaching (or learning) how two-sided markets actually work by running one with zero infrastructure.

## When It Isn't

- Real trading. Use a real exchange and a real broker.
- Zero-shot eval. The point is multi-cycle decision-making against evolving information.
- Replicable lab benchmarking. Live news is inherently unreproducible.
