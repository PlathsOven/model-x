"""Async per-market state machine driven by the global supervisor.

A `MarketRunner` owns the in-memory state of one live market (positions,
cash, info log, the active `Phase` object if any). The supervisor calls
`step(phase_deadline, tick_time)` once per global wall-clock tick; each
call runs exactly one phase (MM or HF) end-to-end.

State is always recoverable from the DB so a supervisor restart resumes
mid-market without loss.
"""

import asyncio
import sqlite3
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .agents.base import Agent, AgentContext
from .config import AgentSpec, MarketConfig
from .phase import (
    Phase,
    close_hf_phase,
    close_mm_phase,
    load_phase,
    open_phase,
    submit_order,
    submit_quote,
)
from .db import (
    delete_future_data,
    delete_market_data,
    list_phase_states,
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


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


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
        # Holds the MM phase object between MM and HF steps so the HF
        # phase can read the residual book.
        self._mm_phase: Optional[Phase] = None

        # Live news: track phase timestamps so we can look back N phases
        # for headlines (configurable via news_lookback_phases).
        self._use_live_news = bool(config.search_terms)
        self._phase_timestamps: List[datetime] = [datetime.now(timezone.utc)]

        # Counter for static info_schedule lookups (keyed by MM phase #).
        self._mm_phase_count: int = 0

        self._restore_state()

    # ---------- public API ----------

    def is_active(self) -> bool:
        return self.market.state == "RUNNING"

    async def step(self, phase_deadline: float, tick_time: float) -> None:
        """Advance this market by one phase. Called by the supervisor."""
        if not self.is_active():
            return
        try:
            if self.market.pending_mm:
                await self._run_mm_phase(phase_deadline, tick_time)
                self.market.pending_mm = 0
            else:
                await self._run_hf_phase(phase_deadline, tick_time)
                self.market.pending_mm = 1
                # Time-based termination.
                if (
                    self.config.settlement_datetime is not None
                    and datetime.now(timezone.utc)
                    >= self.config.settlement_datetime.astimezone(timezone.utc)
                ):
                    self.market.state = "PENDING_SETTLEMENT"
                    print(
                        f"[{self.market.id}] settlement time reached — "
                        f"entered PENDING_SETTLEMENT",
                        flush=True,
                    )
            self.market.last_phase_ts = tick_time
            update_market_progress(
                self.db,
                self.market.id,
                self.market.state,
                self.market.pending_mm,
                self.market.last_phase_ts,
            )
        except Exception as e:
            print(
                f"[{self.market.id}] step failed: {type(e).__name__}: {e}",
                flush=True,
            )
            traceback.print_exc()

    # ---------- phase handlers ----------

    async def _run_mm_phase(self, phase_deadline: float, tick_time: float) -> None:
        phase_info_text: Optional[str] = None
        if self._use_live_news:
            from .news import NewsConfig, build_info_payload
            news_config = NewsConfig(
                sources=self.config.news_sources,
                max_headlines_per_cycle=self.config.max_headlines_per_cycle,
            )
            lookback = self.config.news_lookback_phases
            since_idx = max(0, len(self._phase_timestamps) - lookback)
            since = self._phase_timestamps[since_idx]
            payload = build_info_payload(self.contract, news_config, since)
            phase_info_text = payload
            self._info_log.append(f"(MM @ {_fmt_ts(tick_time)}) {payload}")
            self._phase_timestamps.append(datetime.now(timezone.utc))
            payload_lines = payload.split("\n")
            headline_lines = [l for l in payload_lines if l.startswith("[")]
            has_price = any("PRICE DATA" in l for l in payload_lines)
            parts = [f"{len(headline_lines)} headline(s)"]
            if has_price:
                parts.append("+ price data")
            print(
                f"[{self.market.id}] MM @ {_fmt_ts(tick_time)}: fetched live news: "
                f"{', '.join(parts)}",
                flush=True,
            )
            for hl in headline_lines[:3]:
                display = hl[:80] + ("..." if len(hl) > 80 else "")
                print(f"  {display}", flush=True)
            if len(headline_lines) > 3:
                print(f"  ... and {len(headline_lines) - 3} more", flush=True)
        else:
            new_info = self.config.info_schedule.get(self._mm_phase_count, [])
            if new_info:
                phase_info_text = "\n".join(new_info)
            for line in new_info:
                self._info_log.append(f"(MM @ {_fmt_ts(tick_time)}) {line}")
            if new_info:
                print(
                    f"[{self.market.id}] MM @ {_fmt_ts(tick_time)}: revealed "
                    f"{len(new_info)} info item(s)",
                    flush=True,
                )
        self._mm_phase_count += 1

        # Open the MM phase
        self._mm_phase = open_phase(
            self.contract,
            "MM",
            tick_time,
            positions=self._positions,
            db=self.db,
        )
        if phase_info_text:
            self._mm_phase.state.info_text = phase_info_text

        print(
            f"[{self.market.id}] MM @ {_fmt_ts(tick_time)}: "
            f"collecting quotes from {len(self._mms)} MMs",
            flush=True,
        )

        results = await self._gather_with_deadline(
            [self._safe_quote_call(p, tick_time) for p in self._mms],
            phase_deadline,
        )

        for p, result in zip(self._mms, results):
            if result is None:
                continue
            try:
                submit_quote(self._mm_phase, result)
            except Exception as e:
                print(
                    f"[{self.market.id}] {p.account_id} submit_quote failed: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

        mm_fills, residual_book, mm_mark = close_mm_phase(self._mm_phase)
        self._all_fills.extend(mm_fills)
        _apply_cash(self._cash, mm_fills)
        if mm_mark > 0:
            self._current_mark = mm_mark
        self._positions.update(self._mm_phase.positions)
        print(
            f"[{self.market.id}] MM @ {_fmt_ts(tick_time)} closed: "
            f"{len(mm_fills)} fills, mark={mm_mark:.4f}, "
            f"residual book={len(residual_book)} levels",
            flush=True,
        )

    async def _run_hf_phase(self, phase_deadline: float, tick_time: float) -> None:
        # Restart-safety: if we resumed mid-pair, _mm_phase was None.
        # Load the most recent MM phase to get its residual book.
        if self._mm_phase is None:
            phase_states = list_phase_states(self.db, self.contract.id)
            mm_phases = [ps for ps in phase_states if ps.phase_type == "MM"]
            if mm_phases:
                last_mm = mm_phases[-1]
                self._mm_phase = load_phase(self.db, self.contract, last_mm.id)
                self._positions.update(self._mm_phase.positions)
            else:
                print(
                    f"[{self.market.id}] no prior MM phase found — skipping HF",
                    flush=True,
                )
                self.market.pending_mm = 1
                return

        residual_book = self._mm_phase.residual_book

        # Open the HF phase
        hf_phase = open_phase(
            self.contract,
            "HF",
            tick_time,
            positions=self._positions,
            db=self.db,
        )
        # Transfer residual book from MM phase to HF phase.
        hf_phase.residual_book = residual_book

        print(
            f"[{self.market.id}] HF @ {_fmt_ts(tick_time)}: "
            f"collecting orders from {len(self._hfs)} HFs",
            flush=True,
        )

        results = await self._gather_with_deadline(
            [self._safe_order_call(p, tick_time, residual_book) for p in self._hfs],
            phase_deadline,
        )

        for p, result in zip(self._hfs, results):
            if result is None:
                continue
            try:
                submit_order(hf_phase, result)
            except Exception as e:
                print(
                    f"[{self.market.id}] {p.account_id} submit_order failed: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

        hf_fills, hf_mark = close_hf_phase(hf_phase)
        self._all_fills.extend(hf_fills)
        _apply_cash(self._cash, hf_fills)
        if hf_mark > 0:
            self._current_mark = hf_mark
        self._positions.update(hf_phase.positions)
        print(
            f"[{self.market.id}] HF @ {_fmt_ts(tick_time)} closed: "
            f"{len(hf_fills)} fills, mark={hf_mark:.4f}",
            flush=True,
        )
        # Pair done — clear so the next MM tick opens a fresh one.
        self._mm_phase = None

    # ---------- per-agent calls ----------

    async def _safe_quote_call(self, p: Participant, tick_time: float) -> Any:
        ctx = self._build_context(p, tick_time, "MM")
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
        self, p: Participant, tick_time: float, book: List[BookLevel],
    ) -> Any:
        ctx = self._build_context(p, tick_time, "HF")
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
        self, coros: List[Any], phase_deadline: float,
    ) -> List[Any]:
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

    def _build_context(
        self, p: Participant, tick_time: float, phase_type: str,
    ) -> AgentContext:
        pos = self._positions.get(p.account_id, 0)
        c = self._cash.get(p.account_id, 0.0)
        pnl = (c + pos * self._current_mark) * self.contract.multiplier
        phase_id = f"{self.contract.id}:{int(tick_time)}"
        return AgentContext(
            account_id=p.account_id,
            phase_id=phase_id,
            contract=self.contract,
            phase_type=phase_type,
            phase_timestamp=tick_time,
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
        """Rebuild in-memory caches from the DB."""
        self._positions = positions_for_contract(self.db, self.contract.id)
        for p in self.participants:
            self._positions.setdefault(p.account_id, 0)
            self._cash.setdefault(p.account_id, 0.0)

        self._all_fills = list_fills_by_contract(self.db, self.contract.id)
        _apply_cash(self._cash, self._all_fills)

        phase_states = list_phase_states(self.db, self.contract.id)
        for ps in phase_states:
            if ps.mark and ps.mark > 0:
                self._current_mark = ps.mark
            if ps.info_text:
                self._info_log.append(
                    f"({ps.phase_type} @ {_fmt_ts(ps.created_at)}) {ps.info_text}"
                )
            if ps.phase_type == "MM":
                self._mm_phase_count += 1

        # If we resumed between MM and HF (pending_mm == 0), load the
        # last MM phase so the HF step has the residual book.
        if self.market.pending_mm == 0:
            mm_phases = [ps for ps in phase_states if ps.phase_type == "MM"]
            if mm_phases:
                last_mm = mm_phases[-1]
                try:
                    self._mm_phase = load_phase(
                        self.db, self.contract, last_mm.id,
                    )
                    self._positions.update(self._mm_phase.positions)
                except ValueError:
                    self.market.pending_mm = 1
            else:
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
            f"  {f.phase_id} {f.phase} {side} {f.size}@{f.price} (vs {other}){tag}"
        )
    return "\n".join(lines)


def _format_info_log(info: List[str], limit: int = 10) -> str:
    if not info:
        return "(no information yet)"
    recent = info[-limit:]
    prefix = f"({len(info) - len(recent)} older entries omitted)\n" if len(info) > limit else ""
    return prefix + "\n".join(f"- {item}" for item in recent)


# ---------- factory ----------

def build_runner(
    config: MarketConfig,
    db: sqlite3.Connection,
    agent_specs: List[AgentSpec],
    agent_factory,
) -> MarketRunner:
    """Build a MarketRunner from config.

    Preserves historical data: only trims future/incomplete phases on restart.
    """
    from .db import get_market
    existing = get_market(db, config.id)
    if existing is not None:
        deleted = delete_future_data(db, config.id, time.time())
        if deleted:
            print(
                f"[{config.id}] trimmed {deleted} future rows from previous run",
                flush=True,
            )
        existing = get_market(db, config.id)

    market = Market(
        id=config.id,
        name=config.name,
        description=config.description,
        multiplier=config.multiplier,
        position_limit=config.position_limit,
        max_size=config.max_size,
        settlement_date=config.settlement_date,
        state="RUNNING",
        pending_mm=existing.pending_mm if existing else 1,
        last_phase_ts=existing.last_phase_ts if existing else 0.0,
        created_at=existing.created_at if existing else time.time(),
    )
    upsert_market(db, market)

    contract = Contract(
        id=config.id,
        name=config.name,
        description=config.description,
        multiplier=config.multiplier,
        position_limit=config.position_limit,
        created_at=market.created_at,
        search_terms=config.search_terms,
        price_ticker=config.price_ticker,
    )
    upsert_contract(db, contract)

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
