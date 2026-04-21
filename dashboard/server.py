"""ModelX debug dashboard — read-only FastAPI backend.

Reads a SQLite db (produced by `run_live.py --db ...`) and an optional
reasoning-traces JSON file. Exposes endpoints that power the dashboard
frontend. Never writes to either source.

Multi-market support: when the db has rows in the `markets` table the
dashboard exposes one MarketAppState per market and every per-market
endpoint accepts an optional `?market_id=` query param. Without that
param the first market is used (backward compatible with legacy
single-contract demo runs).

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

# Make the sibling `modelx` package importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from modelx.phase import Phase, load_phase
from modelx.db import (
    connect,
    get_contract,
    list_accounts,
    list_accounts_for_market,
    list_phase_states,
    list_phase_traces_by_contract,
    list_fills_by_contract,
    list_markets,
    positions_before_phase,
    positions_for_contract,
)
from modelx.matching import BookLevel, match_mm_phase
from modelx.models import Account, Contract, PhaseState, Fill, Market
from modelx.scoring import (
    HFScores,
    MMScores,
    _carry_mark_forward,
    list_lifetime_by_name,
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
class MarketAppState:
    """Per-market loaded data for the dashboard. One of these exists per
    contract present in the db. The first one is the default when no
    `market_id` query param is supplied."""
    market: Optional[Market] = None    # may be None for legacy demo dbs
    contract: Optional[Contract] = None
    accounts: List[Account] = field(default_factory=list)
    phase_states: List[PhaseState] = field(default_factory=list)
    phases: List[Phase] = field(default_factory=list)
    all_fills: List[Fill] = field(default_factory=list)
    positions: Dict[str, int] = field(default_factory=dict)
    marks: List[float] = field(default_factory=list)
    residual_books: Dict[str, List[BookLevel]] = field(default_factory=dict)
    # Reasoning traces grouped by agent, rehydrated from phase_traces.
    # {account_id: [trace_dict, ...]}, ordered chronologically (MM before
    # HF within a tick). Empty dict when the market has no persisted
    # traces yet (common during the first phase of a fresh run).
    traces_by_agent: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


@dataclass
class AppState:
    """Everything the dashboard needs, loaded from disk and refreshed on
    file-mtime change. `markets` is a list of per-market app states; the
    first entry is treated as the default when an endpoint receives no
    market_id query param."""
    markets: List[MarketAppState] = field(default_factory=list)
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


def _load_market(
    conn: sqlite3.Connection,
    contract_id: str,
    market: Optional[Market],
) -> Optional[MarketAppState]:
    """Build a MarketAppState for one contract. Returns None if the contract
    row has disappeared (db is mid-write or inconsistent)."""
    contract = get_contract(conn, contract_id)
    if contract is None:
        return None

    # Prefer per-market accounts; fall back to the global accounts list
    # for legacy databases without market_id on accounts.
    if market is not None:
        accounts = list_accounts_for_market(conn, market.id)
    else:
        accounts = list_accounts(conn)

    phase_states = list_phase_states(conn, contract.id)
    phases: List[Phase] = [
        load_phase(conn, contract, ps.id) for ps in phase_states
    ]

    all_fills = list_fills_by_contract(conn, contract.id)
    positions = positions_for_contract(conn, contract.id)
    marks = _carry_mark_forward(phases) if phases else []

    # Load reasoning traces for this contract and group by agent.
    trace_rows = list_phase_traces_by_contract(conn, contract.id)
    traces_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for row in trace_rows:
        traces_by_agent.setdefault(row["account_id"], []).append(
            _trace_row_to_dict(row),
        )

    # For every MM phase, derive the residual book the HFs saw — always
    # recompute, never trust load_phase's conditional population.
    residual_books: Dict[str, List[BookLevel]] = {}
    for ps, phase in zip(phase_states, phases):
        if ps.phase_type == "MM":
            entering = positions_before_phase(conn, contract.id, ps.created_at)
            _, book, _ = match_mm_phase(
                phase.quotes,
                cycle_id=ps.id,
                contract_id=contract.id,
                positions=entering,
            )
            residual_books[ps.id] = book

    return MarketAppState(
        market=market,
        contract=contract,
        accounts=accounts,
        phase_states=phase_states,
        phases=phases,
        all_fills=all_fills,
        positions=positions,
        marks=marks,
        residual_books=residual_books,
        traces_by_agent=traces_by_agent,
    )


def _load_state(db_path: str, traces_path: str) -> AppState:
    """Open the db, load every market (or fall back to the first contract for
    legacy demo dbs), rebuild phases, load traces.

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

        markets_rows = list_markets(conn)
        market_states: List[MarketAppState] = []

        if markets_rows:
            # Multi-market mode: one MarketAppState per markets-table row.
            for m in markets_rows:
                ms = _load_market(conn, m.id, m)
                if ms is not None:
                    market_states.append(ms)
        else:
            # Legacy mode: load the first contract row (no markets table data).
            row = conn.execute(
                "SELECT id FROM contracts ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is not None:
                ms = _load_market(conn, row["id"], None)
                if ms is not None:
                    market_states.append(ms)

        traces, traces_loaded = _load_traces(traces_path)

        if not market_states:
            return AppState(
                markets=[],
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

        return AppState(
            markets=market_states,
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


def _select_market(
    state: AppState,
    market_id: Optional[str] = None,
) -> Optional[MarketAppState]:
    """Pick the right MarketAppState for an endpoint. Defaults to the first
    market when no `market_id` is supplied. Returns None if the requested
    market doesn't exist or there are no markets loaded."""
    if not state.loaded or not state.markets:
        return None
    if market_id is None:
        return state.markets[0]
    for ms in state.markets:
        if ms.contract is not None and ms.contract.id == market_id:
            return ms
    return None


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

def _phase_state_dict(ps: PhaseState, info: Optional[str], mark: float, phase: Phase) -> dict:
    mm_fills = sum(1 for f in phase.fills if f.phase == "MM")
    hf_fills = sum(1 for f in phase.fills if f.phase == "HF")
    return {
        "phase_id": ps.id,
        "phase_type": ps.phase_type,
        "phase": ps.phase,
        "mark": mark if mark > 0 else None,
        "timestamp": ps.created_at,
        "closed_at": ps.closed_at,
        "num_quotes": len(phase.quotes),
        "num_orders": len(phase.orders),
        "mm_fills": mm_fills,
        "hf_fills": hf_fills,
        "info": info,
    }


def _fill_dict(f: Fill, phase_ts_map: Dict[str, float]) -> dict:
    timestamp = phase_ts_map.get(f.phase_id)
    return {
        "id": f.id,
        "phase_id": f.phase_id,
        "phase": f.phase,
        "buyer": f.buyer_account_id,
        "seller": f.seller_account_id,
        "price": f.price,
        "size": f.size,
        "is_self_cross": f.buyer_account_id == f.seller_account_id,
        "timestamp": timestamp,
    }


def _book_level_dict(bl: BookLevel) -> dict:
    return {
        "account_id": bl.account_id,
        "side": bl.side,
        "price": bl.price,
        "size": bl.size,
    }


def _trace_row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a phase_traces row for the frontend TraceEntry schema.

    `row` comes from `list_phase_traces_by_contract`, which has already
    decoded the parsed/decision JSON blobs. Produces the same keys the
    legacy `episode_traces.json` format used so the dashboard's
    ReasoningTraces and ContextView components work unchanged.
    """
    phase_type = row.get("phase_type") or "MM"
    return {
        "phase": phase_type,         # legacy alias, always equal to phase_type
        "phase_id": row["phase_id"],
        "phase_type": phase_type,
        "timestamp": row.get("phase_ts") or row.get("created_at") or 0.0,
        "account_id": row["account_id"],
        "model": row.get("model") or "",
        "request": row.get("request") or "",
        "raw_response": row.get("raw_response"),
        "parsed": row.get("parsed"),
        "decision": row.get("decision"),
        "error": row.get("error"),
    }


def _info_by_phase(state: AppState, ms: MarketAppState) -> Dict[str, str]:
    """Extract {phase_id: info_text} from DB phase_states or traces.

    Primary source: per-phase info_text persisted in phase_states (works for
    both live news and static info_schedule markets).
    Fallback: episode_traces.json info_schedule (legacy demo compatibility).
    """
    out: Dict[str, str] = {}
    # Primary: DB-persisted info from phase_states.
    for ps in ms.phase_states:
        if ps.info_text:
            out[ps.id] = ps.info_text
    if out:
        return out
    # Fallback: traces file (legacy demo runs) — map by phase_id using index.
    if not state.traces:
        return {}
    raw = state.traces.get("info_schedule", {}) or {}
    for k, v in raw.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if idx < len(ms.phase_states):
            ps = ms.phase_states[idx]
            if isinstance(v, list):
                out[ps.id] = "\n".join(str(x) for x in v)
            else:
                out[ps.id] = str(v)
    return out


# ---------- scoring wrapper ----------

def _partial_mm_scores(ms: MarketAppState) -> Dict[str, dict]:
    """Settlement-independent MM metrics, computed the same way scoring.py does
    but without the settlement-dependent fields."""
    mm_accounts = {q.account_id for p in ms.phases for q in p.quotes}
    total_volume = sum(f.size for f in ms.all_fills)
    mm_phases = [p for p in ms.phases if p.state.phase_type == "MM"]
    result: Dict[str, dict] = {}
    for acct in sorted(mm_accounts):
        volume = sum(
            f.size for f in ms.all_fills
            if f.buyer_account_id == acct or f.seller_account_id == acct
        )
        quoted = sum(
            1 for p in mm_phases if any(q.account_id == acct for q in p.quotes)
        )
        uptime = quoted / len(mm_phases) if mm_phases else 0.0
        total = 0
        with_other = 0
        for f in ms.all_fills:
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
        avg_abs_pos = (
            sum(abs(p.positions.get(acct, 0)) for p in ms.phases) / len(ms.phases)
            if ms.phases else 0.0
        )
        self_count = sum(
            1 for f in ms.all_fills
            if f.buyer_account_id == acct and f.seller_account_id == acct
        )
        self_vol = sum(
            f.size for f in ms.all_fills
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
            "markout_2": None,
            "markout_10": None,
            "markout_40": None,
            "markout_2_bps": None,
            "markout_10_bps": None,
            "markout_40_bps": None,
            "avg_abs_position": avg_abs_pos,
            "self_cross_count": self_count,
            "self_cross_volume": self_vol,
        }
    return result


def _partial_hf_scores(ms: MarketAppState) -> Dict[str, dict]:
    hf_accounts = {o.account_id for p in ms.phases for o in p.orders}
    result: Dict[str, dict] = {}
    for acct in sorted(hf_accounts):
        result[acct] = {
            "account_id": acct,
            "total_pnl": None,
            "sharpe": None,
            "markout_2": None,
            "markout_10": None,
            "markout_40": None,
            "markout_2_bps": None,
            "markout_10_bps": None,
            "markout_40_bps": None,
        }
    return result


def _compute_scores_safe(ms: MarketAppState) -> dict:
    """Try the real scoring; on unsettled, fall back to partial metrics."""
    latest_mark = ms.marks[-1] if ms.marks else 0.0
    if ms.contract is not None and ms.contract.settlement_value is not None:
        mm: Dict[str, MMScores] = score_mm(
            ms.all_fills, ms.phases, ms.positions, ms.contract,
            latest_mark=latest_mark,
        )
        hf: Dict[str, HFScores] = score_hf(
            ms.all_fills, ms.phases, ms.positions, ms.contract,
            latest_mark=latest_mark,
        )
        return {
            "settled": True,
            "mm": {k: v.__dict__ for k, v in mm.items()},
            "hf": {k: v.__dict__ for k, v in hf.items()},
        }
    # Unsettled — still try the real scoring with latest_mark for PnL estimates.
    if ms.phases and latest_mark > 0:
        try:
            mm_scores: Dict[str, MMScores] = score_mm(
                ms.all_fills, ms.phases, ms.positions, ms.contract,
                latest_mark=latest_mark,
            )
            hf_scores: Dict[str, HFScores] = score_hf(
                ms.all_fills, ms.phases, ms.positions, ms.contract,
                latest_mark=latest_mark,
            )
            return {
                "settled": False,
                "mm": {k: v.__dict__ for k, v in mm_scores.items()},
                "hf": {k: v.__dict__ for k, v in hf_scores.items()},
            }
        except Exception:
            pass  # fall through to partial
    return {
        "settled": False,
        "mm": _partial_mm_scores(ms),
        "hf": _partial_hf_scores(ms),
    }


# ---------- positions / pnl series ----------

def _positions_series(ms: MarketAppState) -> Dict[str, List[dict]]:
    """Walk all phases once and record {pos, cash, pnl_mtm, pnl_realized} per
    account per phase. Mirrors scoring._phase_pnl_series but tracks all
    accounts at once and keeps cash/position separately."""
    marks = ms.marks
    multiplier = ms.contract.multiplier
    settlement = ms.contract.settlement_value  # may be None

    all_accounts: set = set()
    for p in ms.phases:
        for f in p.fills:
            all_accounts.add(f.buyer_account_id)
            all_accounts.add(f.seller_account_id)
    # Include all known accounts (even ones who never traded) so the UI shows
    # a flat line for passive participants.
    for a in ms.accounts:
        all_accounts.add(a.id)

    cash: Dict[str, float] = {a: 0.0 for a in all_accounts}
    pos: Dict[str, int] = {a: 0 for a in all_accounts}
    out: Dict[str, List[dict]] = {a: [] for a in all_accounts}

    for i, phase in enumerate(ms.phases):
        for f in phase.fills:
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
                "phase_id": phase.state.id,
                "timestamp": phase.state.created_at,
                "phase_type": phase.state.phase_type,
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


app = FastAPI(title="ModelX - LLM Prediction Exchange", lifespan=lifespan)

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
def episode(market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)

    # Unloaded path: return a minimal but well-shaped payload so the frontend
    # can render its waiting screen without crashing on null derefs.
    if ms is None or ms.contract is None:
        return {
            "contract": None,
            "phase_count": 0,
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
    pos_series = _positions_series(ms)
    final_mark = ms.marks[-1] if ms.marks else 0.0
    multiplier = ms.contract.multiplier

    accounts_payload = []
    for a in ms.accounts:
        series = pos_series.get(a.id) or []
        if series:
            final = series[-1]
            final_pos = final["position"]
            final_pnl = (
                final["pnl_realized"]
                if final["pnl_realized"] is not None else final["pnl_mtm"]
            )
        else:
            final_pos = ms.positions.get(a.id, 0)
            final_pnl = final_pos * final_mark * multiplier
        accounts_payload.append({
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "model": a.model,
            "final_position": final_pos,
            "final_pnl": round(final_pnl, 6) if final_pnl is not None else None,
        })

    total_fills = len(ms.all_fills)
    total_volume = sum(f.size for f in ms.all_fills)
    mm_fills_count = sum(1 for f in ms.all_fills if f.phase == "MM")
    hf_fills_count = sum(1 for f in ms.all_fills if f.phase == "HF")

    # Settlement date: prefer the market row (live mode), then fall back to
    # the legacy traces JSON for demo dashboards.
    settlement_date = None
    if ms.market is not None:
        settlement_date = ms.market.settlement_date
    if settlement_date is None and state.traces and isinstance(state.traces.get("contract"), dict):
        settlement_date = state.traces["contract"].get("settlement_date")

    market_state = ms.market.state if ms.market is not None else (
        "SETTLED" if ms.contract.settlement_value is not None else "RUNNING"
    )
    last_phase_ts = ms.market.last_phase_ts if ms.market is not None else (
        ms.phase_states[-1].created_at if ms.phase_states else 0.0
    )
    pending_mm = ms.market.pending_mm if ms.market is not None else 1
    phase_duration_seconds = _phase_duration_seconds()

    return {
        "contract": {
            "id": ms.contract.id,
            "name": ms.contract.name,
            "description": ms.contract.description,
            "multiplier": ms.contract.multiplier,
            "position_limit": ms.contract.position_limit,
            "settlement_value": ms.contract.settlement_value,
            "settlement_date": settlement_date,
        },
        "market_state": market_state,
        "phase_count": len(ms.phase_states),
        "last_phase_ts": last_phase_ts,
        "pending_mm": pending_mm,
        "phase_duration_seconds": phase_duration_seconds,
        "settled": ms.contract.settlement_value is not None,
        "accounts": accounts_payload,
        "stats": {
            "total_fills": total_fills,
            "total_volume": total_volume,
            "mm_fills": mm_fills_count,
            "hf_fills": hf_fills_count,
        },
        **_status_envelope(state),
    }


_yaml_cache: Dict[str, Tuple[float, Optional[float]]] = {}


def _phase_duration_seconds() -> Optional[float]:
    """Return the configured phase duration in seconds, or None.

    Read directly from config — this is a setting, not something to derive.
      1. `PHASE_DURATION_SECONDS` env var (same var `run_live.py` reads).
      2. Top-level `phase_duration_seconds` in `$CONTRACT_YAML` (mtime-cached).
    """
    env_val = os.environ.get("PHASE_DURATION_SECONDS")
    if env_val:
        try:
            v = float(env_val)
            if v > 0:
                return v
        except ValueError:
            pass
    return _phase_duration_from_yaml(os.environ.get("CONTRACT_YAML"))


def _phase_duration_from_yaml(path: Optional[str]) -> Optional[float]:
    """Read `phase_duration_seconds` from a contracts YAML; cache by mtime."""
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _yaml_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        import yaml  # type: ignore
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        _yaml_cache[path] = (mtime, None)
        return None
    val = data.get("phase_duration_seconds") if isinstance(data, dict) else None
    try:
        out: Optional[float] = float(val) if val is not None else None
        if out is not None and out <= 0:
            out = None
    except (TypeError, ValueError):
        out = None
    _yaml_cache[path] = (mtime, out)
    return out


@app.get("/api/phases")
def phases(market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None:
        return []
    info_map = _info_by_phase(state, ms)
    out = []
    for i, (ps, phase) in enumerate(zip(ms.phase_states, ms.phases)):
        out.append(_phase_state_dict(
            ps,
            info=info_map.get(ps.id),
            mark=ms.marks[i],
            phase=phase,
        ))
    return out


@app.get("/api/fills")
def fills(
    agent: Optional[str] = None,
    phase: Optional[str] = None,
    market_id: Optional[str] = Query(None),
):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None:
        return []
    phase_ts_map: Dict[str, float] = {
        ps.id: ps.created_at for ps in ms.phase_states
    }
    out = []
    for f in ms.all_fills:
        if phase and f.phase != phase:
            continue
        if agent and agent not in (f.buyer_account_id, f.seller_account_id):
            continue
        out.append(_fill_dict(f, phase_ts_map))
    return out


@app.get("/api/quotes")
def quotes(
    phase_id: Optional[str] = None,
    market_id: Optional[str] = Query(None),
):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None:
        return []
    out = []
    for ps, ph in zip(ms.phase_states, ms.phases):
        if phase_id is not None and ps.id != phase_id:
            continue
        for q in ph.quotes:
            out.append({
                "phase_id": ps.id,
                "phase_type": ps.phase_type,
                "timestamp": ps.created_at,
                "account_id": q.account_id,
                "bid_price": q.bid_price,
                "bid_size": q.bid_size,
                "ask_price": q.ask_price,
                "ask_size": q.ask_size,
            })
    return out


@app.get("/api/orders")
def orders(
    phase_id: Optional[str] = None,
    market_id: Optional[str] = Query(None),
):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None:
        return []
    out = []
    for ps, ph in zip(ms.phase_states, ms.phases):
        if phase_id is not None and ps.id != phase_id:
            continue
        for o in ph.orders:
            out.append({
                "phase_id": ps.id,
                "phase_type": ps.phase_type,
                "timestamp": ps.created_at,
                "account_id": o.account_id,
                "side": o.side,
                "size": o.size,
            })
    return out


@app.get("/api/orderbook/{phase_id:path}")
def orderbook(phase_id: str, market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None:
        raise HTTPException(
            status_code=404, detail=f"no phases loaded yet (status={state.status})"
        )

    # Find the phase by id.
    target_idx: Optional[int] = None
    for i, ps in enumerate(ms.phase_states):
        if ps.id == phase_id:
            target_idx = i
            break
    if target_idx is None:
        raise HTTPException(status_code=404, detail=f"no phase with id {phase_id!r}")

    ps = ms.phase_states[target_idx]
    phase = ms.phases[target_idx]
    book = ms.residual_books.get(ps.id, [])

    positions_after = dict(phase.positions)
    positions_before = dict(positions_after)
    for f in phase.fills:
        positions_before[f.buyer_account_id] = (
            positions_before.get(f.buyer_account_id, 0) - f.size
        )
        positions_before[f.seller_account_id] = (
            positions_before.get(f.seller_account_id, 0) + f.size
        )

    phase_ts_map: Dict[str, float] = {
        p.id: p.created_at for p in ms.phase_states
    }

    return {
        "phase_id": ps.id,
        "phase_type": ps.phase_type,
        "phase": ps.phase,
        "mark": ps.mark,
        "timestamp": ps.created_at,
        "quotes": [
            {
                "account_id": q.account_id,
                "bid_price": q.bid_price,
                "bid_size": q.bid_size,
                "ask_price": q.ask_price,
                "ask_size": q.ask_size,
            }
            for q in phase.quotes
        ],
        "mm_fills": [
            _fill_dict(f, phase_ts_map) for f in phase.fills if f.phase == "MM"
        ],
        "residual_book": [_book_level_dict(bl) for bl in book],
        "orders": [
            {"account_id": o.account_id, "side": o.side, "size": o.size}
            for o in phase.orders
        ],
        "hf_fills": [
            _fill_dict(f, phase_ts_map) for f in phase.fills if f.phase == "HF"
        ],
        "positions_before": positions_before,
        "positions_after": positions_after,
    }


def _build_traces_from_market(ms: MarketAppState) -> Dict[str, Any]:
    """Shape the /api/traces payload from a market's phase_traces rows.

    Returns the schema the ReasoningTraces frontend expects:
    `{loaded, contract, agents: {id: {model, role, traces: [...]}}}`.
    Role/model are pulled from the accounts table so agents show up
    even before they've emitted their first trace; model on trace rows
    takes precedence so a mid-run model swap is reflected.
    """
    accounts_map = {a.id: a for a in ms.accounts}
    agents: Dict[str, Any] = {}
    for acct in ms.accounts:
        agents[acct.id] = {
            "model": acct.model,
            "role": acct.role,
            "traces": [],
        }
    for aid, trace_list in ms.traces_by_agent.items():
        acct = accounts_map.get(aid)
        first_model = next(
            (t["model"] for t in trace_list if t.get("model")),
            (acct.model if acct else ""),
        )
        agents[aid] = {
            "model": first_model,
            "role": acct.role if acct else (
                trace_list[0].get("phase_type") if trace_list else "MM"
            ),
            "traces": trace_list,
        }

    contract_payload: Optional[Dict[str, Any]] = None
    if ms.contract is not None:
        contract_payload = {
            "id": ms.contract.id,
            "name": ms.contract.name,
            "description": ms.contract.description,
            "multiplier": ms.contract.multiplier,
            "position_limit": ms.contract.position_limit,
            "settlement_value": ms.contract.settlement_value,
            "settlement_date": (
                ms.market.settlement_date if ms.market is not None else None
            ),
        }

    return {
        "loaded": True,
        "contract": contract_payload,
        "agents": agents,
        "info_schedule": {},
    }


@app.get("/api/traces")
def traces(market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is not None and ms.contract is not None:
        return _build_traces_from_market(ms)
    # Legacy fallback: demo dbs with no phase_traces but a companion
    # episode_traces.json file next to the db.
    if state.traces is None:
        return {"loaded": False, "contract": None, "agents": {}, "info_schedule": {}}
    return {"loaded": True, **state.traces}


@app.get("/api/traces/{agent_id}")
def traces_for_agent(agent_id: str, market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is not None and ms.contract is not None:
        payload = _build_traces_from_market(ms)
        agents = payload.get("agents", {}) or {}
        if agent_id not in agents:
            raise HTTPException(
                status_code=404, detail=f"no traces for agent {agent_id!r}",
            )
        return {"account_id": agent_id, **agents[agent_id]}
    # Legacy fallback.
    if state.traces is None:
        raise HTTPException(status_code=404, detail="traces not loaded")
    legacy_agents = state.traces.get("agents", {}) or {}
    if agent_id not in legacy_agents:
        raise HTTPException(
            status_code=404, detail=f"no traces for agent {agent_id!r}",
        )
    return {"account_id": agent_id, **legacy_agents[agent_id]}


@app.get("/api/metrics")
def metrics(market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None or ms.contract is None or not ms.phases:
        return {"settled": False, "mm": {}, "hf": {}}
    return _compute_scores_safe(ms)


@app.get("/api/positions")
def positions(market_id: Optional[str] = Query(None)):
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None or ms.contract is None:
        return {"agents": {}}
    return {"agents": _positions_series(ms)}


@app.get("/api/timeseries")
def timeseries(market_id: Optional[str] = Query(None)):
    """Precomposed payload for the hero chart."""
    state = _state()
    ms = _select_market(state, market_id)
    if ms is None or ms.contract is None:
        return {
            "phases": [],
            "fills": [],
            "settlement": None,
            "info_phases": [],
            "info_by_phase": {},
            "mm_accounts": [],
            "hf_accounts": [],
        }
    info_map = _info_by_phase(state, ms)
    phase_ts_map: Dict[str, float] = {
        ps.id: ps.created_at for ps in ms.phase_states
    }

    rows = []
    for i, (ps, phase) in enumerate(zip(ms.phase_states, ms.phases)):
        quotes_by_agent: Dict[str, dict] = {}
        for q in phase.quotes:
            quotes_by_agent[q.account_id] = {
                "bid_price": q.bid_price,
                "bid_size": q.bid_size,
                "ask_price": q.ask_price,
                "ask_size": q.ask_size,
                "mid": (q.bid_price + q.ask_price) / 2.0,
            }

        rows.append({
            "phase_id": ps.id,
            "phase_type": ps.phase_type,
            "phase": ps.phase,
            "mark": ms.marks[i] if ms.marks[i] > 0 else None,
            "timestamp": ps.created_at,
            "closed_at": ps.closed_at,
            "quotes_by_agent": quotes_by_agent,
            "info": info_map.get(ps.id),
        })

    fills_payload = []
    for f in ms.all_fills:
        timestamp = phase_ts_map.get(f.phase_id)
        fills_payload.append({
            "phase_id": f.phase_id,
            "timestamp": timestamp,
            "price": f.price,
            "size": f.size,
            "phase": f.phase,
            "buyer": f.buyer_account_id,
            "seller": f.seller_account_id,
            "is_self_cross": f.buyer_account_id == f.seller_account_id,
        })

    info_phases = sorted(info_map.keys())

    return {
        "phases": rows,
        "fills": fills_payload,
        "settlement": ms.contract.settlement_value,
        "info_phases": info_phases,
        "info_by_phase": info_map,
        "mm_accounts": sorted({q.account_id for p in ms.phases for q in p.quotes}),
        "hf_accounts": sorted({o.account_id for p in ms.phases for o in p.orders}),
    }


@app.get("/api/markets")
def markets():
    """List every market the dashboard knows about. Used by the frontend's
    market selector dropdown."""
    state = _state()
    out = []
    for ms in state.markets:
        if ms.contract is None:
            continue
        m = ms.market
        if m is not None:
            out.append({
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "state": m.state,
                "phase_count": len(ms.phase_states),
                "last_phase_ts": m.last_phase_ts,
                "settlement_date": m.settlement_date,
                "settlement_value": ms.contract.settlement_value,
                "multiplier": m.multiplier,
                "settled": ms.contract.settlement_value is not None,
            })
        else:
            # Legacy demo: synthesize from contract.
            settled = ms.contract.settlement_value is not None
            out.append({
                "id": ms.contract.id,
                "name": ms.contract.name,
                "description": ms.contract.description,
                "state": "SETTLED" if settled else "RUNNING",
                "phase_count": len(ms.phase_states),
                "last_phase_ts": (
                    ms.phase_states[-1].created_at if ms.phase_states else 0.0
                ),
                "settlement_date": None,
                "settlement_value": ms.contract.settlement_value,
                "multiplier": ms.contract.multiplier,
                "settled": settled,
            })
    return out


@app.get("/api/metrics/lifetime")
def metrics_lifetime():
    """Aggregate per-agent stats across every settled market in the db.

    Reads from `agent_lifetime_stats`, which `settle.py` populates when a
    market settles. Until at least one market has been settled, this returns
    an empty mapping. Keyed by short agent name (with the `{market_id}:` prefix
    stripped) so the same agent across multiple markets aggregates as one row.
    """
    if not os.path.exists(CONFIG.db_path):
        return {"agents": {}}
    conn = connect(CONFIG.db_path)
    try:
        out: Dict[str, dict] = {}
        for name, ls in list_lifetime_by_name(conn).items():
            out[name] = {
                "account_id": ls.account_id,
                "name": ls.name,
                "markets_traded": ls.markets_traded,
                "total_pnl": round(ls.total_pnl, 6),
                "total_volume": ls.total_volume,
                "avg_sharpe": round(ls.avg_sharpe, 6),
                "best_market_pnl": round(ls.best_market_pnl, 6),
                "worst_market_pnl": round(ls.worst_market_pnl, 6),
                "per_market": [
                    {
                        "market_id": s.market_id,
                        "role": s.role,
                        "total_pnl": s.total_pnl,
                        "sharpe": s.sharpe,
                        "volume": s.volume,
                        "settled_at": s.settled_at,
                    }
                    for s in ls.per_market
                ],
            }
        return {"agents": out}
    finally:
        conn.close()


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


# ---------- static frontend ----------

# Serve the built Vite frontend (dashboard/frontend/dist) when present. All
# `/api/*` routes above are registered first and win route matching; anything
# else falls through to the static files, and unknown paths serve index.html
# so React Router / direct deep-links work on refresh.
_FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "frontend", "dist",
)
if os.path.isdir(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        candidate = os.path.join(_FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))


# ---------- entrypoint ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "modelx.db"),
        help="SQLite db path (default: $DB_PATH or modelx.db)",
    )
    parser.add_argument(
        "--traces",
        default=os.environ.get("TRACES_PATH", "episode_traces.json"),
        help="reasoning traces JSON path (default: $TRACES_PATH or episode_traces.json)",
    )
    parser.add_argument(
        "--port", type=int,
        default=int(os.environ.get("PORT", "8000")),
    )
    parser.add_argument(
        "--host", default=os.environ.get("HOST", "127.0.0.1"),
    )
    args = parser.parse_args()

    CONFIG.db_path = args.db
    CONFIG.traces_path = args.traces
    CONFIG.port = args.port

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
