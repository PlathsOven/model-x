"""Smoke tests for the matching engine.

Run: python3 tests/test_matching.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.matching import BookLevel, match_hf_phase, match_mm_phase
from modelx.models import Order, Quote


# ---------- HF phase ----------

def test_hf_prorata_basic():
    """15/10/5 HF demand vs available 12 -> 6/4/2 (the spec example)."""
    book = [BookLevel("mm-A", "ask", 100.0, 12)]
    orders = [
        Order("o1", "c1", "k1", "hf-X", "buy", 15),
        Order("o2", "c1", "k1", "hf-Y", "buy", 10),
        Order("o3", "c1", "k1", "hf-Z", "buy", 5),
    ]
    fills, vwap = match_hf_phase(book, orders, {}, "c1", "k1")
    agg = {}
    for f in fills:
        agg[f.buyer_account_id] = agg.get(f.buyer_account_id, 0) + f.size
    assert agg == {"hf-X": 6, "hf-Y": 4, "hf-Z": 2}, agg
    assert vwap == 100.0


def test_position_limit_redistribute():
    """X capped at 1 of its 5-share alloc -> voided 4 redistributed to Y."""
    book = [BookLevel("mm-A", "ask", 100.0, 10)]
    orders = [
        Order("o1", "c1", "k1", "hf-X", "buy", 10),
        Order("o2", "c1", "k1", "hf-Y", "buy", 10),
    ]
    fills, _ = match_hf_phase(book, orders, {"hf-X": 99}, "c1", "k1")
    agg = {}
    for f in fills:
        agg[f.buyer_account_id] = agg.get(f.buyer_account_id, 0) + f.size
    assert agg == {"hf-X": 1, "hf-Y": 9}, agg


def test_floor_leftover_redistributed():
    """avail=7, demand 10/10/10 -> base [2,2,2], leftover 1 lot redistributed.

    Fractional remainders are tied (10/30 each); position-after-fill tiebreak
    is also tied (every account at 0 -> abs(0+3)=3); falls through to index,
    so the first sorted order id (o1 = hf-X) wins the leftover lot.
    """
    book = [BookLevel("mm-A", "ask", 100.0, 7)]
    orders = [
        Order("o1", "c1", "k1", "hf-X", "buy", 10),
        Order("o2", "c1", "k1", "hf-Y", "buy", 10),
        Order("o3", "c1", "k1", "hf-Z", "buy", 10),
    ]
    fills, _ = match_hf_phase(book, orders, {}, "c1", "k1")
    agg = {}
    for f in fills:
        agg[f.buyer_account_id] = agg.get(f.buyer_account_id, 0) + f.size
    assert agg == {"hf-X": 3, "hf-Y": 2, "hf-Z": 2}, agg
    assert sum(agg.values()) == 7  # nothing left unfilled


def test_user_5_2_for_3_hf():
    """Spec example (HF context): demand 5 and 2 vs ask 3 -> 2 and 1.

    Pro-rata floor gives 2 and 0; remainders 1/7 and 6/7; the 6/7 entry
    wins the leftover lot.
    """
    book = [BookLevel("mm-X", "ask", 100.0, 3)]
    orders = [
        Order("o1", "c1", "k1", "hf-A", "buy", 5),
        Order("o2", "c1", "k1", "hf-B", "buy", 2),
    ]
    fills, _ = match_hf_phase(book, orders, {}, "c1", "k1")
    agg = {}
    for f in fills:
        agg[f.buyer_account_id] = agg.get(f.buyer_account_id, 0) + f.size
    assert agg == {"hf-A": 2, "hf-B": 1}, agg


def test_book_walk():
    """A buy of 12 walks asks 5@100 + 5@101 + 2@102."""
    book = [
        BookLevel("mm-A", "ask", 100.0, 5),
        BookLevel("mm-B", "ask", 101.0, 5),
        BookLevel("mm-C", "ask", 102.0, 5),
    ]
    orders = [Order("o1", "c1", "k1", "hf-X", "buy", 12)]
    fills, _ = match_hf_phase(book, orders, {}, "c1", "k1")
    by_price = sorted(fills, key=lambda f: f.price)
    sizes = [(f.price, f.size, f.seller_account_id) for f in by_price]
    assert sizes == [
        (100.0, 5, "mm-A"),
        (101.0, 5, "mm-B"),
        (102.0, 2, "mm-C"),
    ], sizes


def test_same_price_largest_remainder():
    """Buy 7 from MM-A=6, MM-B=4 at 100 -> A:4, B:3 via largest-remainder."""
    book = [
        BookLevel("mm-A", "ask", 100.0, 6),
        BookLevel("mm-B", "ask", 100.0, 4),
    ]
    orders = [Order("o1", "c1", "k1", "hf-X", "buy", 7)]
    fills, _ = match_hf_phase(book, orders, {}, "c1", "k1")
    agg = {}
    for f in fills:
        agg[f.seller_account_id] = agg.get(f.seller_account_id, 0) + f.size
    assert agg == {"mm-A": 4, "mm-B": 3}, agg


# ---------- MM phase ----------

def test_mm_cross_simple():
    """MM-B's bid 106 lifts MM-A's ask 105 at midpoint 105.5."""
    quotes = [
        Quote("q1", "c1", "k1", "mm-A", 100, 10, 105, 10),
        Quote("q2", "c1", "k1", "mm-B", 106, 5, 110, 5),
    ]
    fills, rb, vwap = match_mm_phase(quotes, "c1", "k1")
    assert len(fills) == 1
    f = fills[0]
    assert f.buyer_account_id == "mm-B"
    assert f.seller_account_id == "mm-A"
    assert f.price == 105.5
    assert f.size == 5
    rb_set = {(lvl.account_id, lvl.side, lvl.price, lvl.size) for lvl in rb}
    assert rb_set == {
        ("mm-A", "bid", 100, 10),
        ("mm-A", "ask", 105, 5),
        ("mm-B", "ask", 110, 5),
    }, rb_set


def test_mm_matching_order_aggressive_first():
    """Two-MM encoding of the spec example: bids 1@50, 2@40; asks 2@35, 1@45.

    Most-aggressive cross first: bid 50 vs ask 35 at midpoint 42.5 (1 lot),
    then bid 40 vs the remaining ask 35 at midpoint 37.5 (1 lot). After
    that, bid 40 vs ask 45 doesn't cross. Residual: 1@40 bid + 1@45 ask.
    """
    # MM2 holds the inverted side (bid 50 / ask 35) so that the spec's
    # "1@50 bid" and "2@35 ask" both belong to one MM. The first fill is
    # therefore a self-cross (recorded, not blocked).
    quotes = [
        Quote("q1", "c1", "k1", "mm1", 40, 2, 45, 1),
        Quote("q2", "c1", "k1", "mm2", 50, 1, 35, 2),
    ]
    fills, rb, _ = match_mm_phase(quotes, "c1", "k1")

    assert len(fills) == 2, fills

    # Most aggressive (highest midpoint) must come first.
    assert fills[0].price == 42.5, fills
    assert fills[0].size == 1
    assert fills[0].buyer_account_id == "mm2"
    assert fills[0].seller_account_id == "mm2"  # self-cross of the inverted quote

    assert fills[1].price == 37.5, fills
    assert fills[1].size == 1
    assert fills[1].buyer_account_id == "mm1"
    assert fills[1].seller_account_id == "mm2"

    rb_set = {(lvl.account_id, lvl.side, lvl.price, lvl.size) for lvl in rb}
    assert rb_set == {
        ("mm1", "bid", 40, 1),
        ("mm1", "ask", 45, 1),
    }, rb_set


def test_mm_self_cross_recorded():
    """A purely inverted single-MM quote produces a buyer==seller fill."""
    quotes = [
        Quote("q1", "c1", "k1", "mm-X", 50, 5, 45, 5),
    ]
    fills, rb, _ = match_mm_phase(quotes, "c1", "k1")
    assert len(fills) == 1
    f = fills[0]
    assert f.buyer_account_id == "mm-X"
    assert f.seller_account_id == "mm-X"
    assert f.price == 47.5
    assert f.size == 5
    assert rb == []


def test_user_5_2_for_3_mm():
    """Spec example (MM context): MM-A bid 5 + MM-B bid 2 vs MM-C ask 3 -> 2 and 1."""
    quotes = [
        Quote("qa", "c1", "k1", "mm-A", 50, 5, 999, 0),
        Quote("qb", "c1", "k1", "mm-B", 50, 2, 999, 0),
        Quote("qc", "c1", "k1", "mm-C", 0,  0, 50,  3),
    ]
    fills, rb, _ = match_mm_phase(quotes, "c1", "k1")
    buys = {}
    for f in fills:
        buys[f.buyer_account_id] = buys.get(f.buyer_account_id, 0) + f.size
        assert f.seller_account_id == "mm-C"
        assert f.price == 50
    assert buys == {"mm-A": 2, "mm-B": 1}, buys
    rb_set = {(lvl.account_id, lvl.side, lvl.price, lvl.size) for lvl in rb}
    assert rb_set == {
        ("mm-A", "bid", 50, 3),  # 5 - 2 = 3 unfilled
        ("mm-B", "bid", 50, 1),  # 2 - 1 = 1 unfilled
    }, rb_set


def test_mm_position_tiebreak():
    """Equal weights + equal fractional remainders; closer-to-flat MM wins.

    Both MM-A and MM-B bid 3 at 50; MM-C asks 5 at 50. V = 5; pro-rata floor
    gives base [2, 2] with 1 leftover lot (frac 3/6 each — tied). MM-A starts
    at 0; MM-B starts at +10. After +1, abs positions would be 3 vs 13, so
    MM-A gets the leftover -> A=3, B=2.
    """
    quotes = [
        Quote("qa", "c1", "k1", "mm-A", 50, 3, 999, 0),
        Quote("qb", "c1", "k1", "mm-B", 50, 3, 999, 0),
        Quote("qc", "c1", "k1", "mm-C", 0,  0, 50,  5),
    ]
    fills, _, _ = match_mm_phase(
        quotes, "c1", "k1", positions={"mm-A": 0, "mm-B": 10},
    )
    buys = {}
    for f in fills:
        buys[f.buyer_account_id] = buys.get(f.buyer_account_id, 0) + f.size
    assert buys == {"mm-A": 3, "mm-B": 2}, buys

    # Inverse: MM-B is the one closer to flat -> MM-B wins the leftover.
    fills2, _, _ = match_mm_phase(
        quotes, "c1", "k1", positions={"mm-A": 10, "mm-B": -3},
    )
    buys2 = {}
    for f in fills2:
        buys2[f.buyer_account_id] = buys2.get(f.buyer_account_id, 0) + f.size
    # MM-B at -3 + 3 = 0 (abs 0) beats MM-A at 10 + 3 = 13 (abs 13).
    assert buys2 == {"mm-A": 2, "mm-B": 3}, buys2


def test_mm_self_cross_does_not_block_real_cross():
    """An MM with a self-crossing inverted quote still trades against others."""
    # MM-X self-crosses 5 lots; MM-Y is willing to sell 3 at 48 to MM-X's 50 bid.
    quotes = [
        Quote("q1", "c1", "k1", "mm-X", 50, 5, 45, 5),  # inverted; self-trades
        Quote("q2", "c1", "k1", "mm-Y", 30, 0, 48, 3),  # ask only
    ]
    fills, rb, _ = match_mm_phase(quotes, "c1", "k1")

    # Most aggressive cross is X-bid-50 vs X-ask-45 (midpoint 47.5).
    # That cross has 5 bid vs 5 ask but Y's 48 ask is also at the
    # ask-best level? No: best_ask = min(45, 48) = 45 -> only X's ask.
    # So round 1 fully consumes X's bid (5) and X's ask (5).
    # Round 2: best_bid = none (X bid exhausted), Y's ask 48 sits unmatched.
    self_fills = [f for f in fills if f.buyer_account_id == f.seller_account_id]
    cross_fills = [f for f in fills if f.buyer_account_id != f.seller_account_id]
    assert sum(f.size for f in self_fills) == 5
    assert sum(f.size for f in cross_fills) == 0
    rb_set = {(lvl.account_id, lvl.side, lvl.price, lvl.size) for lvl in rb}
    assert rb_set == {("mm-Y", "ask", 48, 3)}, rb_set


TESTS = [
    test_hf_prorata_basic,
    test_position_limit_redistribute,
    test_floor_leftover_redistributed,
    test_user_5_2_for_3_hf,
    test_book_walk,
    test_same_price_largest_remainder,
    test_mm_cross_simple,
    test_mm_matching_order_aggressive_first,
    test_user_5_2_for_3_mm,
    test_mm_position_tiebreak,
    test_mm_self_cross_recorded,
    test_mm_self_cross_does_not_block_real_cross,
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
