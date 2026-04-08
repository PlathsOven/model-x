"""Matching engine: MM-MM crosses and HF market orders against the residual book."""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .models import Fill, Order, Quote


@dataclass
class BookLevel:
    """Liquidity from one MM, on one side, at one price.

    Used as both the canonical residual-book output of the MM phase and
    the input to the HF phase.
    """
    account_id: str
    side: str   # "bid" or "ask"
    price: float
    size: int


# ---------- MM phase ----------

def match_mm_phase(
    quotes: List[Quote],
    cycle_id: str,
    contract_id: str,
    positions: Optional[Dict[str, int]] = None,
) -> Tuple[List[Fill], List[BookLevel], float]:
    """Cross sealed MM quotes against each other.

    Each MM submits at most one Quote per cycle (one bid/ask pair per
    account). Caller is responsible for that uniqueness — the engine
    treats each Quote as a distinct account.

    Algorithm:
      while a cross exists between any two MMs, take the level
      (best_bid_price, best_ask_price), gather every MM on each side at
      that exact price, allocate `V = min(sum_bid, sum_ask)` to each side
      with `_largest_remainder` (so the floor leftover is allocated, not
      dropped), two-pointer pair the allocations into fills at the midpoint,
      reduce remaining sizes, repeat.

    The largest-remainder tiebreaker prefers the taker whose absolute
    inventory after the marginal lot would be smallest, so leftover lots
    flow toward MMs whose fills bring them closer to flat. `positions`
    is the snapshot of every account's inventory entering this MM phase;
    the function does not mutate it.

    Mark-to-market = VWAP of the residual orderbook.

    Self-trades are NOT prevented. An MM whose own bid crosses its own ask
    (inverted quote), or whose two sides land in the same pro-rata pairing,
    produces Fills with buyer_account_id == seller_account_id. Downstream
    scoring counts these in consensus and PnL bps just like any other fill —
    bad reasoning is punished by P&L, not by the engine.

    Returns:
        fills: MM-MM crosses (Fill.id == ""; caller assigns real ids)
        remaining_book: unmatched bid/ask levels
        vwap_mark: VWAP of remaining_book (0.0 if empty)
    """
    bid_rem: Dict[str, int] = {q.id: q.bid_size for q in quotes}
    ask_rem: Dict[str, int] = {q.id: q.ask_size for q in quotes}
    fills: List[Fill] = []
    proj_pos: Dict[str, int] = dict(positions) if positions else {}

    while True:
        active_bids = [q for q in quotes if bid_rem.get(q.id, 0) > 0]
        active_asks = [q for q in quotes if ask_rem.get(q.id, 0) > 0]
        if not active_bids or not active_asks:
            break

        best_bid = max(q.bid_price for q in active_bids)
        best_ask = min(q.ask_price for q in active_asks)
        if best_bid < best_ask:
            break

        midpoint = (best_bid + best_ask) / 2.0

        bid_pool = [(q, bid_rem[q.id]) for q in active_bids if q.bid_price == best_bid]
        ask_pool = [(q, ask_rem[q.id]) for q in active_asks if q.ask_price == best_ask]

        sum_bid = sum(s for _, s in bid_pool)
        sum_ask = sum(s for _, s in ask_pool)
        V = min(sum_bid, sum_ask)
        if V == 0:
            break

        # Largest-remainder allocation on each side. Leftover lots from
        # flooring are distributed by fractional remainder, with a tiebreak
        # that prefers MMs whose absolute position after the marginal lot
        # would be smallest.
        bid_szs = [s for _, s in bid_pool]
        ask_szs = [s for _, s in ask_pool]

        def _bid_tb(i: int, base: int) -> int:
            acct = bid_pool[i][0].account_id
            return abs(proj_pos.get(acct, 0) + base + 1)

        def _ask_tb(i: int, base: int) -> int:
            acct = ask_pool[i][0].account_id
            return abs(proj_pos.get(acct, 0) - base - 1)

        bid_amts = _largest_remainder(bid_szs, V, _bid_tb)
        ask_amts = _largest_remainder(ask_szs, V, _ask_tb)

        bid_alloc = [(bid_pool[i][0], a) for i, a in enumerate(bid_amts) if a > 0]
        ask_alloc = [(ask_pool[i][0], a) for i, a in enumerate(ask_amts) if a > 0]
        if not bid_alloc or not ask_alloc:
            break

        # Two-pointer pairing — both sides sum to V, so we always exhaust both.
        bi = ai = 0
        bq, bsz = bid_alloc[0]
        aq, asz = ask_alloc[0]
        progress = False

        while bi < len(bid_alloc) and ai < len(ask_alloc):
            match = min(bsz, asz)
            fills.append(Fill(
                id="",
                cycle_id=cycle_id,
                contract_id=contract_id,
                buyer_account_id=bq.account_id,
                seller_account_id=aq.account_id,
                price=midpoint,
                size=match,
                phase="MM",
            ))
            bid_rem[bq.id] -= match
            ask_rem[aq.id] -= match
            proj_pos[bq.account_id] = proj_pos.get(bq.account_id, 0) + match
            proj_pos[aq.account_id] = proj_pos.get(aq.account_id, 0) - match
            bsz -= match
            asz -= match
            progress = True

            if bsz == 0:
                bi += 1
                if bi < len(bid_alloc):
                    bq, bsz = bid_alloc[bi]
            if asz == 0:
                ai += 1
                if ai < len(ask_alloc):
                    aq, asz = ask_alloc[ai]

        if not progress:
            break  # safety: never spin

    # Build residual orderbook.
    remaining_book: List[BookLevel] = []
    for q in quotes:
        if bid_rem.get(q.id, 0) > 0:
            remaining_book.append(BookLevel(q.account_id, "bid", q.bid_price, bid_rem[q.id]))
        if ask_rem.get(q.id, 0) > 0:
            remaining_book.append(BookLevel(q.account_id, "ask", q.ask_price, ask_rem[q.id]))

    total_size = sum(lvl.size for lvl in remaining_book)
    vwap_mark = (
        sum(lvl.price * lvl.size for lvl in remaining_book) / total_size
        if total_size > 0
        else 0.0
    )

    return fills, remaining_book, vwap_mark


# ---------- HF phase helpers ----------

def _allocate_pool_with_limits(
    demand: Dict[str, int],
    available: int,
    order_to_account: Dict[str, str],
    pos_state: Dict[str, int],
    limit: int,
    side: str,  # "buy" or "sell"
) -> Dict[str, int]:
    """Largest-remainder allocate `available` across HF orders at one price.

    Each round allocates the available pool exactly via `_largest_remainder`
    (so leftover lots from flooring are distributed by fractional remainder,
    with a tiebreak that prefers orders whose absolute account position
    after the marginal lot would be smallest). Position limits then cap
    each order; voided units carry into the next round and are redistributed
    among remaining eligible orders.

    `pos_state` is the caller's current view, already reflecting fills from
    earlier price levels in this HF phase. The function does not mutate it;
    it operates on a private `proj_pos` copy.

    Returns: dict[order_id -> filled_at_this_level].
    """
    eligible = {oid: d for oid, d in demand.items() if d > 0}
    fills_at_level: Dict[str, int] = {oid: 0 for oid in demand}
    avail = available
    sign = 1 if side == "buy" else -1

    # Position projection carried across redistribution rounds: an order
    # that filled some units in round 1 (and still has remaining demand)
    # uses the post-fill position for round 2's tiebreaker and cap.
    proj_pos = dict(pos_state)

    while avail > 0 and eligible:
        total_d = sum(eligible.values())
        if total_d == 0:
            break

        oids = sorted(eligible.keys())
        weights = [eligible[oid] for oid in oids]

        def _hf_tb(i: int, base: int) -> int:
            acct = order_to_account[oids[i]]
            return abs(proj_pos.get(acct, 0) + sign * (base + 1))

        # Largest-remainder fully allocates min(avail, total_d) — no leftover.
        allocs = _largest_remainder(weights, min(avail, total_d), _hf_tb)

        new_eligible: Dict[str, int] = {}
        voided = 0
        any_progress = False

        for i, oid in enumerate(oids):
            alloc = allocs[i]
            if alloc == 0:
                # Keep eligible in case redistribution opens room next round.
                new_eligible[oid] = eligible[oid]
                continue

            acct = order_to_account[oid]
            cur = proj_pos.get(acct, 0)
            cap = (limit - cur) if side == "buy" else (limit + cur)
            cap = max(0, cap)

            actual = min(alloc, cap)
            if actual > 0:
                fills_at_level[oid] += actual
                proj_pos[acct] = cur + sign * actual
                avail -= actual
                any_progress = True

            if actual < alloc:
                voided += (alloc - actual)
                # Account hit its limit — exclude from this level entirely.
            else:
                rem = eligible[oid] - actual
                if rem > 0:
                    new_eligible[oid] = rem

        if voided == 0 or not any_progress:
            break
        eligible = new_eligible
        # `avail` already equals `voided` here (we decremented by actuals,
        # and largest-remainder consumed the rest as voided).

    return fills_at_level


def _largest_remainder(
    weights: List[int],
    target: int,
    tiebreak: Optional[Callable[[int, int], int]] = None,
) -> List[int]:
    """Allocate exactly `target` units across positions in `weights`.

    Floors each share, then distributes the rounding remainder one lot at
    a time to positions with the largest fractional remainder. Ties on
    fractional remainder are broken by `tiebreak(i, base[i])` ascending
    (smaller value = higher priority); ties on tiebreak are broken by index.

    Used as the universal pro-rata floor distributor across the engine: MM
    bid pool, MM ask pool, HF demand pool, and the HF book side. The
    invariant `sum(result) == min(target, total_w)` always holds, so floor
    leftover from rounding is never dropped.
    """
    n = len(weights)
    total_w = sum(weights)
    if total_w == 0 or target == 0:
        return [0] * n
    if target >= total_w:
        return list(weights)

    base = [(target * w) // total_w for w in weights]
    fracs: List[Tuple[int, int, int]] = []  # (-frac, tiebreak, index)
    for i, w in enumerate(weights):
        frac_num = (target * w) - (base[i] * total_w)
        tb = tiebreak(i, base[i]) if tiebreak is not None else 0
        fracs.append((frac_num, tb, i))

    remainder = target - sum(base)
    fracs.sort(key=lambda x: (-x[0], x[1], x[2]))
    for k in range(remainder):
        _, _, idx = fracs[k]
        # Defensive cap; with target < total_w and w > 0, base[idx] < weights[idx].
        if base[idx] < weights[idx]:
            base[idx] += 1
    return base


# ---------- HF phase ----------

def match_hf_phase(
    book: List[BookLevel],
    orders: List[Order],
    positions: Dict[str, int],
    cycle_id: str,
    contract_id: str,
    position_limit: int = 100,
) -> Tuple[List[Fill], float]:
    """Match HF market orders against the residual orderbook.

    Each HF submits at most one Order per cycle — a single market order
    (side + size) or pass (no Order at all). Caller is responsible for
    that uniqueness; the engine treats each Order as a distinct account.

    All HF orders are processed simultaneously — no time priority. At each
    price level: largest-remainder across HFs (with cumulative position
    limits; void+redistribute on limit hit; floor leftover allocated by
    fractional remainder), then largest-remainder across book entries at
    the same price, then two-pointer pair into Fill records. Walks the book
    from best to worst, level by level.

    `positions` is the snapshot of every account's position going into the
    HF phase. The function takes a copy and does NOT mutate the input.

    Mark-to-market = VWAP of HF fills (0.0 if no fills).

    Returns:
        fills: HF fills (Fill.id == ""; caller assigns real ids)
        vwap_mark: VWAP of HF fills
    """
    fills: List[Fill] = []
    pos = dict(positions)

    asks = sorted([lvl for lvl in book if lvl.side == "ask"], key=lambda l: l.price)
    bids = sorted([lvl for lvl in book if lvl.side == "bid"], key=lambda l: -l.price)

    # One mutable size map covering every BookLevel in `book`. Both `asks`
    # and `bids` reference the same objects, so id()-keyed access is consistent.
    book_rem: Dict[int, int] = {id(lvl): lvl.size for lvl in book}

    buy_orders = [o for o in orders if o.side == "buy" and o.size > 0]
    sell_orders = [o for o in orders if o.side == "sell" and o.size > 0]

    fills.extend(_walk_book(
        buy_orders, asks, book_rem, pos, position_limit, "buy",
        cycle_id, contract_id,
    ))
    fills.extend(_walk_book(
        sell_orders, bids, book_rem, pos, position_limit, "sell",
        cycle_id, contract_id,
    ))

    total_size = sum(f.size for f in fills)
    vwap = (
        sum(f.price * f.size for f in fills) / total_size
        if total_size > 0
        else 0.0
    )
    return fills, vwap


def _walk_book(
    orders: List[Order],
    sorted_book: List[BookLevel],
    book_rem: Dict[int, int],
    pos: Dict[str, int],
    limit: int,
    side: str,
    cycle_id: str,
    contract_id: str,
) -> List[Fill]:
    """Walk price levels of `sorted_book` (already best-to-worst), filling HFs."""
    fills: List[Fill] = []
    if not orders or not sorted_book:
        return fills

    rem_demand = {o.id: o.size for o in orders}
    order_to_account = {o.id: o.account_id for o in orders}

    i = 0
    while i < len(sorted_book) and any(d > 0 for d in rem_demand.values()):
        cur_price = sorted_book[i].price
        level: List[BookLevel] = []
        j = i
        while j < len(sorted_book) and sorted_book[j].price == cur_price:
            if book_rem[id(sorted_book[j])] > 0:
                level.append(sorted_book[j])
            j += 1
        i = j

        if not level:
            continue

        available = sum(book_rem[id(lvl)] for lvl in level)
        if available == 0:
            continue

        active_demand = {oid: d for oid, d in rem_demand.items() if d > 0}
        if not active_demand:
            break

        hf_fills = _allocate_pool_with_limits(
            active_demand, available, order_to_account, pos, limit, side,
        )

        total_filled = sum(hf_fills.values())
        if total_filled == 0:
            continue  # every eligible HF at this level was capped

        # Reduce HF demand. Position updates happen in the pairing loop so
        # MM positions get tracked symmetrically.
        for oid, amt in hf_fills.items():
            if amt > 0:
                rem_demand[oid] -= amt

        # Largest-remainder allocate `total_filled` across the book entries
        # at this level. Tiebreak prefers the MM whose position after the
        # marginal lot would be smallest in absolute value (HF buying = MM
        # selling, position decreases; HF selling = MM buying, increases).
        level_weights = [book_rem[id(lvl)] for lvl in level]
        mm_sign = -1 if side == "buy" else 1

        def _book_tb(i: int, base: int) -> int:
            mm_acct = level[i].account_id
            return abs(pos.get(mm_acct, 0) + mm_sign * (base + 1))

        book_allocs = _largest_remainder(level_weights, total_filled, _book_tb)

        for lvl, amt in zip(level, book_allocs):
            book_rem[id(lvl)] -= amt

        # Two-pointer pair HF fills with book allocations into Fill records.
        hf_pairs = [
            (oid, amt) for oid, amt in sorted(hf_fills.items()) if amt > 0
        ]
        book_pairs = [
            (lvl, amt) for lvl, amt in zip(level, book_allocs) if amt > 0
        ]
        if not hf_pairs or not book_pairs:
            continue

        bi = ai = 0
        h_oid, h_amt = hf_pairs[0]
        b_lvl, b_amt = book_pairs[0]
        while bi < len(hf_pairs) and ai < len(book_pairs):
            match = min(h_amt, b_amt)
            hf_acct = order_to_account[h_oid]
            book_acct = b_lvl.account_id
            if side == "buy":
                buyer, seller = hf_acct, book_acct
            else:
                buyer, seller = book_acct, hf_acct
            fills.append(Fill(
                id="",
                cycle_id=cycle_id,
                contract_id=contract_id,
                buyer_account_id=buyer,
                seller_account_id=seller,
                price=cur_price,
                size=match,
                phase="HF",
            ))
            pos[buyer] = pos.get(buyer, 0) + match
            pos[seller] = pos.get(seller, 0) - match
            h_amt -= match
            b_amt -= match
            if h_amt == 0:
                bi += 1
                if bi < len(hf_pairs):
                    h_oid, h_amt = hf_pairs[bi]
            if b_amt == 0:
                ai += 1
                if ai < len(book_pairs):
                    b_lvl, b_amt = book_pairs[ai]

    return fills
