"""Scoring tests. Run: python3 tests/test_scoring.py"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.cycle import (
    close_hf_phase,
    close_mm_phase,
    open_cycle,
    submit_order,
    submit_quote,
)
from modelx.models import Contract, Order, Quote
from modelx.scoring import score_hf, score_mm


def _contract(**overrides) -> Contract:
    base = dict(
        id="cpi",
        name="CPI",
        description="MoM CPI print",
        multiplier=1.0,
        position_limit=100,
    )
    base.update(overrides)
    return Contract(**base)


def _q(cid, contract_id, account, bid_p, bid_s, ask_p, ask_s) -> Quote:
    return Quote(
        id=f"{cid}:{account}:q",
        cycle_id=cid,
        contract_id=contract_id,
        account_id=account,
        bid_price=bid_p,
        bid_size=bid_s,
        ask_price=ask_p,
        ask_size=ask_s,
    )


def _o(cid, contract_id, account, side, size) -> Order:
    return Order(
        id=f"{cid}:{account}:o",
        cycle_id=cid,
        contract_id=contract_id,
        account_id=account,
        side=side,
        size=size,
    )


# ---------- scenarios ----------

def _episode_single_mm_single_hf(contract: Contract):
    """3 cycles: MM-A quotes shift by +5 each cycle, HF-X buys 3 at the ask each time."""
    positions = {"mm-A": 0, "hf-X": 0}
    cycles = []
    for i in range(3):
        cycle = open_cycle(contract, i, positions=positions)
        submit_quote(cycle, _q(cycle.state.id, contract.id, "mm-A", 100 + 5 * i, 5, 105 + 5 * i, 5))
        close_mm_phase(cycle)
        submit_order(cycle, _o(cycle.state.id, contract.id, "hf-X", "buy", 3))
        close_hf_phase(cycle)
        positions.update(cycle.positions)
        cycles.append(cycle)
    fills = [f for c in cycles for f in c.fills]
    return cycles, positions, fills


# ---------- tests ----------

def test_require_settlement():
    c = _contract()  # settlement_value=None by default
    try:
        score_mm([], [], {}, c)
    except ValueError as e:
        assert "settlement_value" in str(e), e
        return
    assert False, "expected ValueError"


def test_single_mm_single_hf_metrics():
    c = _contract()
    c.settlement_value = 115.0
    cycles, positions, fills = _episode_single_mm_single_hf(c)

    mm = score_mm(fills, cycles, positions, c)
    hf = score_hf(fills, cycles, positions, c)

    assert set(mm.keys()) == {"mm-A"}
    assert set(hf.keys()) == {"hf-X"}

    # MM-A sells 3 per cycle at 105, 110, 115 -> cash 990, pos -9.
    # Settlement 115: pnl = 990 - 9*115 = -45.
    assert abs(mm["mm-A"].total_pnl - (-45.0)) < 1e-9
    # HF-X symmetric.
    assert abs(hf["hf-X"].total_pnl - 45.0) < 1e-9

    assert mm["mm-A"].volume == 9
    assert mm["mm-A"].volume_share == 1.0
    assert abs(mm["mm-A"].pnl_bps - (10000 * -45.0 / 9)) < 1e-6
    assert mm["mm-A"].uptime == 1.0
    assert mm["mm-A"].consensus == 1.0  # no other MMs
    # abs positions at end of each cycle: 3, 6, 9 -> avg 6.
    assert mm["mm-A"].avg_abs_position == 6.0
    assert mm["mm-A"].self_cross_count == 0
    assert mm["mm-A"].self_cross_volume == 0

    # Markout 1 (MM is seller, short, price rising -> losses):
    # Cycle 0: sold 3 @ 105, next mark 110 -> (110-105)*-1 = -5 * 3 = -15
    # Cycle 1: sold 3 @ 110, next mark 115 -> -5 * 3 = -15
    # Cycle 2: target beyond; skip.
    # Total -30 / 6 = -5.
    assert abs(mm["mm-A"].markout_1 - (-5.0)) < 1e-9
    assert abs(hf["hf-X"].markout_1 - 5.0) < 1e-9

    # 5-cycle and 20-cycle: no target data.
    assert mm["mm-A"].markout_5 == 0.0
    assert mm["mm-A"].markout_20 == 0.0
    assert hf["hf-X"].markout_5 == 0.0

    # Sharpe: per-cycle MM-A pnls are 0, -15, -45. Changes [0, -15, -30].
    # total = -45, std = sqrt(((0+15)^2 + 0 + (-15)^2)/3) = sqrt(150).
    # Sharpe = total / (sqrt(N) * std) = -45 / (sqrt(3) * sqrt(150))
    #        = -45 / sqrt(450) = -15 / sqrt(50).
    assert mm["mm-A"].sharpe < 0
    assert abs(mm["mm-A"].sharpe - (-45.0 / math.sqrt(450.0))) < 1e-9
    assert abs(hf["hf-X"].sharpe - (45.0 / math.sqrt(450.0))) < 1e-9


def test_self_cross_metrics():
    """An inverted MM quote self-trades; count + volume show up in scores."""
    c = _contract()
    c.settlement_value = 100.0
    cycle = open_cycle(c, 0, positions={"mm-A": 0})
    submit_quote(cycle, _q(cycle.state.id, c.id, "mm-A", 50, 5, 45, 5))
    close_mm_phase(cycle)
    close_hf_phase(cycle)
    positions = dict(cycle.positions)
    fills = list(cycle.fills)

    mm = score_mm(fills, [cycle], positions, c)
    assert mm["mm-A"].self_cross_count == 1
    assert mm["mm-A"].self_cross_volume == 5
    assert mm["mm-A"].volume == 5
    # Self-trade counted in total but not in "with other MMs": consensus stays 1.
    assert mm["mm-A"].consensus == 1.0
    # Self-trade nets to zero pnl/position.
    assert mm["mm-A"].total_pnl == 0.0
    assert mm["mm-A"].avg_abs_position == 0.0


def test_mm_cross_consensus_drops_to_zero():
    """Two MMs whose only fills are against each other have consensus = 0."""
    c = _contract()
    c.settlement_value = 100.0
    cycle = open_cycle(c, 0)
    submit_quote(cycle, _q(cycle.state.id, c.id, "mm-A", 100, 10, 105, 10))
    submit_quote(cycle, _q(cycle.state.id, c.id, "mm-B", 106, 5, 110, 5))
    close_mm_phase(cycle)
    close_hf_phase(cycle)
    positions = dict(cycle.positions)
    fills = list(cycle.fills)

    mm = score_mm(fills, [cycle], positions, c)
    assert mm["mm-A"].consensus == 0.0
    assert mm["mm-B"].consensus == 0.0
    # Each is on one side of the single 5-lot fill.
    assert mm["mm-A"].volume == 5
    assert mm["mm-B"].volume == 5
    # Total market volume = 5, each has share 1.0.
    assert mm["mm-A"].volume_share == 1.0
    assert mm["mm-B"].volume_share == 1.0


def test_uptime_partial():
    """MM-B quotes 2 of 4 cycles -> uptime 0.5."""
    c = _contract()
    c.settlement_value = 100.0
    positions = {"mm-A": 0, "mm-B": 0}
    cycles = []
    for i in range(4):
        cycle = open_cycle(c, i, positions=positions)
        submit_quote(cycle, _q(cycle.state.id, c.id, "mm-A", 100, 5, 105, 5))
        if i in (0, 2):
            submit_quote(cycle, _q(cycle.state.id, c.id, "mm-B", 99, 5, 106, 5))
        close_mm_phase(cycle)
        close_hf_phase(cycle)
        positions.update(cycle.positions)
        cycles.append(cycle)
    fills = [f for c in cycles for f in c.fills]

    mm = score_mm(fills, cycles, positions, c)
    assert mm["mm-A"].uptime == 1.0
    assert mm["mm-B"].uptime == 0.5


def test_sharpe_zero_when_no_variance():
    """Constant per-cycle PnL change -> std 0 -> Sharpe 0 by the safety fallback."""
    c = _contract()
    c.settlement_value = 100.0
    positions = {"mm-A": 0, "hf-X": 0}
    cycles = []
    for i in range(3):
        cycle = open_cycle(c, i, positions=positions)
        submit_quote(cycle, _q(cycle.state.id, c.id, "mm-A", 100, 5, 105, 5))
        close_mm_phase(cycle)
        submit_order(cycle, _o(cycle.state.id, c.id, "hf-X", "buy", 1))
        close_hf_phase(cycle)
        positions.update(cycle.positions)
        cycles.append(cycle)
    fills = [f for c in cycles for f in c.fills]

    mm = score_mm(fills, cycles, positions, c)
    # Each cycle: MM sells 1 @ 105, mark = 105, pnl stays 0 every cycle.
    # Changes = [0, 0, 0] -> Sharpe = 0 (safety).
    assert mm["mm-A"].sharpe == 0.0


def test_score_hf_skips_passive_accounts():
    """An HF that never submitted an order is not in score_hf results."""
    c = _contract()
    c.settlement_value = 100.0
    positions = {"mm-A": 0, "hf-X": 0, "hf-Y": 0}
    cycle = open_cycle(c, 0, positions=positions)
    submit_quote(cycle, _q(cycle.state.id, c.id, "mm-A", 100, 5, 105, 5))
    close_mm_phase(cycle)
    submit_order(cycle, _o(cycle.state.id, c.id, "hf-X", "buy", 1))
    # hf-Y passes.
    close_hf_phase(cycle)
    positions.update(cycle.positions)
    fills = list(cycle.fills)

    hf = score_hf(fills, [cycle], positions, c)
    assert "hf-X" in hf
    assert "hf-Y" not in hf


def test_multiplier_scales_pnl_and_pnl_bps():
    c = _contract(multiplier=10.0)
    c.settlement_value = 115.0
    cycles, positions, fills = _episode_single_mm_single_hf(c)

    mm = score_mm(fills, cycles, positions, c)
    hf = score_hf(fills, cycles, positions, c)

    # Base pnls are -45 / 45; with multiplier 10 they become -450 / 450.
    assert abs(mm["mm-A"].total_pnl - (-450.0)) < 1e-9
    assert abs(hf["hf-X"].total_pnl - 450.0) < 1e-9
    # pnl_bps uses multiplier-scaled pnl over raw volume.
    assert abs(mm["mm-A"].pnl_bps - (10000 * -450.0 / 9)) < 1e-6


def test_markouts_beyond_horizon_return_zero():
    c = _contract()
    c.settlement_value = 100.0
    cycles, positions, fills = _episode_single_mm_single_hf(c)
    mm = score_mm(fills, cycles, positions, c)
    # With only 3 cycles, markout_5 and markout_20 have zero forward data.
    assert mm["mm-A"].markout_5 == 0.0
    assert mm["mm-A"].markout_20 == 0.0


TESTS = [
    test_require_settlement,
    test_single_mm_single_hf_metrics,
    test_self_cross_metrics,
    test_mm_cross_consensus_drops_to_zero,
    test_uptime_partial,
    test_sharpe_zero_when_no_variance,
    test_score_hf_skips_passive_accounts,
    test_multiplier_scales_pnl_and_pnl_bps,
    test_markouts_beyond_horizon_return_zero,
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
