"""Abstract Agent interface for MM and HF participants."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from ..matching import BookLevel
from ..models import Contract, Order, Quote


@dataclass
class AgentContext:
    """Everything an agent needs to make a decision in one cycle.

    `trade_history` and `information_log` are pre-formatted strings produced
    by the caller (runner / cycle wrapper) so individual Agent implementations
    stay format-agnostic.
    """
    account_id: str
    cycle_id: str
    contract: Contract
    cycle_number: int
    total_cycles: int
    position: int
    pnl: float
    trade_history: str
    information_log: str
    settlement_date: str = "TBD"
    position_limit: int = 100
    max_size: int = 50


class Agent(ABC):
    """An MM or HF participant.

    Concrete implementations (OpenRouterAgent, HumanAgent, ...) implement
    both methods. Returning None means "skip this cycle" for an MM (counts
    against uptime) or "pass" for an HF.
    """

    @abstractmethod
    def get_quote(self, ctx: AgentContext) -> Optional[Quote]:
        """Return this agent's MM quote for the cycle, or None to skip."""
        ...

    @abstractmethod
    def get_order(self, ctx: AgentContext, book: List[BookLevel]) -> Optional[Order]:
        """Return this agent's HF market order for the cycle, or None to pass."""
        ...


def format_book(book: List[BookLevel]) -> str:
    """Render a residual orderbook as text for LLM and human display."""
    if not book:
        return "(empty)"
    asks = sorted([l for l in book if l.side == "ask"], key=lambda l: l.price)
    bids = sorted([l for l in book if l.side == "bid"], key=lambda l: -l.price)
    lines: List[str] = []
    if asks:
        lines.append("Asks (lowest to highest):")
        for lvl in asks:
            lines.append(f"  {lvl.size} @ {lvl.price}  ({lvl.account_id})")
    if bids:
        if asks:
            lines.append("")
        lines.append("Bids (highest to lowest):")
        for lvl in bids:
            lines.append(f"  {lvl.size} @ {lvl.price}  ({lvl.account_id})")
    return "\n".join(lines)
