#!/usr/bin/env python3
"""ModelX market settlement CLI.

Settles one PENDING_SETTLEMENT market by writing the supplied settlement
value to its contract row, computing per-account MM and HF scores, writing
one `agent_lifetime_stats` row per participant, and printing a final P&L
table.

Usage:
    python3 settle.py --db modelx.db --market cpi-yoy-may-2025 --value 2.8
"""

import argparse
import os
import sys
import time

# Make the modelx package importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelx.cycle import load_cycle
from modelx.db import (
    connect,
    get_contract,
    get_market,
    list_accounts_for_market,
    list_cycle_states,
    list_fills_by_contract,
    positions_for_contract,
    update_market_progress,
    upsert_contract,
    upsert_lifetime_stat,
)
from modelx.models import LifetimeStat
from modelx.scoring import score_hf, score_mm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="modelx.db", help="SQLite db path")
    parser.add_argument(
        "--market", required=True,
        help="market id from markets.yaml (e.g. cpi-yoy-may-2025)",
    )
    parser.add_argument(
        "--value", type=float, required=True,
        help="settlement value (the realized real-world quantity)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="settle even if the market is not in PENDING_SETTLEMENT state",
    )
    args = parser.parse_args()

    db = connect(args.db)

    market = get_market(db, args.market)
    if market is None:
        _die(f"market {args.market!r} not found in {args.db}")
    if market.state != "PENDING_SETTLEMENT" and not args.force:
        _die(
            f"market {args.market!r} is in state {market.state!r}, "
            f"not PENDING_SETTLEMENT (use --force to override)"
        )

    contract = get_contract(db, market.id)
    if contract is None:
        _die(f"contract row {market.id!r} missing — db is in an inconsistent state")

    # Persist the settlement value on the contract row first so the scoring
    # functions (which call _require_settlement) succeed.
    contract.settlement_value = float(args.value)
    contract.settled_at = time.time()
    upsert_contract(db, contract)

    # Reconstruct cycles + fills + positions for scoring.
    cycle_states = list_cycle_states(db, contract.id)
    cycles = [load_cycle(db, contract, cs.id) for cs in cycle_states]
    fills = list_fills_by_contract(db, contract.id)
    positions = positions_for_contract(db, contract.id)

    mm_scores = score_mm(fills, cycles, positions, contract)
    hf_scores = score_hf(fills, cycles, positions, contract)

    # Persist a lifetime stat row per participant. We write one row per
    # account that traded in this market, including HFs (which are tracked
    # separately from MMs in the score dicts).
    settled_at = contract.settled_at
    for acct, s in mm_scores.items():
        upsert_lifetime_stat(db, LifetimeStat(
            account_id=acct,
            market_id=market.id,
            role="MM",
            total_pnl=s.total_pnl,
            sharpe=s.sharpe,
            volume=s.volume,
            settled_at=settled_at,
        ))
    for acct, s in hf_scores.items():
        # HF scores don't include volume — recompute from fills.
        vol = sum(
            f.size for f in fills
            if f.buyer_account_id == acct or f.seller_account_id == acct
        )
        upsert_lifetime_stat(db, LifetimeStat(
            account_id=acct,
            market_id=market.id,
            role="HF",
            total_pnl=s.total_pnl,
            sharpe=s.sharpe,
            volume=vol,
            settled_at=settled_at,
        ))

    # Mark the market as SETTLED.
    market.state = "SETTLED"
    update_market_progress(
        db, market.id, market.state, market.current_cycle, market.pending_mm,
    )

    _print_summary(market.id, args.value, contract.multiplier, mm_scores, hf_scores)


def _print_summary(market_id, value, multiplier, mm_scores, hf_scores):
    print()
    print("=" * 70)
    print(f"SETTLED  market={market_id}  value={value}  multiplier={multiplier}")
    print("=" * 70)
    if mm_scores:
        print()
        print(f"{'Account':<40} {'PnL':>14} {'Sharpe':>10} {'Volume':>10}")
        print("-" * 76)
        for acct, s in sorted(mm_scores.items()):
            print(
                f"{acct:<40} {s.total_pnl:>14.4f} "
                f"{s.sharpe:>10.4f} {s.volume:>10}  [MM]"
            )
    if hf_scores:
        if not mm_scores:
            print()
            print(f"{'Account':<40} {'PnL':>14} {'Sharpe':>10} {'Volume':>10}")
            print("-" * 76)
        for acct, s in sorted(hf_scores.items()):
            print(
                f"{acct:<40} {s.total_pnl:>14.4f} "
                f"{s.sharpe:>10.4f} {'-':>10}  [HF]"
            )
    print()
    print(f"Lifetime stats written to agent_lifetime_stats for market {market_id}.")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
