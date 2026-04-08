"""Human-controlled Agent: prompts the operator at the CLI for manual play and debugging."""

from typing import List, Optional

from ..matching import BookLevel
from ..models import Order, Quote
from .base import Agent, AgentContext, format_book


class HumanAgent(Agent):
    """A keyboard-driven Agent.

    `input_fn` is injectable for tests; defaults to the builtin `input`.
    """

    def __init__(self, input_fn=input, output_fn=print):
        self._input = input_fn
        self._print = output_fn

    def get_quote(self, ctx: AgentContext) -> Optional[Quote]:
        self._render_header("MM quote", ctx)
        choice = self._input("Submit a quote? [Y/n] ").strip().lower()
        if choice == "n":
            return None
        bid_price = self._ask_float("Bid price")
        bid_size = self._ask_int("Bid size", default=0)
        ask_price = self._ask_float("Ask price")
        ask_size = self._ask_int("Ask size", default=0)
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

    def get_order(self, ctx: AgentContext, book: List[BookLevel]) -> Optional[Order]:
        self._render_header("HF order", ctx)
        self._print("")
        self._print("Orderbook:")
        self._print(format_book(book))
        self._print("")
        side = self._input("Side (buy/sell/pass): ").strip().lower()
        if side not in ("buy", "sell"):
            return None
        size = self._ask_int("Size", default=0)
        if size <= 0:
            return None
        return Order(
            id=f"{ctx.cycle_id}:{ctx.account_id}:o",
            cycle_id=ctx.cycle_id,
            contract_id=ctx.contract.id,
            account_id=ctx.account_id,
            side=side,
            size=size,
        )

    # ---- helpers ----

    def _render_header(self, label: str, ctx: AgentContext) -> None:
        self._print("")
        self._print(f"=== {label} for {ctx.account_id} ({ctx.contract.name}) ===")
        self._print(f"Cycle {ctx.cycle_number}/{ctx.total_cycles}")
        self._print(
            f"Position: {ctx.position}  P&L: {ctx.pnl:.4f}  Limit: ±{ctx.position_limit}"
        )
        self._print(
            f"Multiplier: {ctx.contract.multiplier}  Settlement: {ctx.settlement_date}"
        )
        self._print(f"Description: {ctx.contract.description}")
        if ctx.information_log:
            self._print("")
            self._print("Information log:")
            self._print(ctx.information_log)
        if ctx.trade_history:
            self._print("")
            self._print("Trade history:")
            self._print(ctx.trade_history)

    def _ask_float(self, prompt: str) -> float:
        while True:
            raw = self._input(f"{prompt}: ").strip()
            try:
                return float(raw)
            except ValueError:
                self._print(f"  invalid float: {raw!r}")

    def _ask_int(self, prompt: str, default: int = 0) -> int:
        while True:
            raw = self._input(f"{prompt}: ").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                self._print(f"  invalid int: {raw!r}")
