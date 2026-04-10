"""Phase orchestrator: MM/HF phase transitions backed by SQLite.

Each wall-clock tick produces one phase (MM or HF), identified by its
timestamp. Phases alternate: MM -> HF -> MM -> HF -> ...

State machine per phase:
    open_phase()       -> OPEN
    submit_quote/order (any number)
    close_mm_phase()   OPEN -> CLOSED  (runs MM matching, for MM phases)
    close_hf_phase()   OPEN -> CLOSED  (runs HF matching, for HF phases)

Every state mutation is written through to a SQLite connection so a restart
can rebuild the in-memory view with `load_phase(db, contract, phase_id)`.
"""

import sqlite3
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .db import (
    connect,
    delete_phase_data,
    get_phase_state,
    insert_fill,
    insert_order,
    insert_quote,
    list_fills_by_phase,
    list_orders_by_phase,
    list_quotes_by_phase,
    positions_before_phase,
    upsert_contract,
    upsert_phase_state,
)
from .matching import BookLevel, match_hf_phase, match_mm_phase
from .models import Contract, Fill, Order, PhaseState, Quote


@dataclass
class Phase:
    contract: Contract
    state: PhaseState
    positions: Dict[str, int]                  # mutated as fills are applied
    quotes: List[Quote] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    residual_book: List[BookLevel] = field(default_factory=list)
    db: Optional[sqlite3.Connection] = None


def open_phase(
    contract: Contract,
    phase_type: str,
    tick_time: float,
    positions: Optional[Dict[str, int]] = None,
    db: Optional[sqlite3.Connection] = None,
) -> Phase:
    """Create a new phase in OPEN state.

    `phase_type` is "MM" or "HF".
    `tick_time` is the wall-clock epoch seconds of the tick that triggered
    this phase. The phase_id is derived from it: "{contract_id}:{int(tick_time)}".
    """
    if db is None:
        db = connect(":memory:")

    phase_id = f"{contract.id}:{int(tick_time)}"

    # Clean up any stale data from a previously interrupted run.
    delete_phase_data(db, phase_id)

    state = PhaseState(
        id=phase_id,
        contract_id=contract.id,
        phase_type=phase_type,
        phase="OPEN",
        created_at=tick_time,
    )

    upsert_contract(db, contract)
    upsert_phase_state(db, state)

    if positions is None:
        positions = positions_before_phase(db, contract.id, tick_time)

    return Phase(
        contract=contract,
        state=state,
        positions=dict(positions),
        db=db,
    )


def submit_quote(phase: Phase, quote: Quote) -> None:
    """Append an MM quote to the current phase and persist it."""
    if phase.state.phase != "OPEN":
        raise RuntimeError(
            f"submit_quote: phase is in {phase.state.phase}, expected OPEN"
        )
    if phase.state.phase_type != "MM":
        raise RuntimeError(
            f"submit_quote: phase_type is {phase.state.phase_type}, expected MM"
        )
    for q in phase.quotes:
        if q.account_id == quote.account_id:
            raise ValueError(
                f"submit_quote: account {quote.account_id} already submitted "
                f"a quote in phase {phase.state.id}"
            )
    phase.quotes.append(quote)
    insert_quote(phase.db, quote)


def close_mm_phase(phase: Phase) -> Tuple[List[Fill], List[BookLevel], float]:
    """Run MM matching, persist fills, advance to CLOSED.

    Returns (mm_fills, residual_book, mm_mark).
    """
    if phase.state.phase != "OPEN":
        raise RuntimeError(
            f"close_mm_phase: phase is in {phase.state.phase}, expected OPEN"
        )

    fills, book, mark = match_mm_phase(
        phase.quotes,
        cycle_id=phase.state.id,
        contract_id=phase.contract.id,
        positions=phase.positions,
    )
    for i, f in enumerate(fills):
        f.id = f"{phase.state.id}:{i}"

    phase.fills.extend(fills)
    phase.residual_book = book
    phase.state.mark = mark if mark > 0 else None
    phase.state.closed_at = _time.time()
    _apply_fills_to_positions(phase.positions, fills)
    phase.state.phase = "CLOSED"

    for f in fills:
        insert_fill(phase.db, f)
    upsert_phase_state(phase.db, phase.state)

    return fills, book, mark


def submit_order(phase: Phase, order: Order) -> None:
    """Append an HF market order and persist it."""
    if phase.state.phase != "OPEN":
        raise RuntimeError(
            f"submit_order: phase is in {phase.state.phase}, expected OPEN"
        )
    if phase.state.phase_type != "HF":
        raise RuntimeError(
            f"submit_order: phase_type is {phase.state.phase_type}, expected HF"
        )
    for o in phase.orders:
        if o.account_id == order.account_id:
            raise ValueError(
                f"submit_order: account {order.account_id} already submitted "
                f"an order in phase {phase.state.id}"
            )
    phase.orders.append(order)
    insert_order(phase.db, order)


def close_hf_phase(phase: Phase) -> Tuple[List[Fill], float]:
    """Run HF matching, persist fills, advance to CLOSED.

    Returns (hf_fills, hf_mark).
    """
    if phase.state.phase != "OPEN":
        raise RuntimeError(
            f"close_hf_phase: phase is in {phase.state.phase}, expected OPEN"
        )

    fills, mark = match_hf_phase(
        phase.residual_book,
        phase.orders,
        positions=phase.positions,
        cycle_id=phase.state.id,
        contract_id=phase.contract.id,
        position_limit=phase.contract.position_limit,
    )
    for i, f in enumerate(fills):
        f.id = f"{phase.state.id}:{i}"

    phase.fills.extend(fills)
    phase.state.mark = mark if mark > 0 else None
    phase.state.closed_at = _time.time()
    _apply_fills_to_positions(phase.positions, fills)
    phase.state.phase = "CLOSED"

    for f in fills:
        insert_fill(phase.db, f)
    upsert_phase_state(phase.db, phase.state)

    return fills, mark


def load_phase(
    db: sqlite3.Connection,
    contract: Contract,
    phase_id: str,
) -> Phase:
    """Reconstruct a Phase snapshot from the db.

    When the phase is an MM phase in CLOSED state (MM matching done,
    HF phase hasn't started yet), re-derives the residual book from stored
    quotes + entering positions.
    """
    state = get_phase_state(db, phase_id)
    if state is None:
        raise ValueError(f"load_phase: phase {phase_id!r} not found")
    if state.contract_id != contract.id:
        raise ValueError(
            f"load_phase: phase {phase_id!r} belongs to contract "
            f"{state.contract_id!r}, not {contract.id!r}"
        )

    quotes = list_quotes_by_phase(db, phase_id)
    orders = list_orders_by_phase(db, phase_id)
    fills = list_fills_by_phase(db, phase_id)

    # Positions = entering positions + fills applied so far in this phase.
    positions = positions_before_phase(db, contract.id, state.created_at)
    _apply_fills_to_positions(positions, fills)

    residual_book: List[BookLevel] = []
    if state.phase_type == "MM" and state.phase == "CLOSED":
        # Re-derive residual book from quotes + entering positions.
        entering = positions_before_phase(db, contract.id, state.created_at)
        _, residual_book, _ = match_mm_phase(
            quotes,
            cycle_id=state.id,
            contract_id=contract.id,
            positions=entering,
        )

    return Phase(
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
    """Mutate `positions` by applying each fill (buyer +size, seller -size)."""
    for f in fills:
        positions[f.buyer_account_id] = positions.get(f.buyer_account_id, 0) + f.size
        positions[f.seller_account_id] = positions.get(f.seller_account_id, 0) - f.size
