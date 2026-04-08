"""Episode runner: wires agents into the cycle orchestrator and drives N cycles.

Each phase transition waits on `input_fn` (default: builtin input, so pressing
Enter advances). After all cycles run, prompts for the settlement value and
prints a final positions / cash / P&L table.
"""

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .agents.base import Agent, AgentContext, format_book
from .cycle import Cycle, close_hf_phase, close_mm_phase, open_cycle, submit_order, submit_quote
from .db import connect as db_connect, upsert_contract
from .models import Contract, Fill


@dataclass
class Participant:
    agent: Agent
    account_id: str
    role: str  # "MM" or "HF"


@dataclass
class EpisodeResult:
    positions: Dict[str, int]
    cash: Dict[str, float]
    pnl: Dict[str, float]
    all_fills: List[Fill]
    settlement_value: float
    cycles: List[Cycle] = field(default_factory=list)


def run_episode(
    contract: Contract,
    info_schedule: Dict[int, List[str]],
    participants: List[Participant],
    num_cycles: int,
    settlement_value: Optional[float] = None,
    settlement_date: str = "TBD",
    max_size: int = 50,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    db: Optional[sqlite3.Connection] = None,
) -> EpisodeResult:
    """Run the full contract lifecycle: N cycles then settlement.

    The operator advances each phase by pressing Enter. If `settlement_value`
    is provided, the settlement prompt is skipped (useful for tests and
    scripted runs). Pass `db` to persist everything to a file; omitting it
    creates an ephemeral in-memory SQLite that lives only for this call.
    """
    if db is None:
        db = db_connect(":memory:")

    positions: Dict[str, int] = {p.account_id: 0 for p in participants}
    cash: Dict[str, float] = {p.account_id: 0.0 for p in participants}
    info_log: List[str] = []
    cycles: List[Cycle] = []
    all_fills: List[Fill] = []

    mms = [p for p in participants if p.role == "MM"]
    hfs = [p for p in participants if p.role == "HF"]
    current_mark = 0.0

    for cycle_idx in range(num_cycles):
        # Reveal any new info scheduled for this cycle.
        new_info = info_schedule.get(cycle_idx, [])
        if new_info:
            print_fn("")
            print_fn(f"--- Information revealed before cycle {cycle_idx} ---")
            for item in new_info:
                info_log.append(f"(cycle {cycle_idx}) {item}")
                print_fn(f"  {item}")

        input_fn(f"\n[Enter] open MM phase of cycle {cycle_idx}... ")
        cycle = open_cycle(contract, cycle_idx, positions=positions, db=db)
        cycles.append(cycle)

        print_fn(f"\n=== Cycle {cycle_idx} MM phase ===")
        for p in mms:
            ctx = _build_context(
                p, contract, cycle, cycle_idx, num_cycles,
                positions, cash, current_mark, info_log, all_fills,
                settlement_date, max_size,
            )
            try:
                quote = p.agent.get_quote(ctx)
            except Exception as e:
                print_fn(f"  {p.account_id}: agent error: {type(e).__name__}: {e}")
                continue
            if quote is None:
                print_fn(f"  {p.account_id}: skipped quote")
                continue
            submit_quote(cycle, quote)
            print_fn(
                f"  {p.account_id}: bid {quote.bid_size}@{quote.bid_price} / "
                f"ask {quote.ask_size}@{quote.ask_price}"
            )

        input_fn(f"\n[Enter] close MM phase of cycle {cycle_idx}... ")
        mm_fills, residual_book, mm_mark = close_mm_phase(cycle)
        all_fills.extend(mm_fills)
        _apply_cash(cash, mm_fills)
        if mm_mark > 0:
            current_mark = mm_mark
        print_fn(f"\n  MM fills: {len(mm_fills)}")
        for f in mm_fills:
            tag = " [self]" if f.buyer_account_id == f.seller_account_id else ""
            print_fn(
                f"    {f.buyer_account_id} buys {f.size}@{f.price} "
                f"from {f.seller_account_id}{tag}"
            )
        print_fn(f"  MM mark: {mm_mark:.4f}")
        print_fn(f"  Residual book ({len(residual_book)} levels):")
        if residual_book:
            for line in format_book(residual_book).split("\n"):
                print_fn(f"    {line}")
        else:
            print_fn("    (empty)")

        input_fn(f"\n[Enter] open HF phase of cycle {cycle_idx}... ")
        print_fn(f"\n=== Cycle {cycle_idx} HF phase ===")
        # Refresh positions snapshot so HF contexts reflect post-MM state.
        positions.update(cycle.positions)
        for p in hfs:
            ctx = _build_context(
                p, contract, cycle, cycle_idx, num_cycles,
                positions, cash, current_mark, info_log, all_fills,
                settlement_date, max_size,
            )
            try:
                order = p.agent.get_order(ctx, residual_book)
            except Exception as e:
                print_fn(f"  {p.account_id}: agent error: {type(e).__name__}: {e}")
                continue
            if order is None:
                print_fn(f"  {p.account_id}: pass")
                continue
            submit_order(cycle, order)
            print_fn(f"  {p.account_id}: {order.side} {order.size}")

        input_fn(f"\n[Enter] close HF phase of cycle {cycle_idx}... ")
        hf_fills, hf_mark = close_hf_phase(cycle)
        all_fills.extend(hf_fills)
        _apply_cash(cash, hf_fills)
        if hf_mark > 0:
            current_mark = hf_mark
        print_fn(f"\n  HF fills: {len(hf_fills)}")
        for f in hf_fills:
            print_fn(
                f"    {f.buyer_account_id} buys {f.size}@{f.price} "
                f"from {f.seller_account_id}"
            )
        print_fn(f"  HF mark: {hf_mark:.4f}")

        # Pull the cycle's final positions back into the runner's view.
        positions.update(cycle.positions)

        print_fn(f"\n  End of cycle {cycle_idx} — positions:")
        for acct in sorted(positions.keys()):
            print_fn(f"    {acct}: {positions[acct]}")

    # ---- settlement ----
    print_fn("")
    print_fn("=" * 60)
    print_fn("All cycles complete. Time to settle.")
    if settlement_value is None:
        while True:
            raw = input_fn("Settlement value: ").strip()
            try:
                settlement_value = float(raw)
                break
            except ValueError:
                print_fn(f"  invalid float: {raw!r}")

    # Mark the contract settled and persist so downstream scoring works.
    contract.settlement_value = float(settlement_value)
    contract.settled_at = time.time()
    upsert_contract(db, contract)

    pnl = _compute_pnl(cash, positions, settlement_value, contract.multiplier)

    print_fn("")
    print_fn("=" * 60)
    print_fn(
        f"Settlement: {settlement_value}  Multiplier: {contract.multiplier}"
    )
    print_fn("")
    header = f"{'Account':<20} {'Role':<6} {'Position':>10} {'Cash':>14} {'P&L':>14}"
    print_fn(header)
    print_fn("-" * len(header))
    role_by_acct = {p.account_id: p.role for p in participants}
    for acct in sorted(positions.keys()):
        role = role_by_acct.get(acct, "?")
        pos = positions[acct]
        c = cash.get(acct, 0.0)
        v = pnl.get(acct, 0.0)
        print_fn(f"{acct:<20} {role:<6} {pos:>10} {c:>14.4f} {v:>14.4f}")

    return EpisodeResult(
        positions=dict(positions),
        cash=dict(cash),
        pnl=pnl,
        all_fills=list(all_fills),
        settlement_value=float(settlement_value),
        cycles=cycles,
    )


# ---------- helpers ----------

def _build_context(
    p: Participant,
    contract: Contract,
    cycle: Cycle,
    cycle_idx: int,
    num_cycles: int,
    positions: Dict[str, int],
    cash: Dict[str, float],
    current_mark: float,
    info_log: List[str],
    all_fills: List[Fill],
    settlement_date: str,
    max_size: int,
) -> AgentContext:
    pos = positions.get(p.account_id, 0)
    c = cash.get(p.account_id, 0.0)
    # Running mark-to-market P&L: cash received + mark value of open position.
    pnl = (c + pos * current_mark) * contract.multiplier
    return AgentContext(
        account_id=p.account_id,
        cycle_id=cycle.state.id,
        contract=contract,
        cycle_number=cycle_idx,
        total_cycles=num_cycles,
        position=pos,
        pnl=pnl,
        trade_history=_format_trade_history(all_fills, p.account_id),
        information_log=_format_info_log(info_log),
        settlement_date=settlement_date,
        position_limit=contract.position_limit,
        max_size=max_size,
    )


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


def _apply_cash(cash: Dict[str, float], fills: List[Fill]) -> None:
    for f in fills:
        notional = f.price * f.size
        cash[f.buyer_account_id] = cash.get(f.buyer_account_id, 0.0) - notional
        cash[f.seller_account_id] = cash.get(f.seller_account_id, 0.0) + notional


def _compute_pnl(
    cash: Dict[str, float],
    positions: Dict[str, int],
    settlement: float,
    multiplier: float,
) -> Dict[str, float]:
    pnl: Dict[str, float] = {}
    for acct in set(cash.keys()) | set(positions.keys()):
        c = cash.get(acct, 0.0)
        pos = positions.get(acct, 0)
        pnl[acct] = (c + pos * settlement) * multiplier
    return pnl
