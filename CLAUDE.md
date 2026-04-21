# ModelX - LLM Prediction Exchange

Points-based prediction exchange where LLMs and humans trade linear derivatives that settle against real-world quantitative outcomes.

## Tech Stack
- Python 3.11+, CLI-first, no web framework
- SQLite via sqlite3 stdlib (single file, portable)
- httpx for OpenRouter API calls (single gateway to all models)
- No ORMs, no Pydantic — plain dataclasses and dicts
- Manual phase triggers in v1 (no real-time clock)

## Directory Structure
```
modelx/
  models.py          # Dataclasses: Account, Contract, Quote, Order, Fill, CycleState
  db.py              # SQLite persistence
  matching.py        # MM self-matching + HF pro-rata matching
  contracts.py       # Contract creation, info schedule, manual settlement
  agents/
    base.py          # Abstract agent interface
    openrouter.py    # OpenRouter API agent (supports any model)
    human.py         # CLI-based human input
    prompts.py       # System prompts for MM and HF roles
  cycle.py           # Cycle orchestrator (manual phase triggers)
  scoring.py         # MM and HF performance metrics
  runner.py          # Episode runner (full contract lifecycle)
  cli.py             # Entry point
```

## Market Structure

### Hourly Cycles (configurable timing, manual trigger in v1)

**MM Phase:**
- All MMs submit sealed quotes (bid/ask/sizes) simultaneously
- No MM sees another MM's quotes
- At phase end: MM orders match against each other immediately (crossed quotes fill)
- Mark-to-market = VWAP of remaining (unmatched) orderbook
- Remaining quotes become the visible orderbook for HF phase

**HF Phase:**
- HFs see the full remaining orderbook from MM phase
- All HFs submit market orders simultaneously (buy/sell with size, or pass)
- Matching is pro-rata across available liquidity at each price level
- Mark-to-market = Volume-weighted average trade price (of HF fills)

### Matching Rules

**MM self-matching (end of MM phase):**
- If any MM's bid >= another MM's ask, they match
- Crossed orders fill at midpoint of the crossing prices
- Pro-rata if multiple MMs cross at the same level

**HF matching (end of HF phase):**
- All HF orders processed simultaneously — no time priority
- Pro-rata allocation when total HF demand exceeds available MM liquidity at a price level
- HFs lift asks (sorted best to worst) or hit bids (sorted best to worst)
- Book-walking: if best price exhausted, move to next level

### Position Limits
- Absolute position limit: 100 contracts (configurable per contract)
- If an order would push position past the limit, the excess portion is voided (partial fill up to limit)
- An account with position -80 CAN submit buy for 180 (would net to +100), but not buy 181
- The limit is on the resulting absolute position, not the order size itself

### Contract Settlement
- Admin manually settles each contract by entering the settlement value
- All positions marked to settlement value, P&L finalized

### Contract Multiplier
- Each contract has an admin-set multiplier to normalize performance across markets
- PnL = raw_pnl * multiplier

## Scoring

### MM Metrics
- Total PnL (multiplier-adjusted)
- Sharpe ratio (per-cycle PnL series)
- Volume (total contracts traded)
- Volume share (MM's volume / total market volume)
- PnL bps (10000 * PnL / notional, where notional = sum of price * size across the account's fills)
- Uptime (quotes submitted / quotes requested, i.e. cycles where MM actually quoted)
- Consensus (1 - volume_matched_with_other_MMs / total_order_volume)
- 1-cycle markout, 5-cycle markout, 20-cycle markout
- Average absolute position

### HF Metrics
- Total PnL (multiplier-adjusted)
- Sharpe ratio
- 1-cycle markout, 5-cycle markout, 20-cycle markout

### Markout Definition
- N-cycle markout for a fill = (mark_to_market_N_cycles_later - fill_price) * direction
- Positive markout = the trade was good (price moved in your favor)
- For MMs: direction is from MM's perspective (bought = +1, sold = -1)
- For HFs: direction is from HF's perspective
- Each horizon is exposed in both point and bps form. The bps form is size-weighted
  markout divided by size-weighted average fill price, times 10000 — the dashboard
  renders the bps form.
