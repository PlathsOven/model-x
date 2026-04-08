"""Core data models for ModelX.

Plain dataclasses — no validation, no ORM. Persistence lives in db.py.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Account:
    id: str
    name: str
    role: str  # "MM" or "HF"
    model: str  # OpenRouter model id (e.g. "anthropic/claude-opus-4-6") or "human"
    points: float = 0.0


@dataclass
class Contract:
    id: str
    name: str
    description: str
    multiplier: float = 1.0
    position_limit: int = 100
    settlement_value: Optional[float] = None
    created_at: float = 0.0
    settled_at: Optional[float] = None


@dataclass
class Quote:
    """Two-sided quote from a Market Maker during the MM phase.

    A side with size == 0 is treated as no quote on that side.
    """
    id: str
    cycle_id: str
    contract_id: str
    account_id: str
    bid_price: float
    bid_size: int
    ask_price: float
    ask_size: int
    created_at: float = 0.0


@dataclass
class Order:
    """Market order from a Hedge Fund during the HF phase."""
    id: str
    cycle_id: str
    contract_id: str
    account_id: str
    side: str  # "buy" or "sell"
    size: int
    created_at: float = 0.0


@dataclass
class Fill:
    """A trade between two accounts.

    phase = "MM" for MM-MM crosses at end of MM phase.
    phase = "HF" for HF lifts/hits during HF phase.
    """
    id: str
    cycle_id: str
    contract_id: str
    buyer_account_id: str
    seller_account_id: str
    price: float
    size: int
    phase: str  # "MM" or "HF"
    created_at: float = 0.0


@dataclass
class CycleState:
    id: str
    contract_id: str
    cycle_index: int
    phase: str  # "MM_OPEN" -> "MM_CLOSED" -> "HF_OPEN" -> "HF_CLOSED"
    mm_mark: Optional[float] = None  # VWAP of remaining orderbook after MM cross
    hf_mark: Optional[float] = None  # VWAP of HF fills
    created_at: float = 0.0
    mm_phase_ended_at: Optional[float] = None
    hf_phase_ended_at: Optional[float] = None
