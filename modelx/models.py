"""Core data models for ModelX.

Plain dataclasses — no validation, no ORM. Persistence lives in db.py.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Account:
    id: str
    name: str
    role: str  # "MM" or "HF"
    model: str  # OpenRouter model id (e.g. "anthropic/claude-opus-4-6") or "human"
    points: float = 0.0
    market_id: str = ""  # empty for legacy/global accounts; set for live markets


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
    search_terms: List[str] = field(default_factory=list)
    price_ticker: Optional[str] = None


@dataclass
class Quote:
    """Two-sided quote from a Market Maker during the MM phase.

    A side with size == 0 is treated as no quote on that side.
    """
    id: str
    phase_id: str
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
    phase_id: str
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
    phase_id: str
    contract_id: str
    buyer_account_id: str
    seller_account_id: str
    price: float
    size: int
    phase: str  # "MM" or "HF"
    created_at: float = 0.0


@dataclass
class PhaseState:
    """One phase (MM or HF) in the market's timeline.

    Each wall-clock tick produces one phase. Phases alternate MM / HF.
    Identified by timestamp, not by an integer index.
    """
    id: str                      # "{contract_id}:{unix_timestamp}"
    contract_id: str
    phase_type: str              # "MM" or "HF"
    phase: str                   # "OPEN" or "CLOSED"
    mark: Optional[float] = None  # VWAP at phase close
    created_at: float = 0.0
    closed_at: Optional[float] = None
    info_text: Optional[str] = None


@dataclass
class Market:
    """A live multi-market exchange instance.

    `state` machine: 'RUNNING' -> 'PENDING_SETTLEMENT' -> 'SETTLED'.
    `pending_mm` is 1 if the next phase to fire on the global tick is MM, else 0.
    Each market owns one Contract row with the same `id`.
    """
    id: str
    name: str
    description: str
    multiplier: float = 1.0
    position_limit: int = 100
    max_size: int = 50
    settlement_date: Optional[str] = None
    state: str = "RUNNING"
    pending_mm: int = 1
    last_phase_ts: float = 0.0
    created_at: float = 0.0


@dataclass
class LifetimeStat:
    """Per-market scored row, written by settle.py for each participant.

    Lifetime aggregation across markets is computed on demand by joining
    these rows in scoring.score_lifetime().
    """
    account_id: str
    market_id: str
    role: str  # "MM" or "HF"
    total_pnl: Optional[float] = None
    sharpe: Optional[float] = None
    volume: Optional[int] = None
    settled_at: Optional[float] = None
