# Claude Code Session Guide

Your private playbook. Keep open on a second screen. Don't paste this.

## Build Order (updated to reflect actual session)

✅ 1. models.py — done
✅ 2. matching.py — done (MM self-matching, HF pro-rata, largest-remainder, position tiebreak)
🔜 3. cycle.py — wiring MM → HF → mark-to-market, manual triggers
4. agents/ — OpenRouter integration, prompts
5. runner.py — episode loop with info schedule
6. scoring.py — MM and HF metrics
7. db.py — persistence (deferred, only needed once in-memory flow works)
8. First episode — run Claude vs GPT vs DeepSeek


## 3. Cycle Orchestrator (NEXT)

CC should already be building this. If it needs guidance:

"`cycle.py` manages state for a single cycle. Four manual trigger functions:

1. `start_mm_phase(cycle)` — prompts all MM agents (or waits for human input), collects one Quote per MM
2. `end_mm_phase(cycle)` — runs `match_mm_phase()` on collected quotes, computes remaining book + VWAP mark, updates positions, stores fills
3. `start_hf_phase(cycle)` — reveals remaining orderbook to all HFs, collects one Order per HF (side+size or pass)
4. `end_hf_phase(cycle)` — runs `match_hf_phase()`, computes VWAP trade price mark, updates positions, stores fills, delivers new info for next cycle

State between phases lives in-memory — a dict of positions, a list of fills, the current orderbook. No database yet. Each function prints a summary of what happened so I can see it in the CLI."


## 4. Agent Interface

"Build `agents/base.py` with an abstract class — two methods: `get_quote(state) -> Quote` for MMs and `get_order(state, orderbook) -> Order` for HFs. 

Then `agents/openrouter.py` — takes a model string (e.g. `anthropic/claude-sonnet-4`) on init. Calls OpenRouter's chat completions endpoint via httpx. Parses JSON response. Handle markdown fence stripping and type coercion (string numbers → float/int).

Then `agents/human.py` — CLI prompts for manual play.

Put system prompts in `agents/prompts.py`."

Then give it the system prompts from below.


## 5. Episode Runner

"Build `runner.py`. Takes a contract, info schedule dict, and list of (agent, role) pairs. Loops through N cycles, calling the cycle orchestrator functions in order. Each phase transition waits for me to press Enter. After all cycles, prompts me to enter the settlement value. Prints final positions and P&L for all accounts."


## 6. Scoring

"Now `scoring.py`. Two functions: `score_mm(fills, cycles, positions, contract)` and `score_hf(fills, cycles, positions, contract)`.

MM metrics:
- Total PnL (raw * contract multiplier)
- Sharpe (std of per-cycle PnL changes)
- Volume (total contracts traded, including self-trades)
- Volume share (their volume / total market volume)
- PnL bps (10000 * PnL / volume)
- Uptime (cycles where MM submitted a quote / total cycles)
- Consensus (1 - volume_matched_with_other_MMs / total_order_volume), self-trades included
- 1-cycle markout, 5-cycle markout, 20-cycle markout
- Average absolute position
- Self-cross count and self-cross volume

HF metrics:
- Total PnL (raw * contract multiplier)
- Sharpe
- 1-cycle markout, 5-cycle markout, 20-cycle markout

Markout: N-cycle markout for a fill = (mark_price_N_cycles_later - fill_price) * direction. Use the mm_mark or hf_mark from CycleState, whichever is the last available mark for that cycle."


## 7. Database (deferred)

"Now that the in-memory flow works, add `db.py`. SQLite, single file. Tables for accounts, contracts, quotes, orders, fills, cycle_states. Migrate the in-memory state in cycle.py to read/write from SQLite. Everything should survive a restart."


## 8. First Episode

"Create a test script that:
- Creates a CPI contract: question='US CPI YoY May 2025', multiplier=1.0, position_limit=100
- Sets up 3 agents via OpenRouter: Claude Sonnet as MM1, GPT-4o as MM2, DeepSeek R1 as HF1
- Uses the info schedule I'll paste (20 cycles, info on cycles 1,3,5,8,10,13,16,18,20)
- Runs the episode with manual phase triggers (Enter to advance)
- At end, I enter settlement value 2.8
- Prints full scoring for all 3 participants
- Dumps all reasoning traces to a JSON file"


## System Prompts (for agents/prompts.py)

### MM_SYSTEM_PROMPT

```python
MM_SYSTEM_PROMPT = """You are a Market Maker on a prediction exchange. You trade a linear derivative contract that settles at the true value of a real-world quantitative outcome.

Your job:
- Assess the likely settlement value based on all available information
- Post a two-sided quote: a bid (where you'll buy) and an ask (where you'll sell), each with a size
- Manage inventory risk — skew your quotes to encourage trades that reduce your position
- Earn spread while avoiding adverse selection from better-informed traders

Inventory management: if you are long, lower both bid and ask to discourage further buying and encourage selling. If short, raise both. The larger your position, the more aggressive the skew should be.

Your quotes may match against other Market Makers if your bid is above their ask or vice versa. Price yourself carefully — being too aggressive means you trade against other MMs at bad prices. If your own bid is above your own ask, you will self-cross and trade against yourself.

Constraints:
- Absolute position cannot exceed {position_limit} contracts
- Bid must be strictly less than ask (unless you want to self-cross)
- Sizes between 1 and {max_size}

Current state:
- Contract: {contract_question}
- Settlement date: {settlement_date}
- Multiplier: {multiplier}
- Your position: {position} contracts (positive = long, negative = short)
- Your P&L: {pnl:.4f} points
- Current cycle: {cycle_number} of ~{total_cycles}

Your trade history (most recent first):
{trade_history}

All information received so far:
{information_log}

Respond with ONLY valid JSON, no markdown fences:
{{"fair_value_estimate": <number>, "bid_price": <number>, "ask_price": <number>, "bid_size": <integer>, "ask_size": <integer>, "reasoning": "<your pricing logic, inventory considerations, and information assessment>"}}"""
```

### HF_SYSTEM_PROMPT

```python
HF_SYSTEM_PROMPT = """You are a Hedge Fund trader on a prediction exchange. You trade a linear derivative contract that settles at the true value of a real-world quantitative outcome.

Your job:
- Form a directional view on the likely settlement value
- Evaluate the Market Maker orderbook to find mispricing
- Send a market order when your edge exceeds the cost of crossing the spread
- Manage position size relative to conviction

Only trade when you believe the available prices are meaningfully wrong. Passing is often the right decision. The spread is a real cost — you need to be confident your view is right AND that the mispricing is large enough to overcome the spread.

Your order will be matched pro-rata with other Hedge Funds if total demand exceeds available liquidity.

You submit exactly one decision per phase: buy (with size), sell (with size), or pass.

Constraints:
- Absolute position cannot exceed {position_limit} contracts
- Size between 1 and {max_size}

Current state:
- Contract: {contract_question}
- Settlement date: {settlement_date}
- Multiplier: {multiplier}
- Your position: {position} contracts (positive = long, negative = short)
- Your P&L: {pnl:.4f} points
- Current cycle: {cycle_number} of ~{total_cycles}

Your trade history (most recent first):
{trade_history}

All information received so far:
{information_log}

Available orderbook (remaining after MM self-matching):
{order_book}

Respond with ONLY valid JSON, no markdown fences:
{{"fair_value_estimate": <number>, "side": "buy"|"sell"|"pass", "size": <integer or 0 if passing>, "reasoning": "<your directional view, edge assessment, and position management logic>"}}"""
```


## Key Moments to Engineer

### During cycle.py — check state management
Make sure positions update correctly across phases. A common bug: MM positions update after MM matching, but the HF phase doesn't see the updated MM positions when checking position limits. The HF matching should use post-MM-matching positions for both MMs and HFs.

### During agent integration — JSON parsing
LLMs love wrapping JSON in ```json fences. Make sure the parser strips those. Also watch for:
- String numbers ("3.5" instead of 3.5)
- Reasoning field containing unescaped quotes that break JSON
- DeepSeek R1 putting <think> tags before the JSON

### After first episode — analyze like a trader
Don't say "it works." Look for:
- **MM self-matching frequency:** Are MMs crossing each other often? That means their fair values are far apart — interesting signal about model disagreement.
- **HF selectivity:** Is the HF trading every cycle or only when new info arrives? Trading every cycle means the prompt isn't making spread cost salient enough.
- **Information processing lag:** How many cycles after new info does each agent adjust fair value? If the HF adjusts faster than the MMs, the markouts will be negative for MMs.
- **Inventory skew:** Do MMs actually skew quotes when they accumulate positions, or quote symmetrically regardless?

Say something like: "The markout data tells the whole story. MM1 has negative 5-cycle markouts on every fill where the HF was the counterparty — that's pure adverse selection. But MM1's markouts against MM2 are roughly flat. So MM1 is fine at pricing relative to other MMs but can't keep up with the HF's information processing. That's exactly the capability gap this benchmark is designed to measure."

### Iterate on prompts
If the HF trades every single cycle:
"The HF is overtrading. It's treating every small discrepancy as an opportunity. Let me add stronger language about the cost of crossing the spread and that passing is the default, not the exception."

If MMs don't skew for inventory:
"Both MMs are quoting symmetrically even when they're long 50+ contracts. The inventory management instruction isn't landing. Let me make it more explicit with a concrete example in the prompt."