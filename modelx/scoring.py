"""Per-account MM and HF metrics for a settled contract.

Two entry points: `score_mm` and `score_hf`. Each returns a dict
`{account_id: Scores}` built by walking the list of `Cycle` objects from
the runner. MMs are discovered from submitted quotes; HFs from submitted
orders. The contract must have `settlement_value` set — otherwise the
functions raise ValueError.
"""

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from .cycle import Cycle
from .models import Contract, Fill


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
    markout_1: float
    markout_5: float
    markout_20: float
    avg_abs_position: float
    self_cross_count: int
    self_cross_volume: int


@dataclass
class HFScores:
    account_id: str
    total_pnl: float
    sharpe: float
    markout_1: float
    markout_5: float
    markout_20: float


# ---------- entry points ----------

def score_mm(
    fills: List[Fill],
    cycles: List[Cycle],
    positions: Dict[str, int],
    contract: Contract,
) -> Dict[str, MMScores]:
    """Return per-MM scores for a settled contract."""
    settlement = _require_settlement(contract)
    marks = _carry_mark_forward(cycles)
    mm_accounts = _mm_accounts(cycles)
    total_market_volume = sum(f.size for f in fills)

    result: Dict[str, MMScores] = {}
    for acct in sorted(mm_accounts):
        pnl_series = _cycle_pnl_series(cycles, marks, acct, contract.multiplier)
        volume = _account_volume(fills, acct)
        total_pnl = _realized_pnl(
            fills, positions.get(acct, 0), acct, settlement, contract.multiplier,
        )
        pnl_bps_val = 10000.0 * total_pnl / volume if volume > 0 else 0.0
        vol_share = volume / total_market_volume if total_market_volume > 0 else 0.0
        self_count, self_vol = _self_crosses(fills, acct)
        result[acct] = MMScores(
            account_id=acct,
            total_pnl=total_pnl,
            sharpe=_sharpe(pnl_series),
            volume=volume,
            volume_share=vol_share,
            pnl_bps=pnl_bps_val,
            uptime=_uptime(cycles, acct),
            consensus=_consensus(fills, acct, mm_accounts),
            markout_1=_markout(cycles, marks, acct, 1),
            markout_5=_markout(cycles, marks, acct, 5),
            markout_20=_markout(cycles, marks, acct, 20),
            avg_abs_position=_avg_abs_position(cycles, acct),
            self_cross_count=self_count,
            self_cross_volume=self_vol,
        )
    return result


def score_hf(
    fills: List[Fill],
    cycles: List[Cycle],
    positions: Dict[str, int],
    contract: Contract,
) -> Dict[str, HFScores]:
    """Return per-HF scores for a settled contract."""
    settlement = _require_settlement(contract)
    marks = _carry_mark_forward(cycles)
    hf_accounts = _hf_accounts(cycles)

    result: Dict[str, HFScores] = {}
    for acct in sorted(hf_accounts):
        pnl_series = _cycle_pnl_series(cycles, marks, acct, contract.multiplier)
        total_pnl = _realized_pnl(
            fills, positions.get(acct, 0), acct, settlement, contract.multiplier,
        )
        result[acct] = HFScores(
            account_id=acct,
            total_pnl=total_pnl,
            sharpe=_sharpe(pnl_series),
            markout_1=_markout(cycles, marks, acct, 1),
            markout_5=_markout(cycles, marks, acct, 5),
            markout_20=_markout(cycles, marks, acct, 20),
        )
    return result


# ---------- helpers ----------

def _require_settlement(contract: Contract) -> float:
    if contract.settlement_value is None:
        raise ValueError(
            f"score: contract {contract.id!r} has no settlement_value set"
        )
    return float(contract.settlement_value)


def _mm_accounts(cycles: List[Cycle]) -> Set[str]:
    return {q.account_id for c in cycles for q in c.quotes}


def _hf_accounts(cycles: List[Cycle]) -> Set[str]:
    return {o.account_id for c in cycles for o in c.orders}


def _carry_mark_forward(cycles: List[Cycle]) -> List[float]:
    """Last-available mark per cycle, preferring hf_mark, carrying forward on gaps."""
    marks: List[float] = []
    current = 0.0
    for c in cycles:
        m = 0.0
        if c.state.hf_mark and c.state.hf_mark > 0:
            m = c.state.hf_mark
        elif c.state.mm_mark and c.state.mm_mark > 0:
            m = c.state.mm_mark
        if m > 0:
            current = m
        marks.append(current)
    return marks


def _cycle_pnl_series(
    cycles: List[Cycle],
    marks: List[float],
    account_id: str,
    multiplier: float,
) -> List[float]:
    """Running total PnL at the end of each cycle, mark-to-market at `marks[i]`."""
    cash = 0.0
    pos = 0
    out: List[float] = []
    for i, cycle in enumerate(cycles):
        for f in cycle.fills:
            if f.buyer_account_id == account_id:
                cash -= f.price * f.size
                pos += f.size
            if f.seller_account_id == account_id:
                cash += f.price * f.size
                pos -= f.size
        out.append((cash + pos * marks[i]) * multiplier)
    return out


def _sharpe(pnls: List[float]) -> float:
    """Sharpe = total PnL / (sqrt(N) * SD of per-cycle PnL changes).

    Equivalent to the standard mean / std Sharpe scaled by sqrt(N) — the
    annualized form, treating each cycle as one period.
    """
    if not pnls:
        return 0.0
    changes = [pnls[0]]
    for i in range(1, len(pnls)):
        changes.append(pnls[i] - pnls[i - 1])
    n = len(changes)
    if n == 0:
        return 0.0
    total = sum(changes)  # equals pnls[-1]
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


def _realized_pnl(
    fills: List[Fill],
    final_position: int,
    account_id: str,
    settlement: float,
    multiplier: float,
) -> float:
    cash = 0.0
    for f in fills:
        if f.buyer_account_id == account_id:
            cash -= f.price * f.size
        if f.seller_account_id == account_id:
            cash += f.price * f.size
    return (cash + final_position * settlement) * multiplier


def _uptime(cycles: List[Cycle], account_id: str) -> float:
    if not cycles:
        return 0.0
    quoted = sum(
        1 for c in cycles if any(q.account_id == account_id for q in c.quotes)
    )
    return quoted / len(cycles)


def _consensus(
    fills: List[Fill],
    account_id: str,
    mm_accounts: Set[str],
) -> float:
    """1 - volume_matched_with_other_MMs / total_order_volume.

    Self-trades are counted in total_order_volume but NOT as matched with
    another MM, per the spec (self-trades included).
    """
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
    cycles: List[Cycle],
    marks: List[float],
    account_id: str,
    n: int,
) -> float:
    """Size-weighted average N-cycle markout for fills involving `account_id`.

    Direction is from the account's perspective (+1 for buys, -1 for sells).
    Self-trades have net direction 0, so they contribute 0 to the numerator
    but their size still counts in the denominator.
    Fills whose target cycle (i+N) is beyond the last cycle are skipped.
    Returns 0.0 when no fills have N cycles of forward data.
    """
    total_contrib = 0.0
    total_size = 0
    for i, cycle in enumerate(cycles):
        target = i + n
        if target >= len(cycles):
            continue
        target_mark = marks[target]
        for f in cycle.fills:
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


def _avg_abs_position(cycles: List[Cycle], account_id: str) -> float:
    if not cycles:
        return 0.0
    total = sum(abs(c.positions.get(account_id, 0)) for c in cycles)
    return total / len(cycles)


def _self_crosses(fills: List[Fill], account_id: str) -> Tuple[int, int]:
    count = 0
    volume = 0
    for f in fills:
        if f.buyer_account_id == account_id and f.seller_account_id == account_id:
            count += 1
            volume += f.size
    return count, volume
