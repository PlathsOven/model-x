"""Async per-market state machine driven by the global supervisor.

A `MarketRunner` owns the in-memory state of one live market (positions,
cash, info log, the active mid-cycle `Cycle` object if any). The supervisor
calls `step(phase_deadline)` once per global wall-clock tick; each call runs
exactly one phase (MM or HF) end-to-end:

1. (MM only) reveal any new info, open a new `Cycle`
2. fan out `agent.get_quote_async` / `get_order_async` for every participant
   in parallel via `asyncio.gather`, each wrapped in `wait_for(deadline)`
3. submit valid responses to the `Cycle` and persist via the cycle helpers
4. run matching (`close_mm_phase` / `close_hf_phase`)
5. update local state and persist market progress (`update_market_progress`)

State is always recoverable from the DB so a supervisor restart resumes
mid-market without loss. `_restore_state()` reconstructs positions, cash,
info log, and (if mid-cycle) the active `Cycle` from stored fills/quotes.
"""

import asyncio
import sqlite3
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .agents.base import Agent, AgentContext
from .config import AgentSpec, MarketConfig
from .cycle import (
    Cycle,
    close_hf_phase,
    close_mm_phase,
    load_cycle,
    open_cycle,
    submit_order,
    submit_quote,
)
from .db import (
    list_cycle_states,
    list_fills_by_contract,
    positions_for_contract,
    update_market_progress,
    upsert_account,
    upsert_contract,
    upsert_market,
)
from .matching import BookLevel
from .models import Account, Contract, Fill, Market


@dataclass
class Participant:
    agent: Agent
    account_id: str  # f"{market_id}:{agent_name}"
    role: str        # "MM" or "HF"
    spec: AgentSpec  # original config (for naming/debugging)


class MarketRunner:
    """Drives one live market. The supervisor steps it once per global tick."""

    def __init__(
        self,
        config: MarketConfig,
        db: sqlite3.Connection,
        participants: List[Participant],
        market: Market,
        contract: Contract,
    ):
        self.config = config
        self.db = db
        self.participants = participants
        self.market = market
        self.contract = contract

        self._mms = [p for p in participants if p.role == "MM"]
        self._hfs = [p for p in participants if p.role == "HF"]

        # In-memory caches reconstructed from DB on init / restart.
        self._positions: Dict[str, int] = {}
        self._cash: Dict[str, float] = {}
        self._info_log: List[str] = []
        self._all_fills: List[Fill] = []
        self._current_mark: float = 0.0
        self._cycle: Optional[Cycle] = None

        self._restore_state()

    # ---------- public API ----------

    def is_active(self) -> bool:
        return self.market.state == "RUNNING"

    async def step(self, phase_deadline: float) -> None:
        """Advance this market by one phase. Called by the supervisor."""
        if not self.is_active():
            return
        try:
            if self.market.pending_mm:
                await self._run_mm_phase(phase_deadline)
                self.market.pending_mm = 0
            else:
                await self._run_hf_phase(phase_deadline)
                self.market.pending_mm = 1
                self.market.current_cycle += 1
                if self.market.current_cycle >= self.config.num_cycles:
                    self.market.state = "PENDING_SETTLEMENT"
                    print(
                        f"[{self.market.id}] all {self.config.num_cycles} cycles "
                        f"complete — entered PENDING_SETTLEMENT",
                        flush=True,
                    )
            update_market_progress(
                self.db,
                self.market.id,
                self.market.state,
                self.market.current_cycle,
                self.market.pending_mm,
            )
        except Exception as e:
            # Surface the failure but keep the supervisor alive — other
            # markets should continue to run.
            print(
                f"[{self.market.id}] step failed: {type(e).__name__}: {e}",
                flush=True,
            )
            traceback.print_exc()

    # ---------- phase handlers ----------

    async def _run_mm_phase(self, phase_deadline: float) -> None:
        cycle_idx = self.market.current_cycle
        # Reveal any new info for this cycle
        new_info = self.config.info_schedule.get(cycle_idx, [])
        for line in new_info:
            self._info_log.append(f"(cycle {cycle_idx}) {line}")
        if new_info:
            print(
                f"[{self.market.id}] cycle {cycle_idx}: revealed "
                f"{len(new_info)} info item(s)",
                flush=True,
            )

        # Open the cycle
        self._cycle = open_cycle(
            self.contract,
            cycle_idx,
            positions=self._positions,
            db=self.db,
        )

        print(
            f"[{self.market.id}] cycle {cycle_idx} MM phase: "
            f"collecting quotes from {len(self._mms)} MMs",
            flush=True,
        )

        # Fan out MM quote calls in parallel
        results = await self._gather_with_deadline(
            [
                self._safe_quote_call(p, cycle_idx)
                for p in self._mms
            ],
            phase_deadline,
        )

        for p, result in zip(self._mms, results):
            if result is None:
                continue  # error or pass
            try:
                submit_quote(self._cycle, result)
            except Exception as e:
                print(
                    f"[{self.market.id}] {p.account_id} submit_quote failed: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

        # Run MM-MM matching
        mm_fills, residual_book, mm_mark = close_mm_phase(self._cycle)
        self._all_fills.extend(mm_fills)
        _apply_cash(self._cash, mm_fills)
        if mm_mark > 0:
            self._current_mark = mm_mark
        self._positions.update(self._cycle.positions)
        print(
            f"[{self.market.id}] cycle {cycle_idx} MM closed: "
            f"{len(mm_fills)} fills, mark={mm_mark:.4f}, "
            f"residual book={len(residual_book)} levels",
            flush=True,
        )

    async def _run_hf_phase(self, phase_deadline: float) -> None:
        cycle_idx = self.market.current_cycle
        # Restart-safety: if we resumed mid-cycle, _cycle was None
        if self._cycle is None:
            cycle_id = f"{self.contract.id}:{cycle_idx}"
            self._cycle = load_cycle(self.db, self.contract, cycle_id)
            self._positions.update(self._cycle.positions)

        residual_book = self._cycle.residual_book
        print(
            f"[{self.market.id}] cycle {cycle_idx} HF phase: "
            f"collecting orders from {len(self._hfs)} HFs",
            flush=True,
        )

        results = await self._gather_with_deadline(
            [
                self._safe_order_call(p, cycle_idx, residual_book)
                for p in self._hfs
            ],
            phase_deadline,
        )

        for p, result in zip(self._hfs, results):
            if result is None:
                continue  # error or pass
            try:
                submit_order(self._cycle, result)
            except Exception as e:
                print(
                    f"[{self.market.id}] {p.account_id} submit_order failed: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

        hf_fills, hf_mark = close_hf_phase(self._cycle)
        self._all_fills.extend(hf_fills)
        _apply_cash(self._cash, hf_fills)
        if hf_mark > 0:
            self._current_mark = hf_mark
        self._positions.update(self._cycle.positions)
        print(
            f"[{self.market.id}] cycle {cycle_idx} HF closed: "
            f"{len(hf_fills)} fills, mark={hf_mark:.4f}",
            flush=True,
        )
        # Cycle done — clear so the next MM tick opens a fresh one.
        self._cycle = None

    # ---------- per-agent calls ----------

    async def _safe_quote_call(
        self,
        p: Participant,
        cycle_idx: int,
    ) -> Any:
        ctx = self._build_context(p, cycle_idx)
        try:
            return await p.agent.get_quote_async(ctx)
        except Exception as e:
            print(
                f"[{self.market.id}] {p.account_id} quote error: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            return None

    async def _safe_order_call(
        self,
        p: Participant,
        cycle_idx: int,
        book: List[BookLevel],
    ) -> Any:
        ctx = self._build_context(p, cycle_idx)
        try:
            return await p.agent.get_order_async(ctx, book)
        except Exception as e:
            print(
                f"[{self.market.id}] {p.account_id} order error: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            return None

    async def _gather_with_deadline(
        self,
        coros: List[Any],
        phase_deadline: float,
    ) -> List[Any]:
        """Run coroutines in parallel, cancel any that miss the phase deadline."""
        timeout = max(1.0, phase_deadline - time.time())
        try:
            return await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            print(
                f"[{self.market.id}] phase deadline hit ({timeout:.1f}s) — "
                f"some agents did not respond in time",
                flush=True,
            )
            return [None] * len(coros)

    # ---------- context building ----------

    def _build_context(self, p: Participant, cycle_idx: int) -> AgentContext:
        pos = self._positions.get(p.account_id, 0)
        c = self._cash.get(p.account_id, 0.0)
        pnl = (c + pos * self._current_mark) * self.contract.multiplier
        return AgentContext(
            account_id=p.account_id,
            cycle_id=self._cycle.state.id if self._cycle else f"{self.contract.id}:{cycle_idx}",
            contract=self.contract,
            cycle_number=cycle_idx,
            total_cycles=self.config.num_cycles,
            position=pos,
            pnl=pnl,
            trade_history=_format_trade_history(self._all_fills, p.account_id),
            information_log=_format_info_log(self._info_log),
            settlement_date=self.config.settlement_date,
            position_limit=self.contract.position_limit,
            max_size=self.config.max_size,
        )

    # ---------- restart-safe state restore ----------

    def _restore_state(self) -> None:
        """Rebuild in-memory caches from the DB.

        Called once at construction. Reads positions and cash from stored
        fills, re-emits the info log up to the current cycle, and re-loads
        the active cycle if we crashed/restarted between MM and HF phases.
        """
        self._positions = positions_for_contract(self.db, self.contract.id)
        # Backfill default 0 for participants who haven't traded yet
        for p in self.participants:
            self._positions.setdefault(p.account_id, 0)
            self._cash.setdefault(p.account_id, 0.0)

        self._all_fills = list_fills_by_contract(self.db, self.contract.id)
        _apply_cash(self._cash, self._all_fills)

        # Re-emit info for cycles whose info has already been revealed.
        # If pending_mm == 1 and current_cycle == K, info for 0..K-1 is in.
        # If pending_mm == 0 and current_cycle == K, info for 0..K is in.
        revealed_through = (
            self.market.current_cycle
            if self.market.pending_mm == 0
            else self.market.current_cycle - 1
        )
        for i in range(revealed_through + 1):
            for line in self.config.info_schedule.get(i, []):
                self._info_log.append(f"(cycle {i}) {line}")

        # Most recent mark from cycle_states (carry forward)
        cycle_states = list_cycle_states(self.db, self.contract.id)
        for cs in cycle_states:
            if cs.hf_mark and cs.hf_mark > 0:
                self._current_mark = cs.hf_mark
            elif cs.mm_mark and cs.mm_mark > 0:
                self._current_mark = cs.mm_mark

        # If we resumed mid-cycle (pending_mm == 0), re-load the active Cycle
        # from the db so the next step (HF phase) has the residual book.
        if self.market.pending_mm == 0 and self.market.current_cycle < self.config.num_cycles:
            cycle_id = f"{self.contract.id}:{self.market.current_cycle}"
            try:
                self._cycle = load_cycle(self.db, self.contract, cycle_id)
                self._positions.update(self._cycle.positions)
            except ValueError:
                # No matching row — fall back to MM phase next.
                self.market.pending_mm = 1


# ---------- helpers ----------

def _apply_cash(cash: Dict[str, float], fills: List[Fill]) -> None:
    for f in fills:
        notional = f.price * f.size
        cash[f.buyer_account_id] = cash.get(f.buyer_account_id, 0.0) - notional
        cash[f.seller_account_id] = cash.get(f.seller_account_id, 0.0) + notional


def _format_trade_history(fills: List[Fill], account_id: str, limit: int = 20) -> str:
    relevant = [
        f for f in fills
        if f.buyer_account_id == account_id or f.seller_account_id == account_id
    ]
    if not relevant:
        return "(no trades yet)"
    lines: List[str] = []
    for f in reversed(relevant[-limit:]):
        if f.buyer_account_id == account_id:
            side, other = "BUY", f.seller_account_id
        else:
            side, other = "SELL", f.buyer_account_id
        tag = " [SELF-CROSS]" if f.buyer_account_id == f.seller_account_id else ""
        lines.append(
            f"  {f.cycle_id} {f.phase} {side} {f.size}@{f.price} (vs {other}){tag}"
        )
    return "\n".join(lines)


def _format_info_log(info: List[str]) -> str:
    if not info:
        return "(no information yet)"
    return "\n".join(f"- {item}" for item in info)


# ---------- factory ----------

def build_runner(
    config: MarketConfig,
    db: sqlite3.Connection,
    agent_specs: List[AgentSpec],
    agent_factory,
) -> MarketRunner:
    """Build a MarketRunner from config: persists market+contract+accounts,
    instantiates one agent per (market, agent_spec) pair, and constructs the
    runner. `agent_factory(spec)` returns a fresh `Agent` instance.

    If a market with the same id already exists in the DB its persisted
    runtime state (current_cycle, pending_mm, etc.) is preserved.
    """
    # Create or load the Market row
    from .db import get_market
    existing = get_market(db, config.id)
    if existing is not None:
        market = existing
        # Update mutable config fields in case markets.yaml changed
        market.name = config.name
        market.description = config.description
        market.multiplier = config.multiplier
        market.position_limit = config.position_limit
        market.num_cycles = config.num_cycles
        market.max_size = config.max_size
        market.settlement_date = config.settlement_date
    else:
        market = Market(
            id=config.id,
            name=config.name,
            description=config.description,
            multiplier=config.multiplier,
            position_limit=config.position_limit,
            num_cycles=config.num_cycles,
            max_size=config.max_size,
            settlement_date=config.settlement_date,
            state="RUNNING",
            current_cycle=0,
            pending_mm=1,
            created_at=time.time(),
        )
    upsert_market(db, market)

    # Create the matching Contract row (one per market, sharing the id)
    contract = Contract(
        id=config.id,
        name=config.name,
        description=config.description,
        multiplier=config.multiplier,
        position_limit=config.position_limit,
        created_at=market.created_at,
    )
    upsert_contract(db, contract)

    # Instantiate participants — fresh agent per market so trace logs and
    # any per-call state stay isolated across markets.
    participants: List[Participant] = []
    for spec in agent_specs:
        account_id = f"{config.id}:{spec.name}"
        upsert_account(db, Account(
            id=account_id,
            name=spec.name,
            role=spec.role,
            model=spec.model,
            market_id=config.id,
        ))
        participants.append(Participant(
            agent=agent_factory(spec),
            account_id=account_id,
            role=spec.role,
            spec=spec,
        ))

    return MarketRunner(
        config=config,
        db=db,
        participants=participants,
        market=market,
        contract=contract,
    )
