"""Cycle orchestrator: manual MM/HF phase transitions backed by SQLite.

State machine:
    open_cycle()       -> MM_OPEN
    submit_quote()     (any number of MMs, one Quote each)
    close_mm_phase()   MM_OPEN -> HF_OPEN  (runs MM matching)
    submit_order()     (any number of HFs, one Order each, or absent = pass)
    close_hf_phase()   HF_OPEN -> HF_CLOSED (runs HF matching)

Every state mutation is written through to a SQLite connection so a restart
can rebuild the in-memory view with `load_cycle(db, contract, cycle_id)`.
If a caller doesn't pass `db=` to `open_cycle`, an ephemeral in-memory
connection is created and stored on the Cycle — the persistence layer is
always exercised, even in one-shot scripts and tests.

There are no timers — the caller advances each phase explicitly.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .db import (
    connect,
    get_cycle_state,
    insert_fill,
    insert_order,
    insert_quote,
    list_fills_by_cycle,
    list_orders_by_cycle,
    list_quotes_by_cycle,
    positions_before_cycle,
    upsert_contract,
    upsert_cycle_state,
)
from .matching import BookLevel, match_hf_phase, match_mm_phase
from .models import Contract, CycleState, Fill, Order, Quote


@dataclass
class Cycle:
    contract: Contract
    state: CycleState
    positions: Dict[str, int]                  # mutated as fills are applied
    quotes: List[Quote] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    residual_book: List[BookLevel] = field(default_factory=list)
    db: Optional[sqlite3.Connection] = None    # always set after open_cycle / load_cycle


def open_cycle(
    contract: Contract,
    cycle_index: int,
    positions: Optional[Dict[str, int]] = None,
    cycle_id: Optional[str] = None,
    db: Optional[sqlite3.Connection] = None,
) -> Cycle:
    """Create a new cycle in MM_OPEN.

    If `db` is None, an ephemeral in-memory SQLite connection is created and
    stored on the Cycle. Either way, the contract and initial cycle_state
    row are persisted immediately. `positions` defaults to a copy of the
    aggregated fills-derived positions for the contract (empty on a fresh db).
    """
    if db is None:
        db = connect(":memory:")

    cid = cycle_id or f"{contract.id}:{cycle_index}"
    state = CycleState(
        id=cid,
        contract_id=contract.id,
        cycle_index=cycle_index,
        phase="MM_OPEN",
    )

    upsert_contract(db, contract)
    upsert_cycle_state(db, state)

    if positions is None:
        positions = positions_before_cycle(db, contract.id, cycle_index)

    return Cycle(
        contract=contract,
        state=state,
        positions=dict(positions),
        db=db,
    )


def submit_quote(cycle: Cycle, quote: Quote) -> None:
    """Append an MM quote to the current cycle and persist it.

    Each MM may submit at most one quote per cycle. Raises if the cycle is
    not in MM_OPEN, or if `quote.account_id` has already submitted. The
    UNIQUE (cycle_id, account_id) constraint in the db is a belt-and-braces
    second line of defense.
    """
    if cycle.state.phase != "MM_OPEN":
        raise RuntimeError(
            f"submit_quote: cycle is in {cycle.state.phase}, expected MM_OPEN"
        )
    for q in cycle.quotes:
        if q.account_id == quote.account_id:
            raise ValueError(
                f"submit_quote: account {quote.account_id} already submitted "
                f"a quote in cycle {cycle.state.id}"
            )
    cycle.quotes.append(quote)
    insert_quote(cycle.db, quote)


def close_mm_phase(cycle: Cycle) -> Tuple[List[Fill], List[BookLevel], float]:
    """Run MM matching, persist fills, advance to HF_OPEN.

    Returns (mm_fills, residual_book, mm_mark).
    """
    if cycle.state.phase != "MM_OPEN":
        raise RuntimeError(
            f"close_mm_phase: cycle is in {cycle.state.phase}, expected MM_OPEN"
        )

    fills, book, mark = match_mm_phase(
        cycle.quotes,
        cycle_id=cycle.state.id,
        contract_id=cycle.contract.id,
        positions=cycle.positions,
    )
    for i, f in enumerate(fills):
        f.id = f"{cycle.state.id}:mm:{i}"

    cycle.fills.extend(fills)
    cycle.residual_book = book
    cycle.state.mm_mark = mark
    _apply_fills_to_positions(cycle.positions, fills)
    cycle.state.phase = "HF_OPEN"

    for f in fills:
        insert_fill(cycle.db, f)
    upsert_cycle_state(cycle.db, cycle.state)

    return fills, book, mark


def submit_order(cycle: Cycle, order: Order) -> None:
    """Append an HF market order and persist it.

    Each HF may submit at most one order per cycle; "pass" means simply not
    calling this function for that account. Raises if the cycle is not in
    HF_OPEN, or if `order.account_id` has already submitted.
    """
    if cycle.state.phase != "HF_OPEN":
        raise RuntimeError(
            f"submit_order: cycle is in {cycle.state.phase}, expected HF_OPEN"
        )
    for o in cycle.orders:
        if o.account_id == order.account_id:
            raise ValueError(
                f"submit_order: account {order.account_id} already submitted "
                f"an order in cycle {cycle.state.id}"
            )
    cycle.orders.append(order)
    insert_order(cycle.db, order)


def close_hf_phase(cycle: Cycle) -> Tuple[List[Fill], float]:
    """Run HF matching, persist fills, advance to HF_CLOSED.

    Returns (hf_fills, hf_mark).
    """
    if cycle.state.phase != "HF_OPEN":
        raise RuntimeError(
            f"close_hf_phase: cycle is in {cycle.state.phase}, expected HF_OPEN"
        )

    fills, mark = match_hf_phase(
        cycle.residual_book,
        cycle.orders,
        positions=cycle.positions,
        cycle_id=cycle.state.id,
        contract_id=cycle.contract.id,
        position_limit=cycle.contract.position_limit,
    )
    for i, f in enumerate(fills):
        f.id = f"{cycle.state.id}:hf:{i}"

    cycle.fills.extend(fills)
    cycle.state.hf_mark = mark
    _apply_fills_to_positions(cycle.positions, fills)
    cycle.state.phase = "HF_CLOSED"

    for f in fills:
        insert_fill(cycle.db, f)
    upsert_cycle_state(cycle.db, cycle.state)

    return fills, mark


def load_cycle(
    db: sqlite3.Connection,
    contract: Contract,
    cycle_id: str,
) -> Cycle:
    """Reconstruct a Cycle snapshot from the db.

    Rebuilds `quotes`, `orders`, `fills`, and `positions` from stored rows.
    When the cycle is in HF_OPEN, re-runs `match_mm_phase` on the stored
    quotes (with the entering position snapshot) to re-derive the residual
    book that HF matching needs — residual books are not persisted because
    they're deterministically derivable from quotes + entering positions.
    For MM_OPEN and HF_CLOSED, `residual_book` is left empty.
    """
    state = get_cycle_state(db, cycle_id)
    if state is None:
        raise ValueError(f"load_cycle: cycle {cycle_id!r} not found")
    if state.contract_id != contract.id:
        raise ValueError(
            f"load_cycle: cycle {cycle_id!r} belongs to contract "
            f"{state.contract_id!r}, not {contract.id!r}"
        )

    quotes = list_quotes_by_cycle(db, cycle_id)
    orders = list_orders_by_cycle(db, cycle_id)
    fills = list_fills_by_cycle(db, cycle_id)

    # Positions = entering positions + fills applied so far in this cycle.
    positions = positions_before_cycle(db, contract.id, state.cycle_index)
    _apply_fills_to_positions(positions, fills)

    residual_book: List[BookLevel] = []
    if state.phase == "HF_OPEN":
        entering = positions_before_cycle(db, contract.id, state.cycle_index)
        _, residual_book, _ = match_mm_phase(
            quotes,
            cycle_id=state.id,
            contract_id=contract.id,
            positions=entering,
        )

    return Cycle(
        contract=contract,
        state=state,
        positions=positions,
        quotes=quotes,
        orders=orders,
        fills=fills,
        residual_book=residual_book,
        db=db,
    )


def _apply_fills_to_positions(positions: Dict[str, int], fills: List[Fill]) -> None:
    """Mutate `positions` by applying each fill (buyer +size, seller -size).

    Self-fills (buyer == seller) net to zero, which is correct.
    """
    for f in fills:
        positions[f.buyer_account_id] = positions.get(f.buyer_account_id, 0) + f.size
        positions[f.seller_account_id] = positions.get(f.seller_account_id, 0) - f.size
