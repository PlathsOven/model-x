"""Cycle orchestrator smoke tests. Run: python3 tests/test_cycle.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.cycle import (
    Cycle,
    close_hf_phase,
    close_mm_phase,
    open_cycle,
    submit_order,
    submit_quote,
)
from modelx.models import Contract, Order, Quote


def _contract(**overrides) -> Contract:
    base = dict(
        id="cpi-mar26",
        name="US CPI Mar 2026",
        description="MoM CPI print",
        multiplier=1.0,
        position_limit=100,
    )
    base.update(overrides)
    return Contract(**base)


# ---------- happy path ----------

def test_happy_path_full_cycle():
    """Open -> 2 MM quotes -> close MM -> 2 HF orders -> close HF."""
    c = _contract()
    cycle = open_cycle(c, cycle_index=0)
    assert cycle.state.phase == "MM_OPEN"
    assert cycle.state.id == "cpi-mar26:0"

    submit_quote(cycle, Quote("qa", cycle.state.id, c.id, "mm-A", 100, 10, 105, 10))
    submit_quote(cycle, Quote("qb", cycle.state.id, c.id, "mm-B", 106, 5, 110, 5))

    mm_fills, book, mm_mark = close_mm_phase(cycle)
    assert cycle.state.phase == "HF_OPEN"
    assert len(mm_fills) == 1
    f = mm_fills[0]
    assert f.id == "cpi-mar26:0:mm:0"
    assert f.buyer_account_id == "mm-B"
    assert f.seller_account_id == "mm-A"
    assert f.price == 105.5
    assert f.size == 5
    assert f.phase == "MM"
    # Positions reflect the cross.
    assert cycle.positions == {"mm-A": -5, "mm-B": 5}
    # Residual book is what HFs see.
    assert len(book) == 3
    assert cycle.state.mm_mark == mm_mark
    assert mm_mark > 0

    submit_order(cycle, Order("o1", cycle.state.id, c.id, "hf-X", "buy", 4))
    submit_order(cycle, Order("o2", cycle.state.id, c.id, "hf-Y", "sell", 3))

    hf_fills, hf_mark = close_hf_phase(cycle)
    assert cycle.state.phase == "HF_CLOSED"
    # X buys 4 from MM-A's residual ask 5@105 (best ask).
    # Y sells 3 against MM-B's residual bid? No — MM-B has no bid in residual.
    # MM-A has bid 100 size 10 (residual). Y sells 3 at 100.
    buyers_to_sellers = {(f.buyer_account_id, f.seller_account_id, f.size, f.price) for f in hf_fills}
    assert ("hf-X", "mm-A", 4, 105) in buyers_to_sellers
    assert ("mm-A", "hf-Y", 3, 100) in buyers_to_sellers
    for f in hf_fills:
        assert f.id.startswith("cpi-mar26:0:hf:")
        assert f.phase == "HF"
    assert cycle.state.hf_mark == hf_mark

    # Final positions: MM-A: -5 (MM) - 4 (X buys from A) + 3 (Y sells to A) = -6
    #                  MM-B: +5
    #                  hf-X: +4
    #                  hf-Y: -3
    assert cycle.positions == {"mm-A": -6, "mm-B": 5, "hf-X": 4, "hf-Y": -3}

    # cycle.fills accumulates both phases.
    assert len(cycle.fills) == 1 + len(hf_fills)


# ---------- phase enforcement ----------

def test_submit_quote_wrong_phase():
    c = _contract()
    cycle = open_cycle(c, 0)
    submit_quote(cycle, Quote("q1", cycle.state.id, c.id, "mm-A", 100, 5, 102, 5))
    close_mm_phase(cycle)
    try:
        submit_quote(cycle, Quote("q2", cycle.state.id, c.id, "mm-B", 99, 5, 103, 5))
    except RuntimeError as e:
        assert "HF_OPEN" in str(e), e
        return
    assert False, "expected RuntimeError"


def test_submit_order_wrong_phase():
    c = _contract()
    cycle = open_cycle(c, 0)
    try:
        submit_order(cycle, Order("o1", cycle.state.id, c.id, "hf-X", "buy", 5))
    except RuntimeError as e:
        assert "MM_OPEN" in str(e), e
        return
    assert False, "expected RuntimeError"


def test_close_mm_phase_wrong_phase():
    c = _contract()
    cycle = open_cycle(c, 0)
    close_mm_phase(cycle)  # MM_OPEN -> HF_OPEN
    try:
        close_mm_phase(cycle)
    except RuntimeError as e:
        assert "HF_OPEN" in str(e), e
        return
    assert False, "expected RuntimeError"


def test_close_hf_phase_wrong_phase():
    c = _contract()
    cycle = open_cycle(c, 0)
    try:
        close_hf_phase(cycle)
    except RuntimeError as e:
        assert "MM_OPEN" in str(e), e
        return
    assert False, "expected RuntimeError"


# ---------- one-per-account enforcement ----------

def test_duplicate_quote_rejected():
    c = _contract()
    cycle = open_cycle(c, 0)
    submit_quote(cycle, Quote("qa1", cycle.state.id, c.id, "mm-A", 100, 5, 102, 5))
    try:
        submit_quote(cycle, Quote("qa2", cycle.state.id, c.id, "mm-A", 99, 5, 103, 5))
    except ValueError as e:
        assert "mm-A" in str(e)
        return
    assert False, "expected ValueError on duplicate account quote"


def test_duplicate_order_rejected():
    c = _contract()
    cycle = open_cycle(c, 0)
    submit_quote(cycle, Quote("qa", cycle.state.id, c.id, "mm-A", 100, 5, 102, 5))
    close_mm_phase(cycle)
    submit_order(cycle, Order("o1", cycle.state.id, c.id, "hf-X", "buy", 2))
    try:
        submit_order(cycle, Order("o2", cycle.state.id, c.id, "hf-X", "buy", 1))
    except ValueError as e:
        assert "hf-X" in str(e)
        return
    assert False, "expected ValueError on duplicate account order"


# ---------- carryover ----------

def test_positions_seed_from_prior_cycle():
    """A new cycle takes a positions snapshot and matching uses it for caps."""
    c = _contract(position_limit=10)
    # hf-X enters at +9 -> can only buy 1 more before hitting +10 cap.
    cycle = open_cycle(c, cycle_index=0, positions={"hf-X": 9})
    submit_quote(cycle, Quote("qa", cycle.state.id, c.id, "mm-A", 95, 0, 100, 5))
    close_mm_phase(cycle)
    submit_order(cycle, Order("o1", cycle.state.id, c.id, "hf-X", "buy", 5))
    fills, _ = close_hf_phase(cycle)
    bought = sum(f.size for f in fills if f.buyer_account_id == "hf-X")
    assert bought == 1, bought
    assert cycle.positions["hf-X"] == 10


def test_pass_means_no_order():
    """An HF that passes simply does not call submit_order."""
    c = _contract()
    cycle = open_cycle(c, 0)
    submit_quote(cycle, Quote("qa", cycle.state.id, c.id, "mm-A", 100, 5, 102, 5))
    submit_quote(cycle, Quote("qb", cycle.state.id, c.id, "mm-B", 99, 5, 103, 5))
    close_mm_phase(cycle)
    # No orders submitted -> close cleanly with zero HF fills.
    fills, mark = close_hf_phase(cycle)
    assert fills == []
    assert mark == 0.0
    assert cycle.state.phase == "HF_CLOSED"


TESTS = [
    test_happy_path_full_cycle,
    test_submit_quote_wrong_phase,
    test_submit_order_wrong_phase,
    test_close_mm_phase_wrong_phase,
    test_close_hf_phase_wrong_phase,
    test_duplicate_quote_rejected,
    test_duplicate_order_rejected,
    test_positions_seed_from_prior_cycle,
    test_pass_means_no_order,
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
