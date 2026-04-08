"""Episode runner smoke tests. Run: python3 tests/test_runner.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import List, Optional

from modelx.agents.base import Agent, AgentContext
from modelx.matching import BookLevel
from modelx.models import Contract, Order, Quote
from modelx.runner import Participant, run_episode


# ---------- fakes ----------

class FakeAgent(Agent):
    """Deterministic agent whose decisions come from per-cycle callables.

    `quote_fn(ctx) -> Optional[Quote]` and `order_fn(ctx, book) -> Optional[Order]`
    are pure functions of the context; use them to encode scripted behavior.
    """

    def __init__(self, quote_fn=None, order_fn=None):
        self.quote_fn = quote_fn
        self.order_fn = order_fn
        self.quote_contexts: List[AgentContext] = []
        self.order_contexts: List[AgentContext] = []

    def get_quote(self, ctx: AgentContext) -> Optional[Quote]:
        self.quote_contexts.append(ctx)
        return self.quote_fn(ctx) if self.quote_fn else None

    def get_order(self, ctx: AgentContext, book: List[BookLevel]) -> Optional[Order]:
        self.order_contexts.append(ctx)
        return self.order_fn(ctx, book) if self.order_fn else None


def make_quote(bid_price, bid_size, ask_price, ask_size):
    def f(ctx):
        return Quote(
            id=f"{ctx.cycle_id}:{ctx.account_id}:q",
            cycle_id=ctx.cycle_id,
            contract_id=ctx.contract.id,
            account_id=ctx.account_id,
            bid_price=bid_price,
            bid_size=bid_size,
            ask_price=ask_price,
            ask_size=ask_size,
        )
    return f


def make_order(side, size):
    def f(ctx, book):
        if size == 0 or side == "pass":
            return None
        return Order(
            id=f"{ctx.cycle_id}:{ctx.account_id}:o",
            cycle_id=ctx.cycle_id,
            contract_id=ctx.contract.id,
            account_id=ctx.account_id,
            side=side,
            size=size,
        )
    return f


class ScriptedIO:
    def __init__(self, lines):
        self.lines = list(lines)
        self.outputs: List[str] = []

    def input(self, prompt=""):
        self.outputs.append(f">PROMPT {prompt}")
        if self.lines:
            return self.lines.pop(0)
        return ""

    def print(self, *args):
        self.outputs.append(" ".join(str(a) for a in args))


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


# ---------- tests ----------

def test_full_episode_two_cycles():
    """Two cycles: MM-B lifts MM-A each cycle; HF-X buys residual; then settle."""
    mm_a = FakeAgent(quote_fn=make_quote(100, 10, 105, 10))
    mm_b = FakeAgent(quote_fn=make_quote(106, 5, 110, 5))
    hf_x = FakeAgent(order_fn=make_order("buy", 3))

    participants = [
        Participant(mm_a, "mm-A", "MM"),
        Participant(mm_b, "mm-B", "MM"),
        Participant(hf_x, "hf-X", "HF"),
    ]

    # 2 cycles * 4 phase-transition prompts = 8 Enter lines. Settlement pre-set.
    io = ScriptedIO([""] * 8)

    result = run_episode(
        contract=_contract(),
        info_schedule={},
        participants=participants,
        num_cycles=2,
        settlement_value=107.0,
        input_fn=io.input,
        print_fn=io.print,
    )

    # Each cycle: MM-B lifts 5 from MM-A at midpoint 105.5 -> MM-A -5, MM-B +5.
    # Residual has MM-A ask 5@105. HF-X buys 3 from MM-A @105.
    # Post-cycle: MM-A -8, MM-B +5, HF-X +3 (per cycle).
    # After 2 cycles: MM-A -16, MM-B +10, HF-X +6.
    assert result.positions["mm-A"] == -16, result.positions
    assert result.positions["mm-B"] == 10
    assert result.positions["hf-X"] == 6

    # Cash check: MM-A sold 5 @ 105.5 + 3 @ 105 per cycle.
    # cash_MM_A per cycle = 5 * 105.5 + 3 * 105 = 527.5 + 315 = 842.5
    # MM-B bought 5 @ 105.5: cash -= 527.5 per cycle.
    # HF-X bought 3 @ 105: cash -= 315 per cycle.
    assert abs(result.cash["mm-A"] - 2 * 842.5) < 1e-9
    assert abs(result.cash["mm-B"] - 2 * -527.5) < 1e-9
    assert abs(result.cash["hf-X"] - 2 * -315.0) < 1e-9

    # P&L at settlement 107, multiplier 1.0:
    # MM-A: 2*842.5 + (-16 * 107) = 1685 - 1712 = -27
    # MM-B: -2*527.5 + 10 * 107 = -1055 + 1070 = 15
    # HF-X: -2*315 + 6 * 107 = -630 + 642 = 12
    assert abs(result.pnl["mm-A"] - (-27.0)) < 1e-9, result.pnl
    assert abs(result.pnl["mm-B"] - 15.0) < 1e-9
    assert abs(result.pnl["hf-X"] - 12.0) < 1e-9

    assert result.settlement_value == 107.0
    assert len(result.cycles) == 2
    # 2 cycles * (1 MM fill + 1 HF fill) = 4 fills total
    assert len(result.all_fills) == 4
    # Runner stamps the contract so downstream scoring can read it.
    assert result.cycles[0].contract.settlement_value == 107.0


def test_settlement_prompt_when_not_preset():
    """If settlement_value is None, runner prompts and reads from input_fn."""
    mm = FakeAgent(quote_fn=make_quote(100, 5, 102, 5))
    participants = [Participant(mm, "mm-A", "MM")]

    # 1 cycle: 4 phase Enters + 1 settlement value line.
    io = ScriptedIO(["", "", "", "", "99.5"])

    result = run_episode(
        contract=_contract(),
        info_schedule={},
        participants=participants,
        num_cycles=1,
        settlement_value=None,
        input_fn=io.input,
        print_fn=io.print,
    )
    assert result.settlement_value == 99.5


def test_info_schedule_revealed_in_context():
    """Info items added for cycle N appear in that cycle's agent contexts."""
    mm = FakeAgent(quote_fn=make_quote(100, 5, 102, 5))
    hf = FakeAgent(order_fn=make_order("pass", 0))
    participants = [
        Participant(mm, "mm-A", "MM"),
        Participant(hf, "hf-X", "HF"),
    ]

    info = {
        0: ["CPI rumor: hot print expected"],
        1: ["BLS data delayed by 1 day"],
    }
    io = ScriptedIO([""] * 8)

    run_episode(
        contract=_contract(),
        info_schedule=info,
        participants=participants,
        num_cycles=2,
        settlement_value=100.0,
        input_fn=io.input,
        print_fn=io.print,
    )

    # Cycle 0: MM sees cycle-0 info.
    assert "CPI rumor" in mm.quote_contexts[0].information_log
    # Cycle 1: MM sees both items (log is cumulative).
    assert "CPI rumor" in mm.quote_contexts[1].information_log
    assert "BLS data delayed" in mm.quote_contexts[1].information_log
    # HF sees the same log in cycle 0 (post-MM phase).
    assert "CPI rumor" in hf.order_contexts[0].information_log


def test_mm_skip_and_hf_pass():
    """MM returning None counts as no quote; HF returning None = pass."""
    mm_skip = FakeAgent(quote_fn=lambda ctx: None)
    mm_real = FakeAgent(quote_fn=make_quote(100, 5, 102, 5))
    hf_pass = FakeAgent(order_fn=lambda ctx, book: None)

    participants = [
        Participant(mm_skip, "mm-A", "MM"),
        Participant(mm_real, "mm-B", "MM"),
        Participant(hf_pass, "hf-X", "HF"),
    ]
    io = ScriptedIO([""] * 4)

    result = run_episode(
        contract=_contract(),
        info_schedule={},
        participants=participants,
        num_cycles=1,
        settlement_value=101.0,
        input_fn=io.input,
        print_fn=io.print,
    )

    # No crossing: MM-A skipped, MM-B is alone, no HF orders.
    assert result.positions == {"mm-A": 0, "mm-B": 0, "hf-X": 0}
    assert result.all_fills == []
    # Final P&L: everyone at 0 cash + 0 position.
    for v in result.pnl.values():
        assert v == 0.0


def test_agent_exception_does_not_halt_cycle():
    """A raising agent is skipped; the cycle still runs other agents and closes."""
    def boom(ctx):
        raise RuntimeError("boom")

    mm_bad = FakeAgent(quote_fn=boom)
    mm_good = FakeAgent(quote_fn=make_quote(100, 5, 102, 5))
    participants = [
        Participant(mm_bad, "mm-A", "MM"),
        Participant(mm_good, "mm-B", "MM"),
    ]
    io = ScriptedIO([""] * 4)

    result = run_episode(
        contract=_contract(),
        info_schedule={},
        participants=participants,
        num_cycles=1,
        settlement_value=100.0,
        input_fn=io.input,
        print_fn=io.print,
    )

    # The episode completed; the error surfaced in output.
    assert result.settlement_value == 100.0
    error_lines = [o for o in io.outputs if "agent error" in o]
    assert error_lines, io.outputs
    assert "boom" in error_lines[0]


def test_multiplier_applied_to_pnl():
    """Contract multiplier scales final P&L."""
    mm = FakeAgent(quote_fn=make_quote(100, 5, 102, 5))
    hf = FakeAgent(order_fn=make_order("buy", 2))
    participants = [
        Participant(mm, "mm-A", "MM"),
        Participant(hf, "hf-X", "HF"),
    ]
    io = ScriptedIO([""] * 4)

    result = run_episode(
        contract=_contract(multiplier=10.0),
        info_schedule={},
        participants=participants,
        num_cycles=1,
        settlement_value=105.0,
        input_fn=io.input,
        print_fn=io.print,
    )

    # HF-X buys 2 @ 102 -> cash -204, pos +2.
    # MM-A: cash +204, pos -2.
    # At settle 105:
    # HF-X: (-204 + 2*105) * 10 = (-204 + 210) * 10 = 60
    # MM-A: (204 - 2*105) * 10 = (204 - 210) * 10 = -60
    assert abs(result.pnl["hf-X"] - 60.0) < 1e-9, result.pnl
    assert abs(result.pnl["mm-A"] - (-60.0)) < 1e-9


TESTS = [
    test_full_episode_two_cycles,
    test_settlement_prompt_when_not_preset,
    test_info_schedule_revealed_in_context,
    test_mm_skip_and_hf_pass,
    test_agent_exception_does_not_halt_cycle,
    test_multiplier_applied_to_pnl,
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
