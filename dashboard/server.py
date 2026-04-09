"""ModelX debug dashboard — read-only FastAPI backend.

Reads a SQLite db (produced by `run_demo.py --db ...`) and a reasoning-traces
JSON file (produced by `run_demo.py --traces ...`) and exposes endpoints that
power the dashboard frontend. Never writes to either source.

Run:
    python server.py --db ../modelx.db --traces ../episode_traces.json --port 8000
"""

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Make the sibling `modelx` package importable. Mirrors run_demo.py's approach.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from modelx.cycle import Cycle, load_cycle
from modelx.db import (
    connect,
    get_contract,
    list_accounts,
    list_cycle_states,
    list_fills_by_contract,
    positions_before_cycle,
    positions_for_contract,
)
from modelx.matching import BookLevel, match_mm_phase
from modelx.models import Account, Contract, CycleState, Fill
from modelx.scoring import (
    HFScores,
    MMScores,
    _carry_mark_forward,
    score_hf,
    score_mm,
)


# ---------- CLI / config ----------

@dataclass
class Config:
    db_path: str
    traces_path: str
    port: int


CONFIG: Config = Config(db_path="modelx.db", traces_path="episode_traces.json", port=8000)


# ---------- AppState ----------

@dataclass
class AppState:
    """Everything the dashboard needs, loaded from disk and refreshed on
    file-mtime change. All data fields default to empty so a state can
    represent "no data yet" without crashing endpoints."""
    contract: Optional[Contract] = None
    accounts: List[Account] = field(default_factory=list)
    cycle_states: List[CycleState] = field(default_factory=list)
    cycles: List[Cycle] = field(default_factory=list)
    all_fills: List[Fill] = field(default_factory=list)
    positions: Dict[str, int] = field(default_factory=dict)
    marks: List[float] = field(default_factory=list)
    residual_books: Dict[int, List[BookLevel]] = field(default_factory=dict)
    traces: Optional[Dict[str, Any]] = None
    traces_loaded: bool = False
    db_path: str = ""
    traces_path: str = ""
    # Loading status — frontend uses these to render waiting / live screens.
    loaded: bool = False
    status: str = "db_missing"  # "ok" | "db_missing" | "no_contracts" | "error"
    status_detail: Optional[str] = None
    db_mtime: float = 0.0
    traces_mtime: float = 0.0
    loaded_at: float = 0.0  # epoch seconds; bumps on every successful reload


STATE: Optional[AppState] = None
_RELOAD_LOCK = threading.Lock()


def _file_mtime(path: str) -> float:
    """0.0 if path doesn't exist; mtime otherwise. Used as a cheap change check."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _empty_state(
    db_path: str,
    traces_path: str,
    status: str,
    detail: Optional[str] = None,
    db_mtime: float = 0.0,
    traces_mtime: float = 0.0,
) -> AppState:
    """Build a sentinel state with empty data for the unloaded case."""
    return AppState(
        db_path=db_path,
        traces_path=traces_path,
        loaded=False,
        status=status,
        status_detail=detail,
        db_mtime=db_mtime,
        traces_mtime=traces_mtime,
        loaded_at=time.time(),
    )


def _load_traces(traces_path: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Read the traces JSON if it exists. Returns (parsed, loaded_flag).
    Failures (malformed JSON, partial write) are swallowed and treated as
    'not loaded' so the rest of the dashboard still works."""
    if not traces_path or not os.path.exists(traces_path):
        return None, False
    try:
        with open(traces_path) as f:
            return json.load(f), True
    except (OSError, ValueError):
        return None, False


def _load_state(db_path: str, traces_path: str) -> AppState:
    """Open the db, pick the first contract, rebuild cycles, load traces.

    Never raises. On any failure (missing db, no contracts, malformed db,
    etc.) returns a sentinel AppState with `loaded=False` and a `status`
    that the frontend renders as a friendly waiting screen.
    """
    db_mtime = _file_mtime(db_path)
    traces_mtime = _file_mtime(traces_path)

    if not os.path.exists(db_path):
        return _empty_state(
            db_path, traces_path,
            status="db_missing",
            detail=f"db file not found at {db_path}",
            db_mtime=db_mtime,
            traces_mtime=traces_mtime,
        )

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = connect(db_path)

        # Pick the single contract in the db (run_demo.py only writes one).
        row = conn.execute(
            "SELECT id FROM contracts ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            # Traces may still load even if the db has no contracts yet — keep
            # the partial info for the Reasoning view if the user previously
            # generated traces against a different db path.
            traces, traces_loaded = _load_traces(traces_path)
            return AppState(
                db_path=db_path,
                traces_path=traces_path,
                loaded=False,
                status="no_contracts",
                status_detail="db exists but contains no contracts yet",
                db_mtime=db_mtime,
                traces_mtime=traces_mtime,
                loaded_at=time.time(),
                traces=traces,
                traces_loaded=traces_loaded,
            )

        contract = get_contract(conn, row["id"])
        if contract is None:
            return _empty_state(
                db_path, traces_path,
                status="error",
                detail=f"contract {row['id']} disappeared after initial lookup",
                db_mtime=db_mtime,
                traces_mtime=traces_mtime,
            )

        accounts = list_accounts(conn)
        cycle_states = list_cycle_states(conn, contract.id)

        # Reconstruct full Cycle objects so we can call score_mm/score_hf.
        cycles: List[Cycle] = [
            load_cycle(conn, contract, cs.id) for cs in cycle_states
        ]

        all_fills = list_fills_by_contract(conn, contract.id)
        positions = positions_for_contract(conn, contract.id)
        marks = _carry_mark_forward(cycles) if cycles else []

        # For every cycle, derive the residual book the HFs saw — always
        # recompute, never trust load_cycle's conditional population.
        residual_books: Dict[int, List[BookLevel]] = {}
        for cs, cyc in zip(cycle_states, cycles):
            entering = positions_before_cycle(conn, contract.id, cs.cycle_index)
            _, book, _ = match_mm_phase(
                cyc.quotes,
                cycle_id=cs.id,
                contract_id=contract.id,
                positions=entering,
            )
            residual_books[cs.cycle_index] = book

        traces, traces_loaded = _load_traces(traces_path)

        return AppState(
            contract=contract,
            accounts=accounts,
            cycle_states=cycle_states,
            cycles=cycles,
            all_fills=all_fills,
            positions=positions,
            marks=marks,
            residual_books=residual_books,
            traces=traces,
            traces_loaded=traces_loaded,
            db_path=db_path,
            traces_path=traces_path,
            loaded=True,
            status="ok",
            status_detail=None,
            db_mtime=db_mtime,
            traces_mtime=traces_mtime,
            loaded_at=time.time(),
        )
    except Exception as e:
        # Any unexpected failure (corrupt db, missing schema, partial write
        # mid-commit, etc.) — surface as an error sentinel rather than crash.
        traceback.print_exc()
        return _empty_state(
            db_path, traces_path,
            status="error",
            detail=f"{type(e).__name__}: {e}",
            db_mtime=db_mtime,
            traces_mtime=traces_mtime,
        )
    finally:
        if conn is not None:
            conn.close()


def _state() -> AppState:
    """Return the current state, reloading from disk if file mtimes have
    changed. Called by every endpoint — the mtime check short-circuits in
    microseconds when nothing has changed."""
    global STATE
    db_mtime = _file_mtime(CONFIG.db_path)
    traces_mtime = _file_mtime(CONFIG.traces_path)
    if (
        STATE is None
        or db_mtime != STATE.db_mtime
        or traces_mtime != STATE.traces_mtime
    ):
        with _RELOAD_LOCK:
            # Re-check inside the lock so concurrent requests don't all reload.
            db_mtime = _file_mtime(CONFIG.db_path)
            traces_mtime = _file_mtime(CONFIG.traces_path)
            if (
                STATE is None
                or db_mtime != STATE.db_mtime
                or traces_mtime != STATE.traces_mtime
            ):
                STATE = _load_state(CONFIG.db_path, CONFIG.traces_path)
    return STATE  # type: ignore[return-value]


# ---------- serialization helpers ----------

def _cycle_state_dict(cs: CycleState, info: Optional[str], mark: float, cyc: Cycle) -> dict:
    mm_fills = sum(1 for f in cyc.fills if f.phase == "MM")
    hf_fills = sum(1 for f in cyc.fills if f.phase == "HF")
    return {
        "cycle_index": cs.cycle_index,
        "cycle_id": cs.id,
        "phase": cs.phase,
        "mm_mark": cs.mm_mark,
        "hf_mark": cs.hf_mark,
        "mark": mark if mark > 0 else None,
        "num_quotes": len(cyc.quotes),
        "num_orders": len(cyc.orders),
        "mm_fills": mm_fills,
        "hf_fills": hf_fills,
        "info": info,
    }


def _fill_dict(f: Fill, cycle_index_by_id: Dict[str, int]) -> dict:
    return {
        "id": f.id,
        "cycle_index": cycle_index_by_id.get(f.cycle_id, -1),
        "cycle_id": f.cycle_id,
        "phase": f.phase,
        "buyer": f.buyer_account_id,
        "seller": f.seller_account_id,
        "price": f.price,
        "size": f.size,
        "is_self_cross": f.buyer_account_id == f.seller_account_id,
    }


def _book_level_dict(bl: BookLevel) -> dict:
    return {
        "account_id": bl.account_id,
        "side": bl.side,
        "price": bl.price,
        "size": bl.size,
    }


def _info_by_cycle(state: AppState) -> Dict[int, str]:
    """Extract {cycle_index: info_text} from traces, normalizing schedules
    that store either a single string or a list of strings per cycle."""
    if not state.traces:
        return {}
    raw = state.traces.get("info_schedule", {}) or {}
    out: Dict[int, str] = {}
    for k, v in raw.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, list):
            out[idx] = "\n".join(str(x) for x in v)
        else:
            out[idx] = str(v)
    return out


# ---------- scoring wrapper ----------

def _partial_mm_scores(state: AppState) -> Dict[str, dict]:
    """Settlement-independent MM metrics, computed the same way scoring.py does
    but without the settlement-dependent fields."""
    mm_accounts = {q.account_id for c in state.cycles for q in c.quotes}
    total_volume = sum(f.size for f in state.all_fills)
    result: Dict[str, dict] = {}
    for acct in sorted(mm_accounts):
        volume = sum(
            f.size for f in state.all_fills
            if f.buyer_account_id == acct or f.seller_account_id == acct
        )
        # Uptime
        quoted = sum(
            1 for c in state.cycles if any(q.account_id == acct for q in c.quotes)
        )
        uptime = quoted / len(state.cycles) if state.cycles else 0.0
        # Consensus
        total = 0
        with_other = 0
        for f in state.all_fills:
            buyer = f.buyer_account_id == acct
            seller = f.seller_account_id == acct
            if not buyer and not seller:
                continue
            total += f.size
            if buyer and seller:
                continue
            other = f.seller_account_id if buyer else f.buyer_account_id
            if other in mm_accounts:
                with_other += f.size
        consensus = 1.0 - (with_other / total) if total > 0 else 0.0
        # avg_abs_position
        avg_abs_pos = (
            sum(abs(c.positions.get(acct, 0)) for c in state.cycles) / len(state.cycles)
            if state.cycles else 0.0
        )
        # Self-crosses
        self_count = sum(
            1 for f in state.all_fills
            if f.buyer_account_id == acct and f.seller_account_id == acct
        )
        self_vol = sum(
            f.size for f in state.all_fills
            if f.buyer_account_id == acct and f.seller_account_id == acct
        )
        result[acct] = {
            "account_id": acct,
            "total_pnl": None,
            "sharpe": None,
            "volume": volume,
            "volume_share": (volume / total_volume) if total_volume > 0 else 0.0,
            "pnl_bps": None,
            "uptime": uptime,
            "consensus": consensus,
            "markout_1": None,
            "markout_5": None,
            "markout_20": None,
            "avg_abs_position": avg_abs_pos,
            "self_cross_count": self_count,
            "self_cross_volume": self_vol,
        }
    return result


def _partial_hf_scores(state: AppState) -> Dict[str, dict]:
    hf_accounts = {o.account_id for c in state.cycles for o in c.orders}
    result: Dict[str, dict] = {}
    for acct in sorted(hf_accounts):
        result[acct] = {
            "account_id": acct,
            "total_pnl": None,
            "sharpe": None,
            "markout_1": None,
            "markout_5": None,
            "markout_20": None,
        }
    return result


def _compute_scores_safe(state: AppState) -> dict:
    """Try the real scoring; on unsettled, fall back to partial metrics."""
    if state.contract.settlement_value is not None:
        mm: Dict[str, MMScores] = score_mm(
            state.all_fills, state.cycles, state.positions, state.contract,
        )
        hf: Dict[str, HFScores] = score_hf(
            state.all_fills, state.cycles, state.positions, state.contract,
        )
        return {
            "settled": True,
            "mm": {k: v.__dict__ for k, v in mm.items()},
            "hf": {k: v.__dict__ for k, v in hf.items()},
        }
    return {
        "settled": False,
        "mm": _partial_mm_scores(state),
        "hf": _partial_hf_scores(state),
    }


# ---------- positions / pnl series ----------

def _positions_series(state: AppState) -> Dict[str, List[dict]]:
    """Walk all cycles once and record {pos, cash, pnl_mtm, pnl_realized} per
    account per cycle. Mirrors scoring._cycle_pnl_series but tracks all
    accounts at once and keeps cash/position separately."""
    marks = state.marks
    multiplier = state.contract.multiplier
    settlement = state.contract.settlement_value  # may be None

    all_accounts: set = set()
    for c in state.cycles:
        for f in c.fills:
            all_accounts.add(f.buyer_account_id)
            all_accounts.add(f.seller_account_id)
    # Include all known accounts (even ones who never traded) so the UI shows
    # a flat line for passive participants.
    for a in state.accounts:
        all_accounts.add(a.id)

    cash: Dict[str, float] = {a: 0.0 for a in all_accounts}
    pos: Dict[str, int] = {a: 0 for a in all_accounts}
    out: Dict[str, List[dict]] = {a: [] for a in all_accounts}

    for i, cycle in enumerate(state.cycles):
        for f in cycle.fills:
            if f.buyer_account_id == f.seller_account_id:
                # Self-fills: cash/position cancel, but still "executed"
                continue
            cash[f.buyer_account_id] -= f.price * f.size
            pos[f.buyer_account_id] += f.size
            cash[f.seller_account_id] += f.price * f.size
            pos[f.seller_account_id] -= f.size
        m = marks[i]
        for a in all_accounts:
            pnl_mtm = (cash[a] + pos[a] * m) * multiplier
            pnl_realized = (
                (cash[a] + pos[a] * settlement) * multiplier
                if settlement is not None else None
            )
            out[a].append({
                "cycle_index": cycle.state.cycle_index,
                "position": pos[a],
                "cash": round(cash[a], 6),
                "pnl_mtm": round(pnl_mtm, 6),
                "pnl_realized": (
                    round(pnl_realized, 6) if pnl_realized is not None else None
                ),
            })
    return out


# ---------- FastAPI app ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initial state load. _load_state never raises, so a missing or empty
    db is non-fatal — the server starts with a sentinel state and the
    frontend renders a 'waiting for data' screen until files appear."""
    global STATE
    STATE = _load_state(CONFIG.db_path, CONFIG.traces_path)
    print(
        f"[dashboard] initial state: status={STATE.status} "
        f"db={CONFIG.db_path} traces={CONFIG.traces_path}",
        flush=True,
    )
    yield


app = FastAPI(title="ModelX Debug Dashboard", lifespan=lifespan)

# Permissive CORS for localhost dev — the Vite proxy usually handles this,
# but a direct hit to :8000 from the browser should still work.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- routes ----------

def _status_envelope(state: AppState) -> dict:
    """Common status fields included in /api/episode regardless of load state."""
    return {
        "loaded": state.loaded,
        "status": state.status,
        "status_detail": state.status_detail,
        "loaded_at": state.loaded_at,
        "db_mtime": state.db_mtime,
        "traces_mtime": state.traces_mtime,
        "traces_loaded": state.traces_loaded,
        "sources": {
            "db_path": state.db_path,
            "traces_path": state.traces_path,
        },
    }


@app.get("/api/episode")
def episode():
    state = _state()

    # Unloaded path: return a minimal but well-shaped payload so the frontend
    # can render its waiting screen without crashing on null derefs.
    if not state.loaded or state.contract is None:
        return {
            "contract": None,
            "num_cycles": 0,
            "settled": False,
            "accounts": [],
            "stats": {
                "total_fills": 0,
                "total_volume": 0,
                "mm_fills": 0,
                "hf_fills": 0,
            },
            **_status_envelope(state),
        }

    # Final per-account PnL (mark-to-market with carried-forward final mark)
    pos_series = _positions_series(state)
    final_mark = state.marks[-1] if state.marks else 0.0
    multiplier = state.contract.multiplier

    # Build roster
    accounts_payload = []
    for a in state.accounts:
        series = pos_series.get(a.id) or []
        if series:
            final = series[-1]
            final_pos = final["position"]
            final_pnl = (
                final["pnl_realized"]
                if final["pnl_realized"] is not None else final["pnl_mtm"]
            )
        else:
            final_pos = state.positions.get(a.id, 0)
            final_pnl = final_pos * final_mark * multiplier
        accounts_payload.append({
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "model": a.model,
            "final_position": final_pos,
            "final_pnl": round(final_pnl, 6) if final_pnl is not None else None,
        })

    total_fills = len(state.all_fills)
    total_volume = sum(f.size for f in state.all_fills)
    mm_fills = sum(1 for f in state.all_fills if f.phase == "MM")
    hf_fills = sum(1 for f in state.all_fills if f.phase == "HF")

    settlement_date = None
    if state.traces and isinstance(state.traces.get("contract"), dict):
        settlement_date = state.traces["contract"].get("settlement_date")

    return {
        "contract": {
            "id": state.contract.id,
            "name": state.contract.name,
            "description": state.contract.description,
            "multiplier": state.contract.multiplier,
            "position_limit": state.contract.position_limit,
            "settlement_value": state.contract.settlement_value,
            "settlement_date": settlement_date,
        },
        "num_cycles": len(state.cycle_states),
        "settled": state.contract.settlement_value is not None,
        "accounts": accounts_payload,
        "stats": {
            "total_fills": total_fills,
            "total_volume": total_volume,
            "mm_fills": mm_fills,
            "hf_fills": hf_fills,
        },
        **_status_envelope(state),
    }


@app.get("/api/cycles")
def cycles():
    state = _state()
    info_by_cycle = _info_by_cycle(state)
    out = []
    for i, (cs, cyc) in enumerate(zip(state.cycle_states, state.cycles)):
        out.append(_cycle_state_dict(
            cs,
            info=info_by_cycle.get(cs.cycle_index),
            mark=state.marks[i],
            cyc=cyc,
        ))
    return out


@app.get("/api/fills")
def fills(
    agent: Optional[str] = None,
    phase: Optional[str] = None,
    cycle_min: Optional[int] = None,
    cycle_max: Optional[int] = None,
):
    state = _state()
    cycle_index_by_id = {cs.id: cs.cycle_index for cs in state.cycle_states}
    out = []
    for f in state.all_fills:
        ci = cycle_index_by_id.get(f.cycle_id, -1)
        if phase and f.phase != phase:
            continue
        if agent and agent not in (f.buyer_account_id, f.seller_account_id):
            continue
        if cycle_min is not None and ci < cycle_min:
            continue
        if cycle_max is not None and ci > cycle_max:
            continue
        out.append(_fill_dict(f, cycle_index_by_id))
    return out


@app.get("/api/quotes")
def quotes(cycle_index: Optional[int] = None):
    state = _state()
    out = []
    for cs, cyc in zip(state.cycle_states, state.cycles):
        if cycle_index is not None and cs.cycle_index != cycle_index:
            continue
        for q in cyc.quotes:
            out.append({
                "cycle_index": cs.cycle_index,
                "cycle_id": cs.id,
                "account_id": q.account_id,
                "bid_price": q.bid_price,
                "bid_size": q.bid_size,
                "ask_price": q.ask_price,
                "ask_size": q.ask_size,
            })
    return out


@app.get("/api/orders")
def orders(cycle_index: Optional[int] = None):
    state = _state()
    out = []
    for cs, cyc in zip(state.cycle_states, state.cycles):
        if cycle_index is not None and cs.cycle_index != cycle_index:
            continue
        for o in cyc.orders:
            out.append({
                "cycle_index": cs.cycle_index,
                "cycle_id": cs.id,
                "account_id": o.account_id,
                "side": o.side,
                "size": o.size,
            })
    return out


@app.get("/api/orderbook/{cycle_index}")
def orderbook(cycle_index: int):
    state = _state()
    if not state.loaded:
        raise HTTPException(
            status_code=404, detail=f"no cycles loaded yet (status={state.status})"
        )
    if cycle_index < 0 or cycle_index >= len(state.cycle_states):
        raise HTTPException(status_code=404, detail=f"no cycle with index {cycle_index}")

    cs = state.cycle_states[cycle_index]
    cyc = state.cycles[cycle_index]
    book = state.residual_books.get(cycle_index, [])

    # Reconstruct entering positions (before any fills in this cycle) by
    # starting from current positions and reversing this cycle's fills.
    positions_after = dict(cyc.positions)
    positions_before = dict(positions_after)
    for f in cyc.fills:
        positions_before[f.buyer_account_id] = (
            positions_before.get(f.buyer_account_id, 0) - f.size
        )
        positions_before[f.seller_account_id] = (
            positions_before.get(f.seller_account_id, 0) + f.size
        )

    cycle_index_by_id = {c.id: c.cycle_index for c in state.cycle_states}

    return {
        "cycle_index": cs.cycle_index,
        "cycle_id": cs.id,
        "phase": cs.phase,
        "mm_mark": cs.mm_mark,
        "hf_mark": cs.hf_mark,
        "quotes": [
            {
                "account_id": q.account_id,
                "bid_price": q.bid_price,
                "bid_size": q.bid_size,
                "ask_price": q.ask_price,
                "ask_size": q.ask_size,
            }
            for q in cyc.quotes
        ],
        "mm_fills": [
            _fill_dict(f, cycle_index_by_id) for f in cyc.fills if f.phase == "MM"
        ],
        "residual_book": [_book_level_dict(bl) for bl in book],
        "orders": [
            {"account_id": o.account_id, "side": o.side, "size": o.size}
            for o in cyc.orders
        ],
        "hf_fills": [
            _fill_dict(f, cycle_index_by_id) for f in cyc.fills if f.phase == "HF"
        ],
        "positions_before": positions_before,
        "positions_after": positions_after,
    }


@app.get("/api/traces")
def traces():
    state = _state()
    if state.traces is None:
        return {"loaded": False, "contract": None, "agents": {}, "info_schedule": {}}
    return {
        "loaded": True,
        **state.traces,
    }


@app.get("/api/traces/{agent_id}")
def traces_for_agent(agent_id: str):
    state = _state()
    if state.traces is None:
        raise HTTPException(status_code=404, detail="traces not loaded")
    agents = state.traces.get("agents", {}) or {}
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail=f"no traces for agent {agent_id!r}")
    return {
        "account_id": agent_id,
        **agents[agent_id],
    }


@app.get("/api/metrics")
def metrics():
    state = _state()
    if not state.loaded or state.contract is None or not state.cycles:
        return {"settled": False, "mm": {}, "hf": {}}
    return _compute_scores_safe(state)


@app.get("/api/positions")
def positions():
    state = _state()
    if not state.loaded or state.contract is None:
        return {"agents": {}}
    return {"agents": _positions_series(state)}


@app.get("/api/timeseries")
def timeseries():
    """Precomposed payload for the hero chart."""
    state = _state()
    if not state.loaded or state.contract is None:
        return {
            "cycles": [],
            "fills": [],
            "settlement": None,
            "info_cycles": [],
            "info_by_cycle": {},
            "mm_accounts": [],
            "hf_accounts": [],
        }
    info_by_cycle = _info_by_cycle(state)
    cycle_index_by_id = {cs.id: cs.cycle_index for cs in state.cycle_states}

    rows = []
    for i, (cs, cyc) in enumerate(zip(state.cycle_states, state.cycles)):
        quotes_by_agent: Dict[str, dict] = {}
        for q in cyc.quotes:
            quotes_by_agent[q.account_id] = {
                "bid_price": q.bid_price,
                "bid_size": q.bid_size,
                "ask_price": q.ask_price,
                "ask_size": q.ask_size,
                "mid": (q.bid_price + q.ask_price) / 2.0,
            }

        rows.append({
            "cycle_index": cs.cycle_index,
            "phase": cs.phase,
            "mm_mark": cs.mm_mark,
            "hf_mark": cs.hf_mark,
            "mark": state.marks[i] if state.marks[i] > 0 else None,
            "quotes_by_agent": quotes_by_agent,
            "info": info_by_cycle.get(cs.cycle_index),
        })

    fills_payload = []
    for f in state.all_fills:
        ci = cycle_index_by_id.get(f.cycle_id, -1)
        fills_payload.append({
            "cycle_index": ci,
            "price": f.price,
            "size": f.size,
            "phase": f.phase,
            "buyer": f.buyer_account_id,
            "seller": f.seller_account_id,
            "is_self_cross": f.buyer_account_id == f.seller_account_id,
        })

    info_cycles = sorted(info_by_cycle.keys())

    return {
        "cycles": rows,
        "fills": fills_payload,
        "settlement": state.contract.settlement_value,
        "info_cycles": info_cycles,
        "info_by_cycle": info_by_cycle,
        "mm_accounts": sorted({q.account_id for c in state.cycles for q in c.quotes}),
        "hf_accounts": sorted({o.account_id for c in state.cycles for o in c.orders}),
    }


@app.post("/api/reload")
def reload_state():
    """Force a reload regardless of mtime — useful for cases where the file
    was atomically replaced and the mtime didn't change, or when the user
    wants to be certain the dashboard is showing the latest data."""
    global STATE
    with _RELOAD_LOCK:
        STATE = _load_state(CONFIG.db_path, CONFIG.traces_path)
    return {
        "ok": True,
        "loaded": STATE.loaded,
        "status": STATE.status,
        "loaded_at": STATE.loaded_at,
    }


# ---------- entrypoint ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="modelx.db", help="SQLite db path")
    parser.add_argument(
        "--traces",
        default="episode_traces.json",
        help="reasoning traces JSON path",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    CONFIG.db_path = args.db
    CONFIG.traces_path = args.traces
    CONFIG.port = args.port

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
