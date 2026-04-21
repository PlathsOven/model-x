"""Per-account MM and HF metrics.

Two entry points: `score_mm` and `score_hf`. Each returns a dict
`{account_id: Scores}`. PnL is always available via mark-to-market
(uses settlement_value when settled, otherwise the latest mark).

Lifetime scoring (`score_lifetime` / `list_lifetime_by_account`) aggregates
across multiple settled markets by reading rows from `agent_lifetime_stats`.
"""

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .phase import Phase
from .db import list_lifetime_stats
from .models import Contract, Fill, LifetimeStat


@dataclass
class MMScores:
    account_id: str
    total_pnl: float
    sharpe: float
    volume: int
    volume_share: float
    pnl_bps: float
    uptime: float
    consensus: float
    markout_2: float
    markout_10: float
    markout_40: float
    markout_2_bps: float
    markout_10_bps: float
    markout_40_bps: float
    avg_abs_position: float
    self_cross_count: int
    self_cross_volume: int


@dataclass
class HFScores:
    account_id: str
    total_pnl: float
    sharpe: float
    markout_2: float
    markout_10: float
    markout_40: float
    markout_2_bps: float
    markout_10_bps: float
    markout_40_bps: float


# ---------- entry points ----------

def score_mm(
    fills: List[Fill],
    phases: List[Phase],
    positions: Dict[str, int],
    contract: Contract,
    latest_mark: float = 0.0,
) -> Dict[str, MMScores]:
    """Return per-MM scores. Uses settlement_value if available, else latest_mark."""
    mark = float(contract.settlement_value) if contract.settlement_value else latest_mark
    marks = _carry_mark_forward(phases)
    mm_accounts = _mm_accounts(phases)

    # Volume share: denominator is total MM-phase volume only.
    mm_fills = [f for f in fills if f.phase == "MM"]
    total_mm_volume = sum(f.size for f in mm_fills)

    result: Dict[str, MMScores] = {}
    for acct in sorted(mm_accounts):
        pnl_series = _phase_pnl_series(phases, marks, acct, contract.multiplier)
        volume = _account_volume(fills, acct)
        notional = _account_notional(fills, acct)
        total_pnl = _pnl(
            fills, positions.get(acct, 0), acct, mark, contract.multiplier,
        )
        pnl_bps_val = 10000.0 * total_pnl / notional if notional > 0 else 0.0
        mm_acct_vol = _account_volume(mm_fills, acct)
        vol_share = mm_acct_vol / total_mm_volume if total_mm_volume > 0 else 0.0
        self_count, self_vol = _self_crosses(fills, acct)
        result[acct] = MMScores(
            account_id=acct,
            total_pnl=total_pnl,
            sharpe=_sharpe(pnl_series),
            volume=volume,
            volume_share=vol_share,
            pnl_bps=pnl_bps_val,
            uptime=_uptime(phases, acct),
            consensus=_consensus(fills, acct, mm_accounts),
            markout_2=_markout(phases, marks, acct, 2),
            markout_10=_markout(phases, marks, acct, 10),
            markout_40=_markout(phases, marks, acct, 40),
            markout_2_bps=_markout_bps(phases, marks, acct, 2),
            markout_10_bps=_markout_bps(phases, marks, acct, 10),
            markout_40_bps=_markout_bps(phases, marks, acct, 40),
            avg_abs_position=_avg_abs_position(phases, acct),
            self_cross_count=self_count,
            self_cross_volume=self_vol,
        )
    return result


def score_hf(
    fills: List[Fill],
    phases: List[Phase],
    positions: Dict[str, int],
    contract: Contract,
    latest_mark: float = 0.0,
) -> Dict[str, HFScores]:
    """Return per-HF scores. Uses settlement_value if available, else latest_mark."""
    mark = float(contract.settlement_value) if contract.settlement_value else latest_mark
    marks = _carry_mark_forward(phases)
    hf_accounts = _hf_accounts(phases)

    result: Dict[str, HFScores] = {}
    for acct in sorted(hf_accounts):
        pnl_series = _phase_pnl_series(phases, marks, acct, contract.multiplier)
        total_pnl = _pnl(
            fills, positions.get(acct, 0), acct, mark, contract.multiplier,
        )
        result[acct] = HFScores(
            account_id=acct,
            total_pnl=total_pnl,
            sharpe=_sharpe(pnl_series),
            markout_2=_markout(phases, marks, acct, 2),
            markout_10=_markout(phases, marks, acct, 10),
            markout_40=_markout(phases, marks, acct, 40),
            markout_2_bps=_markout_bps(phases, marks, acct, 2),
            markout_10_bps=_markout_bps(phases, marks, acct, 10),
            markout_40_bps=_markout_bps(phases, marks, acct, 40),
        )
    return result


# ---------- helpers ----------

def _mm_accounts(phases: List[Phase]) -> Set[str]:
    return {q.account_id for p in phases for q in p.quotes}


def _hf_accounts(phases: List[Phase]) -> Set[str]:
    return {o.account_id for p in phases for o in p.orders}


def _carry_mark_forward(phases: List[Phase]) -> List[float]:
    """Last-available mark per phase, carrying forward on gaps."""
    marks: List[float] = []
    current = 0.0
    for p in phases:
        m = p.state.mark
        if m and m > 0:
            current = m
        marks.append(current)
    return marks


def _phase_pnl_series(
    phases: List[Phase],
    marks: List[float],
    account_id: str,
    multiplier: float,
) -> List[float]:
    """Running total PnL at the end of each phase, mark-to-market."""
    cash = 0.0
    pos = 0
    out: List[float] = []
    for i, phase in enumerate(phases):
        for f in phase.fills:
            if f.buyer_account_id == account_id:
                cash -= f.price * f.size
                pos += f.size
            if f.seller_account_id == account_id:
                cash += f.price * f.size
                pos -= f.size
        out.append((cash + pos * marks[i]) * multiplier)
    return out


def _sharpe(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    changes = [pnls[0]]
    for i in range(1, len(pnls)):
        changes.append(pnls[i] - pnls[i - 1])
    n = len(changes)
    if n == 0:
        return 0.0
    total = sum(changes)
    mean = total / n
    var = sum((c - mean) ** 2 for c in changes) / n
    std = var ** 0.5
    if std == 0:
        return 0.0
    return total / (std * (n ** 0.5))


def _account_volume(fills: List[Fill], account_id: str) -> int:
    total = 0
    for f in fills:
        if f.buyer_account_id == account_id or f.seller_account_id == account_id:
            total += f.size
    return total


def _account_notional(fills: List[Fill], account_id: str) -> float:
    total = 0.0
    for f in fills:
        if f.buyer_account_id == account_id or f.seller_account_id == account_id:
            total += f.price * f.size
    return total


def _pnl(
    fills: List[Fill],
    final_position: int,
    account_id: str,
    mark: float,
    multiplier: float,
) -> float:
    """PnL = (cash + position * mark) * multiplier."""
    cash = 0.0
    for f in fills:
        if f.buyer_account_id == account_id:
            cash -= f.price * f.size
        if f.seller_account_id == account_id:
            cash += f.price * f.size
    return (cash + final_position * mark) * multiplier


def _uptime(phases: List[Phase], account_id: str) -> float:
    """Fraction of MM phases where the account submitted a quote."""
    mm_phases = [p for p in phases if p.state.phase_type == "MM"]
    if not mm_phases:
        return 0.0
    quoted = sum(
        1 for p in mm_phases if any(q.account_id == account_id for q in p.quotes)
    )
    return quoted / len(mm_phases)


def _consensus(
    fills: List[Fill],
    account_id: str,
    mm_accounts: Set[str],
) -> float:
    total = 0
    with_other_mms = 0
    for f in fills:
        buyer_is = (f.buyer_account_id == account_id)
        seller_is = (f.seller_account_id == account_id)
        if not buyer_is and not seller_is:
            continue
        total += f.size
        if buyer_is and seller_is:
            continue  # self-trade
        other = f.seller_account_id if buyer_is else f.buyer_account_id
        if other in mm_accounts:
            with_other_mms += f.size
    if total == 0:
        return 0.0
    return 1.0 - (with_other_mms / total)


def _markout(
    phases: List[Phase],
    marks: List[float],
    account_id: str,
    n: int,
) -> float:
    """Size-weighted average N-phase markout for fills involving `account_id`."""
    total_contrib = 0.0
    total_size = 0
    for i, phase in enumerate(phases):
        target = i + n
        if target >= len(phases):
            continue
        target_mark = marks[target]
        for f in phase.fills:
            buyer_is = (f.buyer_account_id == account_id)
            seller_is = (f.seller_account_id == account_id)
            if not buyer_is and not seller_is:
                continue
            direction = (1 if buyer_is else 0) - (1 if seller_is else 0)
            markout = (target_mark - f.price) * direction
            total_contrib += markout * f.size
            total_size += f.size
    if total_size == 0:
        return 0.0
    return total_contrib / total_size


def _markout_bps(
    phases: List[Phase],
    marks: List[float],
    account_id: str,
    n: int,
) -> float:
    """N-phase markout as bps of notional, over fills with a target mark."""
    total_contrib = 0.0
    total_notional = 0.0
    for i, phase in enumerate(phases):
        target = i + n
        if target >= len(phases):
            continue
        target_mark = marks[target]
        for f in phase.fills:
            buyer_is = (f.buyer_account_id == account_id)
            seller_is = (f.seller_account_id == account_id)
            if not buyer_is and not seller_is:
                continue
            direction = (1 if buyer_is else 0) - (1 if seller_is else 0)
            total_contrib += (target_mark - f.price) * direction * f.size
            total_notional += f.price * f.size
    if total_notional == 0:
        return 0.0
    return 10000.0 * total_contrib / total_notional


def _avg_abs_position(phases: List[Phase], account_id: str) -> float:
    if not phases:
        return 0.0
    total = sum(abs(p.positions.get(account_id, 0)) for p in phases)
    return total / len(phases)


def _self_crosses(fills: List[Fill], account_id: str) -> Tuple[int, int]:
    count = 0
    volume = 0
    for f in fills:
        if f.buyer_account_id == account_id and f.seller_account_id == account_id:
            count += 1
            volume += f.size
    return count, volume


# ---------- lifetime aggregation ----------

@dataclass
class LifetimeScores:
    account_id: str
    name: str
    markets_traded: int
    total_pnl: float
    total_volume: int
    avg_sharpe: float
    best_market_pnl: float
    worst_market_pnl: float
    per_market: List[LifetimeStat]


def score_lifetime(
    conn: sqlite3.Connection,
    account_id: str,
) -> LifetimeScores:
    rows = list_lifetime_stats(conn, account_id=account_id)
    name = _strip_market_prefix(account_id)
    if not rows:
        return LifetimeScores(
            account_id=account_id, name=name,
            markets_traded=0, total_pnl=0.0, total_volume=0,
            avg_sharpe=0.0, best_market_pnl=0.0, worst_market_pnl=0.0,
            per_market=[],
        )
    pnls = [r.total_pnl or 0.0 for r in rows]
    sharpes = [r.sharpe for r in rows if r.sharpe is not None]
    return LifetimeScores(
        account_id=account_id,
        name=name,
        markets_traded=len(rows),
        total_pnl=sum(pnls),
        total_volume=sum(r.volume or 0 for r in rows),
        avg_sharpe=sum(sharpes) / len(sharpes) if sharpes else 0.0,
        best_market_pnl=max(pnls),
        worst_market_pnl=min(pnls),
        per_market=rows,
    )


def list_lifetime_by_name(
    conn: sqlite3.Connection,
) -> Dict[str, LifetimeScores]:
    all_rows = list_lifetime_stats(conn)
    grouped: Dict[str, List[LifetimeStat]] = {}
    for r in all_rows:
        name = _strip_market_prefix(r.account_id)
        grouped.setdefault(name, []).append(r)

    out: Dict[str, LifetimeScores] = {}
    for name, rows in grouped.items():
        pnls = [r.total_pnl or 0.0 for r in rows]
        sharpes = [r.sharpe for r in rows if r.sharpe is not None]
        out[name] = LifetimeScores(
            account_id=name,
            name=name,
            markets_traded=len(rows),
            total_pnl=sum(pnls),
            total_volume=sum(r.volume or 0 for r in rows),
            avg_sharpe=sum(sharpes) / len(sharpes) if sharpes else 0.0,
            best_market_pnl=max(pnls),
            worst_market_pnl=min(pnls),
            per_market=rows,
        )
    return out


def _strip_market_prefix(account_id: str) -> str:
    if ":" in account_id:
        return account_id.split(":", 1)[1]
    return account_id
