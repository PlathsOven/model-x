"""SQLite persistence tests. Run: python3 tests/test_db.py

Covers:
- Schema creation + roundtrips for every entity type
- UNIQUE (cycle_id, account_id) enforcement for quotes and orders
- Derived position aggregation (positions_for_contract, positions_before_cycle)
- End-to-end persistence through cycle.py: open/submit/close calls write to the db
- "Everything survives a restart": file-based db, write some state, close,
  reopen, load_cycle, continue the episode, verify final positions match
  an all-in-memory control run.
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.cycle import (
    close_hf_phase,
    close_mm_phase,
    load_cycle,
    open_cycle,
    submit_order,
    submit_quote,
)
from modelx.db import (
    connect,
    get_account,
    get_contract,
    get_cycle_state,
    insert_fill,
    insert_order,
    insert_quote,
    list_accounts,
    list_cycle_states,
    list_fills_by_contract,
    list_fills_by_cycle,
    list_orders_by_cycle,
    list_quotes_by_cycle,
    positions_before_cycle,
    positions_for_contract,
    upsert_account,
    upsert_contract,
    upsert_cycle_state,
)
from modelx.models import Account, Contract, CycleState, Fill, Order, Quote


def _contract(**overrides) -> Contract:
    base = dict(
        id="cpi",
        name="CPI Mar 2026",
        description="MoM CPI print",
        multiplier=1.0,
        position_limit=100,
    )
    base.update(overrides)
    return Contract(**base)


# ---------- schema + basic roundtrips ----------

def test_connect_creates_schema_idempotent():
    db = connect(":memory:")
    # Re-running the schema on the same connection should not error.
    db.executescript(
        "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY);"
    )  # no-op, just verifies the table exists
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    assert {"accounts", "contracts", "cycle_states", "quotes", "orders", "fills"} <= names


def test_account_roundtrip():
    db = connect(":memory:")
    a = Account(id="mm-A", name="Alice", role="MM", model="anthropic/claude", points=42.5)
    upsert_account(db, a)
    got = get_account(db, "mm-A")
    assert got == a

    # Upsert overwrites.
    a2 = Account(id="mm-A", name="Alice2", role="MM", model="anthropic/claude", points=99.0)
    upsert_account(db, a2)
    assert get_account(db, "mm-A").points == 99.0

    # Second account appears in listing.
    upsert_account(db, Account(id="mm-B", name="Bob", role="MM", model="gpt", points=0))
    accts = list_accounts(db)
    assert len(accts) == 2
    assert {a.id for a in accts} == {"mm-A", "mm-B"}


def test_contract_roundtrip_with_nulls():
    db = connect(":memory:")
    c = _contract()
    upsert_contract(db, c)
    got = get_contract(db, c.id)
    assert got.settlement_value is None
    assert got.settled_at is None
    # Now mark it settled and upsert again.
    c.settlement_value = 103.5
    c.settled_at = 1712000000.0
    upsert_contract(db, c)
    got2 = get_contract(db, c.id)
    assert got2.settlement_value == 103.5
    assert got2.settled_at == 1712000000.0


def test_cycle_state_roundtrip():
    db = connect(":memory:")
    upsert_contract(db, _contract())
    s = CycleState(id="cpi:0", contract_id="cpi", cycle_index=0, phase="MM_OPEN")
    upsert_cycle_state(db, s)
    got = get_cycle_state(db, "cpi:0")
    assert got.phase == "MM_OPEN"
    assert got.mm_mark is None

    # Advance phase + add a mark.
    s.phase = "HF_OPEN"
    s.mm_mark = 102.5
    upsert_cycle_state(db, s)
    assert get_cycle_state(db, "cpi:0").phase == "HF_OPEN"
    assert get_cycle_state(db, "cpi:0").mm_mark == 102.5


def test_quote_unique_account_per_cycle():
    db = connect(":memory:")
    upsert_contract(db, _contract())
    upsert_cycle_state(
        db, CycleState(id="cpi:0", contract_id="cpi", cycle_index=0, phase="MM_OPEN")
    )
    q1 = Quote("q1", "cpi:0", "cpi", "mm-A", 100, 5, 105, 5)
    insert_quote(db, q1)
    q2 = Quote("q2", "cpi:0", "cpi", "mm-A", 99, 5, 106, 5)
    try:
        insert_quote(db, q2)
    except sqlite3.IntegrityError:
        return
    assert False, "expected IntegrityError on duplicate (cycle_id, account_id)"


def test_order_unique_account_per_cycle():
    db = connect(":memory:")
    upsert_contract(db, _contract())
    upsert_cycle_state(
        db, CycleState(id="cpi:0", contract_id="cpi", cycle_index=0, phase="HF_OPEN")
    )
    insert_order(db, Order("o1", "cpi:0", "cpi", "hf-X", "buy", 5))
    try:
        insert_order(db, Order("o2", "cpi:0", "cpi", "hf-X", "buy", 3))
    except sqlite3.IntegrityError:
        return
    assert False, "expected IntegrityError on duplicate (cycle_id, account_id)"


def test_fill_list_by_cycle_and_contract_ordering():
    db = connect(":memory:")
    upsert_contract(db, _contract())
    # Insert cycles out of order to prove cycle_index is the sort key.
    upsert_cycle_state(db, CycleState("cpi:1", "cpi", 1, "HF_CLOSED"))
    upsert_cycle_state(db, CycleState("cpi:0", "cpi", 0, "HF_CLOSED"))
    insert_fill(db, Fill("f1", "cpi:1", "cpi", "mm-A", "mm-B", 100, 5, "MM"))
    insert_fill(db, Fill("f0", "cpi:0", "cpi", "mm-A", "mm-B", 99, 3, "MM"))

    c0 = list_fills_by_cycle(db, "cpi:0")
    c1 = list_fills_by_cycle(db, "cpi:1")
    assert [f.id for f in c0] == ["f0"]
    assert [f.id for f in c1] == ["f1"]

    all_fills = list_fills_by_contract(db, "cpi")
    # cpi:0 first because cycle_index=0 < 1.
    assert [f.id for f in all_fills] == ["f0", "f1"]


def test_positions_for_contract_aggregates_fills():
    db = connect(":memory:")
    upsert_contract(db, _contract())
    upsert_cycle_state(db, CycleState("cpi:0", "cpi", 0, "HF_CLOSED"))
    insert_fill(db, Fill("f1", "cpi:0", "cpi", "hf-X", "mm-A", 100, 5, "HF"))
    insert_fill(db, Fill("f2", "cpi:0", "cpi", "hf-X", "mm-A", 101, 3, "HF"))
    insert_fill(db, Fill("f3", "cpi:0", "cpi", "mm-A", "hf-Y", 102, 2, "HF"))

    pos = positions_for_contract(db, "cpi")
    assert pos["hf-X"] == 8   # bought 5+3
    assert pos["mm-A"] == -8 + 2  # sold 5+3, bought 2 = -6
    assert pos["hf-Y"] == -2


def test_positions_before_cycle_boundary():
    db = connect(":memory:")
    upsert_contract(db, _contract())
    upsert_cycle_state(db, CycleState("cpi:0", "cpi", 0, "HF_CLOSED"))
    upsert_cycle_state(db, CycleState("cpi:1", "cpi", 1, "HF_CLOSED"))
    upsert_cycle_state(db, CycleState("cpi:2", "cpi", 2, "MM_OPEN"))
    insert_fill(db, Fill("f0", "cpi:0", "cpi", "hf-X", "mm-A", 100, 5, "HF"))
    insert_fill(db, Fill("f1", "cpi:1", "cpi", "hf-X", "mm-A", 101, 3, "HF"))

    pos0 = positions_before_cycle(db, "cpi", 0)
    assert pos0 == {}
    pos1 = positions_before_cycle(db, "cpi", 1)
    assert pos1 == {"hf-X": 5, "mm-A": -5}
    pos2 = positions_before_cycle(db, "cpi", 2)
    assert pos2 == {"hf-X": 8, "mm-A": -8}


# ---------- cycle.py writes through to db ----------

def test_cycle_flow_persists_every_entity():
    db = connect(":memory:")
    c = _contract()

    cycle = open_cycle(c, cycle_index=0, positions={"mm-A": 0, "hf-X": 0}, db=db)
    # Contract + cycle_state row exist already.
    assert get_contract(db, "cpi") is not None
    assert get_cycle_state(db, "cpi:0").phase == "MM_OPEN"

    submit_quote(cycle, Quote("qA", cycle.state.id, "cpi", "mm-A", 100, 5, 105, 5))
    assert len(list_quotes_by_cycle(db, "cpi:0")) == 1

    close_mm_phase(cycle)
    assert get_cycle_state(db, "cpi:0").phase == "HF_OPEN"
    assert get_cycle_state(db, "cpi:0").mm_mark is not None

    submit_order(cycle, Order("oX", cycle.state.id, "cpi", "hf-X", "buy", 3))
    assert len(list_orders_by_cycle(db, "cpi:0")) == 1

    close_hf_phase(cycle)
    assert get_cycle_state(db, "cpi:0").phase == "HF_CLOSED"
    # 0 MM fills (only one MM quoted) + 1 HF fill.
    fills = list_fills_by_cycle(db, "cpi:0")
    assert len(fills) == 1
    assert fills[0].buyer_account_id == "hf-X"
    # Derived positions match cycle.positions.
    derived = positions_for_contract(db, "cpi")
    assert derived == cycle.positions


def test_load_cycle_reconstructs_hf_open_state():
    """Partial cycle (closed MM but not HF) is reloaded with the residual book re-derived."""
    db = connect(":memory:")
    c = _contract()

    cycle = open_cycle(c, cycle_index=0, positions={"mm-A": 0, "mm-B": 0}, db=db)
    submit_quote(cycle, Quote("qA", cycle.state.id, "cpi", "mm-A", 100, 10, 105, 10))
    submit_quote(cycle, Quote("qB", cycle.state.id, "cpi", "mm-B", 106, 5, 110, 5))
    close_mm_phase(cycle)

    # Now "restart" — throw away the in-memory cycle, keep only the db.
    reloaded = load_cycle(db, c, cycle.state.id)
    assert reloaded.state.phase == "HF_OPEN"
    assert len(reloaded.quotes) == 2
    assert len(reloaded.fills) == 1  # the MM-B lifts MM-A cross
    # Residual book is re-derived from the stored quotes.
    assert len(reloaded.residual_book) > 0
    # Positions carry forward from the stored fills.
    assert reloaded.positions["mm-A"] == -5
    assert reloaded.positions["mm-B"] == 5


def test_load_cycle_reconstructs_hf_closed_state():
    db = connect(":memory:")
    c = _contract()

    cycle = open_cycle(c, 0, positions={"mm-A": 0, "hf-X": 0}, db=db)
    submit_quote(cycle, Quote("qA", cycle.state.id, "cpi", "mm-A", 100, 5, 105, 5))
    close_mm_phase(cycle)
    submit_order(cycle, Order("oX", cycle.state.id, "cpi", "hf-X", "buy", 2))
    close_hf_phase(cycle)

    reloaded = load_cycle(db, c, cycle.state.id)
    assert reloaded.state.phase == "HF_CLOSED"
    assert len(reloaded.orders) == 1
    assert len(reloaded.fills) == 1
    assert reloaded.positions == cycle.positions


# ---------- restart test ----------

def test_full_restart_file_db():
    """Run cycle 0 in process A, close the file, reopen in process B, run cycle 1."""
    tmpdir = tempfile.mkdtemp(prefix="modelx-test-")
    path = os.path.join(tmpdir, "modelx.db")
    c = _contract()

    try:
        # --- "Process A" ---
        db_a = connect(path)
        cycle_a = open_cycle(c, 0, positions={"mm-A": 0, "hf-X": 0}, db=db_a)
        submit_quote(
            cycle_a, Quote("qA0", cycle_a.state.id, "cpi", "mm-A", 100, 5, 105, 5)
        )
        close_mm_phase(cycle_a)
        submit_order(
            cycle_a, Order("oX0", cycle_a.state.id, "cpi", "hf-X", "buy", 3)
        )
        close_hf_phase(cycle_a)
        positions_after_a = dict(cycle_a.positions)
        db_a.close()

        # --- "Process B" ---
        db_b = connect(path)
        # Contract and cycle 0 survived.
        assert get_contract(db_b, "cpi") is not None
        assert get_cycle_state(db_b, "cpi:0").phase == "HF_CLOSED"
        # Positions are recoverable.
        recovered = positions_for_contract(db_b, "cpi")
        assert recovered == positions_after_a

        # Kick off cycle 1 seeded from the aggregated positions (default path).
        cycle_b = open_cycle(c, 1, db=db_b)
        assert cycle_b.positions == positions_after_a
        submit_quote(
            cycle_b, Quote("qA1", cycle_b.state.id, "cpi", "mm-A", 100, 5, 105, 5)
        )
        close_mm_phase(cycle_b)
        submit_order(
            cycle_b, Order("oX1", cycle_b.state.id, "cpi", "hf-X", "buy", 3)
        )
        close_hf_phase(cycle_b)
        final_positions = dict(cycle_b.positions)
        db_b.close()

        # --- "Process C" — cold read of final state ---
        db_c = connect(path)
        cold_read = positions_for_contract(db_c, "cpi")
        assert cold_read == final_positions
        # Both cycle states are persisted.
        states = list_cycle_states(db_c, "cpi")
        assert [s.cycle_index for s in states] == [0, 1]
        assert all(s.phase == "HF_CLOSED" for s in states)
        # Cross-check: 2 cycles * 1 HF fill each = 2 fills on the contract.
        fills = list_fills_by_contract(db_c, "cpi")
        assert len(fills) == 2
        assert all(f.buyer_account_id == "hf-X" for f in fills)
        db_c.close()
    finally:
        if os.path.exists(path):
            os.remove(path)
        os.rmdir(tmpdir)


def test_restart_matches_single_process():
    """Two-cycle episode run as one process vs. split across a restart produces the same end state."""
    tmpdir = tempfile.mkdtemp(prefix="modelx-test-")
    path = os.path.join(tmpdir, "modelx.db")
    c = _contract()

    def run_cycle(cycle, mm_a_q, hf_x_sz):
        submit_quote(cycle, Quote(mm_a_q, cycle.state.id, "cpi", "mm-A", 100, 5, 105, 5))
        close_mm_phase(cycle)
        submit_order(cycle, Order(
            f"oX:{cycle.state.cycle_index}",
            cycle.state.id,
            "cpi",
            "hf-X",
            "buy",
            hf_x_sz,
        ))
        close_hf_phase(cycle)

    try:
        # Single-process control run.
        ctrl = connect(":memory:")
        c0 = open_cycle(c, 0, db=ctrl)
        run_cycle(c0, "qa0", 2)
        c1 = open_cycle(c, 1, db=ctrl)
        run_cycle(c1, "qa1", 4)
        control_positions = positions_for_contract(ctrl, "cpi")

        # Split-process run through a real file.
        db_a = connect(path)
        cycle_a = open_cycle(c, 0, db=db_a)
        run_cycle(cycle_a, "qa0", 2)
        db_a.close()

        db_b = connect(path)
        cycle_b = open_cycle(c, 1, db=db_b)
        run_cycle(cycle_b, "qa1", 4)
        split_positions = positions_for_contract(db_b, "cpi")
        db_b.close()

        assert split_positions == control_positions
    finally:
        if os.path.exists(path):
            os.remove(path)
        os.rmdir(tmpdir)


TESTS = [
    test_connect_creates_schema_idempotent,
    test_account_roundtrip,
    test_contract_roundtrip_with_nulls,
    test_cycle_state_roundtrip,
    test_quote_unique_account_per_cycle,
    test_order_unique_account_per_cycle,
    test_fill_list_by_cycle_and_contract_ordering,
    test_positions_for_contract_aggregates_fills,
    test_positions_before_cycle_boundary,
    test_cycle_flow_persists_every_entity,
    test_load_cycle_reconstructs_hf_open_state,
    test_load_cycle_reconstructs_hf_closed_state,
    test_full_restart_file_db,
    test_restart_matches_single_process,
]


if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print()
    if failures:
        print(f"{failures} of {len(TESTS)} tests failed")
        sys.exit(1)
    print(f"All {len(TESTS)} tests passed")
