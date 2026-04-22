"""Tests for the over-limit cycles metric.

Run: python3 -m pytest tests/test_over_limit_cycles.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.models import Contract, Fill, Order, PhaseState, Quote
from modelx.phase import Phase
from modelx.scoring import _over_limit_cycles


def _contract(position_limit: int = 100) -> Contract:
    return Contract(
        id="cpi",
        name="CPI",
        description="MoM CPI print",
        multiplier=1.0,
        position_limit=position_limit,
    )


def _mm_phase(t: float, account: str, bid_size: int, ask_size: int, fills=None) -> Phase:
    state = PhaseState(
        id=f"cpi:{int(t)}",
        contract_id="cpi",
        phase_type="MM",
        phase="CLOSED",
        created_at=t,
    )
    quote = Quote(
        id=f"q:{int(t)}:{account}",
        phase_id=state.id,
        contract_id="cpi",
        account_id=account,
        bid_price=99.0,
        bid_size=bid_size,
        ask_price=101.0,
        ask_size=ask_size,
    )
    return Phase(
        contract=_contract(),
        state=state,
        positions={},
        quotes=[quote],
        fills=fills or [],
    )


def _hf_phase(t: float, account: str, side: str, size: int, fills=None) -> Phase:
    state = PhaseState(
        id=f"cpi:{int(t)}",
        contract_id="cpi",
        phase_type="HF",
        phase="CLOSED",
        created_at=t,
    )
    order = Order(
        id=f"o:{int(t)}:{account}",
        phase_id=state.id,
        contract_id="cpi",
        account_id=account,
        side=side,
        size=size,
    )
    return Phase(
        contract=_contract(),
        state=state,
        positions={},
        quotes=[],
        orders=[order],
        fills=fills or [],
    )


def _fill(t: float, buyer: str, seller: str, size: int) -> Fill:
    return Fill(
        id=f"f:{int(t)}",
        phase_id=f"cpi:{int(t)}",
        contract_id="cpi",
        buyer_account_id=buyer,
        seller_account_id=seller,
        price=100.0,
        size=size,
        phase="MM",
    )


# ---------- MM tests ----------

def test_mm_no_violation_at_zero_position():
    phases = [_mm_phase(1.0, "mm-A", bid_size=50, ask_size=50)]
    assert _over_limit_cycles(phases, "mm-A", "MM", 100) == 0


def test_mm_bid_size_exceeds_limit():
    phases = [_mm_phase(1.0, "mm-A", bid_size=101, ask_size=50)]
    assert _over_limit_cycles(phases, "mm-A", "MM", 100) == 1


def test_mm_ask_size_exceeds_limit():
    phases = [_mm_phase(1.0, "mm-A", bid_size=50, ask_size=101)]
    assert _over_limit_cycles(phases, "mm-A", "MM", 100) == 1


def test_mm_both_sides_exceed_counts_once():
    phases = [_mm_phase(1.0, "mm-A", bid_size=200, ask_size=200)]
    assert _over_limit_cycles(phases, "mm-A", "MM", 100) == 1


def test_mm_existing_position_pushes_over():
    """pos=80 entering, bid_size=30 -> would push to 110, over limit 100."""
    p1 = _mm_phase(1.0, "mm-A", bid_size=80, ask_size=0,
                   fills=[_fill(1.0, "mm-A", "mm-B", 80)])
    p2 = _mm_phase(2.0, "mm-A", bid_size=30, ask_size=0)
    assert _over_limit_cycles([p1, p2], "mm-A", "MM", 100) == 1


def test_mm_short_position_with_ask_pushes_over():
    """pos=-80, ask_size=30 -> would push to -110."""
    p1 = _mm_phase(1.0, "mm-A", bid_size=0, ask_size=80,
                   fills=[_fill(1.0, "mm-B", "mm-A", 80)])
    p2 = _mm_phase(2.0, "mm-A", bid_size=0, ask_size=30)
    assert _over_limit_cycles([p1, p2], "mm-A", "MM", 100) == 1


def test_mm_skips_other_accounts():
    phases = [_mm_phase(1.0, "mm-B", bid_size=200, ask_size=200)]
    assert _over_limit_cycles(phases, "mm-A", "MM", 100) == 0


def test_mm_multiple_violations_counted_separately():
    p1 = _mm_phase(1.0, "mm-A", bid_size=200, ask_size=50)
    p2 = _mm_phase(2.0, "mm-A", bid_size=50, ask_size=50)  # ok
    p3 = _mm_phase(3.0, "mm-A", bid_size=50, ask_size=200)
    assert _over_limit_cycles([p1, p2, p3], "mm-A", "MM", 100) == 2


# ---------- HF tests ----------

def test_hf_no_violation_at_zero_position():
    phases = [_hf_phase(1.0, "hf-X", "buy", 50)]
    assert _over_limit_cycles(phases, "hf-X", "HF", 100) == 0


def test_hf_buy_exceeds_limit():
    phases = [_hf_phase(1.0, "hf-X", "buy", 101)]
    assert _over_limit_cycles(phases, "hf-X", "HF", 100) == 1


def test_hf_sell_exceeds_limit():
    phases = [_hf_phase(1.0, "hf-X", "sell", 101)]
    assert _over_limit_cycles(phases, "hf-X", "HF", 100) == 1


def test_hf_buy_with_existing_position_exceeds():
    """pos=80 entering HF phase, buy 30 -> would push to 110."""
    p1 = _hf_phase(1.0, "hf-X", "buy", 80,
                   fills=[_fill(1.0, "hf-X", "mm-A", 80)])
    p2 = _hf_phase(2.0, "hf-X", "buy", 30)
    assert _over_limit_cycles([p1, p2], "hf-X", "HF", 100) == 1


def test_hf_skips_mm_role_phases():
    """An HF account's over-limit only counts on HF phases, not MM phases."""
    mm_phase = _mm_phase(1.0, "mm-A", bid_size=200, ask_size=200)
    hf_phase = _hf_phase(2.0, "hf-X", "buy", 50)
    assert _over_limit_cycles([mm_phase, hf_phase], "hf-X", "HF", 100) == 0


def test_mm_skips_hf_role_phases():
    mm_phase = _mm_phase(1.0, "mm-A", bid_size=50, ask_size=50)
    hf_phase = _hf_phase(2.0, "hf-X", "buy", 200)
    assert _over_limit_cycles([mm_phase, hf_phase], "mm-A", "MM", 100) == 0


def test_phases_processed_chronologically_unsorted_input():
    """Pass phases out of order; helper should still order by created_at."""
    p1 = _mm_phase(1.0, "mm-A", bid_size=80, ask_size=0,
                   fills=[_fill(1.0, "mm-A", "mm-B", 80)])
    p2 = _mm_phase(2.0, "mm-A", bid_size=30, ask_size=0)  # over after fill
    # Pass out of order — helper sorts by created_at.
    assert _over_limit_cycles([p2, p1], "mm-A", "MM", 100) == 1
