"""SQLite persistence for ModelX.

Single-file store with one table per dataclass (accounts, contracts,
cycle_states, quotes, orders, fills). The schema is created on `connect`
and every operation commits, so a restart reads what the previous process
wrote. Row shapes line up exactly with `modelx.models` field names, so
we can rebuild dataclasses via `Type(**dict(row))`.

All functions take a `sqlite3.Connection`; use `connect(path)` to open one.
Pass `":memory:"` for an ephemeral in-process db (tests, one-off runs).
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .models import (
    Account,
    Contract,
    Fill,
    LifetimeStat,
    Market,
    Order,
    PhaseState,
    Quote,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    role             TEXT NOT NULL,
    model            TEXT NOT NULL,
    points           REAL NOT NULL DEFAULT 0,
    market_id        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contracts (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL,
    multiplier       REAL NOT NULL DEFAULT 1.0,
    position_limit   INTEGER NOT NULL DEFAULT 100,
    settlement_value REAL,
    created_at       REAL NOT NULL DEFAULT 0,
    settled_at       REAL
);

CREATE TABLE IF NOT EXISTS phase_states (
    id                 TEXT PRIMARY KEY,
    contract_id        TEXT NOT NULL,
    phase_type         TEXT NOT NULL,
    phase              TEXT NOT NULL,
    mark               REAL,
    created_at         REAL NOT NULL DEFAULT 0,
    closed_at          REAL,
    info_text          TEXT
);

CREATE TABLE IF NOT EXISTS quotes (
    id           TEXT PRIMARY KEY,
    phase_id     TEXT NOT NULL,
    contract_id  TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    bid_price    REAL NOT NULL,
    bid_size     INTEGER NOT NULL,
    ask_price    REAL NOT NULL,
    ask_size     INTEGER NOT NULL,
    created_at   REAL NOT NULL DEFAULT 0,
    UNIQUE (phase_id, account_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id           TEXT PRIMARY KEY,
    phase_id     TEXT NOT NULL,
    contract_id  TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    side         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    created_at   REAL NOT NULL DEFAULT 0,
    UNIQUE (phase_id, account_id)
);

CREATE TABLE IF NOT EXISTS fills (
    id                 TEXT PRIMARY KEY,
    phase_id           TEXT NOT NULL,
    contract_id        TEXT NOT NULL,
    buyer_account_id   TEXT NOT NULL,
    seller_account_id  TEXT NOT NULL,
    price              REAL NOT NULL,
    size               INTEGER NOT NULL,
    phase              TEXT NOT NULL,
    created_at         REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS markets (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    multiplier        REAL NOT NULL DEFAULT 1.0,
    position_limit    INTEGER NOT NULL DEFAULT 100,
    max_size          INTEGER NOT NULL DEFAULT 50,
    settlement_date   TEXT,
    state             TEXT NOT NULL DEFAULT 'RUNNING',
    pending_mm        INTEGER NOT NULL DEFAULT 1,
    last_phase_ts     REAL NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_lifetime_stats (
    account_id        TEXT NOT NULL,
    market_id         TEXT NOT NULL,
    role              TEXT NOT NULL,
    total_pnl         REAL,
    sharpe            REAL,
    volume            INTEGER,
    settled_at        REAL,
    PRIMARY KEY (account_id, market_id)
);

CREATE TABLE IF NOT EXISTS phase_traces (
    phase_id       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    contract_id    TEXT NOT NULL,
    phase_type     TEXT NOT NULL,
    model          TEXT NOT NULL,
    request        TEXT NOT NULL,
    raw_response   TEXT,
    parsed_json    TEXT,
    decision_json  TEXT,
    error          TEXT,
    created_at     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (phase_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_quotes_phase   ON quotes  (phase_id);
CREATE INDEX IF NOT EXISTS idx_orders_phase   ON orders  (phase_id);
CREATE INDEX IF NOT EXISTS idx_fills_phase    ON fills   (phase_id);
CREATE INDEX IF NOT EXISTS idx_fills_contract ON fills   (contract_id);
CREATE INDEX IF NOT EXISTS idx_phases_contract ON phase_states (contract_id);
CREATE INDEX IF NOT EXISTS idx_phase_traces_contract ON phase_traces (contract_id);
CREATE INDEX IF NOT EXISTS idx_phase_traces_account  ON phase_traces (account_id);
"""

# Indexes that depend on migrated columns. Created after _maybe_migrate so
# old databases (which need ALTER TABLE first) don't fail.
POST_MIGRATION_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_accounts_market ON accounts (market_id);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with the ModelX schema applied.

    `path` can be a filesystem path or `":memory:"` for an ephemeral db.
    Old databases (pre-multi-market) are silently migrated forward.

    Migration runs BEFORE the new schema so that old cycle-based tables
    are renamed/rebuilt before CREATE TABLE IF NOT EXISTS sees them.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Migrate old tables FIRST, before applying the new schema.
    _maybe_migrate(conn)
    conn.executescript(SCHEMA)
    conn.executescript(POST_MIGRATION_SCHEMA)
    conn.commit()
    return conn


def _maybe_migrate(conn: sqlite3.Connection) -> None:
    """Idempotent migrations for older databases.

    Handles:
    - Legacy accounts without market_id column
    - Legacy cycle_states -> phase_states migration
    - Legacy markets table without new columns
    """
    try:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN market_id TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate old cycle-based schema to phase-based schema.
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    # Detect if child tables still use the old cycle_id column.
    has_old_fills = False
    if "fills" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fills)").fetchall()}
        has_old_fills = "cycle_id" in cols

    if "cycle_states" in tables or has_old_fills:
        _migrate_cycles_to_phases(conn)


def _migrate_cycles_to_phases(conn: sqlite3.Connection) -> None:
    """One-time migration from cycle-based to phase-based schema.

    Renames cycle_id -> phase_id in child tables, splits each cycle_state
    into two phase_states (MM and HF), and migrates the markets table.
    """
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    # Step 1: Rename cycle_id -> phase_id in child tables by recreating them.
    # Only do this if the tables still have the old cycle_id column.
    def _has_column(table: str, col: str) -> bool:
        if table not in tables:
            return False
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        return col in cols

    if _has_column("fills", "cycle_id"):
        conn.executescript("""
            ALTER TABLE fills RENAME TO _old_fills;
            CREATE TABLE fills (
                id TEXT PRIMARY KEY, phase_id TEXT NOT NULL, contract_id TEXT NOT NULL,
                buyer_account_id TEXT NOT NULL, seller_account_id TEXT NOT NULL,
                price REAL NOT NULL, size INTEGER NOT NULL, phase TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT 0
            );
            INSERT INTO fills (id, phase_id, contract_id, buyer_account_id,
                seller_account_id, price, size, phase, created_at)
            SELECT id, cycle_id, contract_id, buyer_account_id,
                seller_account_id, price, size, phase, created_at FROM _old_fills;
            DROP TABLE _old_fills;
        """)

    if _has_column("quotes", "cycle_id"):
        conn.executescript("""
            ALTER TABLE quotes RENAME TO _old_quotes;
            CREATE TABLE quotes (
                id TEXT PRIMARY KEY, phase_id TEXT NOT NULL, contract_id TEXT NOT NULL,
                account_id TEXT NOT NULL, bid_price REAL NOT NULL, bid_size INTEGER NOT NULL,
                ask_price REAL NOT NULL, ask_size INTEGER NOT NULL,
                created_at REAL NOT NULL DEFAULT 0, UNIQUE (phase_id, account_id)
            );
            INSERT INTO quotes (id, phase_id, contract_id, account_id,
                bid_price, bid_size, ask_price, ask_size, created_at)
            SELECT id, cycle_id, contract_id, account_id,
                bid_price, bid_size, ask_price, ask_size, created_at FROM _old_quotes;
            DROP TABLE _old_quotes;
        """)

    if _has_column("orders", "cycle_id"):
        conn.executescript("""
            ALTER TABLE orders RENAME TO _old_orders;
            CREATE TABLE orders (
                id TEXT PRIMARY KEY, phase_id TEXT NOT NULL, contract_id TEXT NOT NULL,
                account_id TEXT NOT NULL, side TEXT NOT NULL, size INTEGER NOT NULL,
                created_at REAL NOT NULL DEFAULT 0, UNIQUE (phase_id, account_id)
            );
            INSERT INTO orders (id, phase_id, contract_id, account_id, side, size, created_at)
            SELECT id, cycle_id, contract_id, account_id, side, size, created_at FROM _old_orders;
            DROP TABLE _old_orders;
        """)

    # Step 2: Create phase_states from cycle_states (if cycle_states exists).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS phase_states (
            id TEXT PRIMARY KEY, contract_id TEXT NOT NULL,
            phase_type TEXT NOT NULL, phase TEXT NOT NULL,
            mark REAL, created_at REAL NOT NULL DEFAULT 0,
            closed_at REAL, info_text TEXT
        )
    """)

    # Re-check tables since the child table rebuilds above may have changed things.
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "cycle_states" not in tables:
        conn.commit()
        return

    # Each old cycle becomes an MM + HF phase pair. Use the cycle_index
    # to generate synthetic unique timestamps since legacy data may have
    # created_at = 0.0 for all rows.  Base = max(created_at, 1_000_000)
    # so we don't collide with epoch 0, then add 2*cycle_index for MM and
    # 2*cycle_index+1 for HF.
    old_rows = conn.execute(
        "SELECT * FROM cycle_states ORDER BY cycle_index"
    ).fetchall()
    for row in old_rows:
        r = dict(row)
        old_id = r["id"]
        contract_id = r["contract_id"]
        cycle_index = r["cycle_index"]
        created_at = r["created_at"] or 0.0

        # Synthetic timestamps: base + 2*idx for MM, base + 2*idx + 1 for HF.
        base_ts = max(created_at, 1_000_000)
        mm_ts = int(base_ts) + 2 * cycle_index
        hf_ts = mm_ts + 1

        mm_id = f"{contract_id}:{mm_ts}"
        mm_closed_at = r.get("mm_phase_ended_at")
        conn.execute(
            "INSERT OR IGNORE INTO phase_states "
            "(id, contract_id, phase_type, phase, mark, created_at, closed_at, info_text) "
            "VALUES (?, ?, 'MM', 'CLOSED', ?, ?, ?, ?)",
            (mm_id, contract_id, r.get("mm_mark"), float(mm_ts), mm_closed_at,
             r.get("info_text")),
        )

        hf_id = f"{contract_id}:{hf_ts}"
        hf_closed_at = r.get("hf_phase_ended_at")
        hf_phase = "CLOSED" if hf_closed_at else "OPEN"
        conn.execute(
            "INSERT OR IGNORE INTO phase_states "
            "(id, contract_id, phase_type, phase, mark, created_at, closed_at, info_text) "
            "VALUES (?, ?, 'HF', ?, ?, ?, ?, NULL)",
            (hf_id, contract_id, hf_phase, r.get("hf_mark"), float(hf_ts), hf_closed_at),
        )

        # Remap phase_id references from old cycle_id to new phase IDs.
        conn.execute(
            "UPDATE fills SET phase_id = ? WHERE phase_id = ? AND phase = 'MM'",
            (mm_id, old_id),
        )
        conn.execute(
            "UPDATE fills SET phase_id = ? WHERE phase_id = ? AND phase = 'HF'",
            (hf_id, old_id),
        )
        conn.execute("UPDATE quotes SET phase_id = ? WHERE phase_id = ?", (mm_id, old_id))
        conn.execute("UPDATE orders SET phase_id = ? WHERE phase_id = ?", (hf_id, old_id))

    conn.execute("DROP TABLE IF EXISTS cycle_states")

    # Step 3: Migrate markets table.
    try:
        conn.execute("ALTER TABLE markets ADD COLUMN last_phase_ts REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Recreate indexes for renamed columns.
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_quotes_phase ON quotes (phase_id);
        CREATE INDEX IF NOT EXISTS idx_orders_phase ON orders (phase_id);
        CREATE INDEX IF NOT EXISTS idx_fills_phase ON fills (phase_id);
        CREATE INDEX IF NOT EXISTS idx_phases_contract ON phase_states (contract_id);
    """)

    conn.commit()


# ---------- accounts ----------

def upsert_account(conn: sqlite3.Connection, account: Account) -> None:
    conn.execute(
        """
        INSERT INTO accounts (id, name, role, model, points, market_id)
        VALUES (:id, :name, :role, :model, :points, :market_id)
        ON CONFLICT(id) DO UPDATE SET
            name      = excluded.name,
            role      = excluded.role,
            model     = excluded.model,
            points    = excluded.points,
            market_id = excluded.market_id
        """,
        account.__dict__,
    )
    conn.commit()


def get_account(conn: sqlite3.Connection, account_id: str) -> Optional[Account]:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return Account(**dict(row)) if row else None


def list_accounts(conn: sqlite3.Connection) -> List[Account]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return [Account(**dict(r)) for r in rows]


def list_accounts_for_market(
    conn: sqlite3.Connection,
    market_id: str,
) -> List[Account]:
    rows = conn.execute(
        "SELECT * FROM accounts WHERE market_id = ? ORDER BY id",
        (market_id,),
    ).fetchall()
    return [Account(**dict(r)) for r in rows]


# ---------- contracts ----------

def upsert_contract(conn: sqlite3.Connection, contract: Contract) -> None:
    conn.execute(
        """
        INSERT INTO contracts (
            id, name, description, multiplier, position_limit,
            settlement_value, created_at, settled_at
        ) VALUES (
            :id, :name, :description, :multiplier, :position_limit,
            :settlement_value, :created_at, :settled_at
        )
        ON CONFLICT(id) DO UPDATE SET
            name             = excluded.name,
            description      = excluded.description,
            multiplier       = excluded.multiplier,
            position_limit   = excluded.position_limit,
            settlement_value = excluded.settlement_value,
            created_at       = excluded.created_at,
            settled_at       = excluded.settled_at
        """,
        contract.__dict__,
    )
    conn.commit()


def get_contract(conn: sqlite3.Connection, contract_id: str) -> Optional[Contract]:
    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    return Contract(**dict(row)) if row else None


# ---------- phase_states ----------

def upsert_phase_state(conn: sqlite3.Connection, state: PhaseState) -> None:
    conn.execute(
        """
        INSERT INTO phase_states (
            id, contract_id, phase_type, phase, mark,
            created_at, closed_at, info_text
        ) VALUES (
            :id, :contract_id, :phase_type, :phase, :mark,
            :created_at, :closed_at, :info_text
        )
        ON CONFLICT(id) DO UPDATE SET
            phase      = excluded.phase,
            mark       = excluded.mark,
            closed_at  = excluded.closed_at,
            info_text  = excluded.info_text
        """,
        state.__dict__,
    )
    conn.commit()


def get_phase_state(conn: sqlite3.Connection, phase_id: str) -> Optional[PhaseState]:
    row = conn.execute("SELECT * FROM phase_states WHERE id = ?", (phase_id,)).fetchone()
    return PhaseState(**dict(row)) if row else None


def list_phase_states(
    conn: sqlite3.Connection,
    contract_id: str,
) -> List[PhaseState]:
    rows = conn.execute(
        "SELECT * FROM phase_states WHERE contract_id = ? ORDER BY created_at",
        (contract_id,),
    ).fetchall()
    return [PhaseState(**dict(r)) for r in rows]


# ---------- quotes ----------

def insert_quote(conn: sqlite3.Connection, quote: Quote) -> None:
    conn.execute(
        """
        INSERT INTO quotes (
            id, phase_id, contract_id, account_id,
            bid_price, bid_size, ask_price, ask_size, created_at
        ) VALUES (
            :id, :phase_id, :contract_id, :account_id,
            :bid_price, :bid_size, :ask_price, :ask_size, :created_at
        )
        """,
        quote.__dict__,
    )
    conn.commit()


def list_quotes_by_phase(
    conn: sqlite3.Connection,
    phase_id: str,
) -> List[Quote]:
    rows = conn.execute(
        "SELECT * FROM quotes WHERE phase_id = ? ORDER BY id",
        (phase_id,),
    ).fetchall()
    return [Quote(**dict(r)) for r in rows]


# ---------- orders ----------

def insert_order(conn: sqlite3.Connection, order: Order) -> None:
    conn.execute(
        """
        INSERT INTO orders (
            id, phase_id, contract_id, account_id, side, size, created_at
        ) VALUES (
            :id, :phase_id, :contract_id, :account_id, :side, :size, :created_at
        )
        """,
        order.__dict__,
    )
    conn.commit()


def list_orders_by_phase(
    conn: sqlite3.Connection,
    phase_id: str,
) -> List[Order]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE phase_id = ? ORDER BY id",
        (phase_id,),
    ).fetchall()
    return [Order(**dict(r)) for r in rows]


# ---------- fills ----------

def delete_phase_data(conn: sqlite3.Connection, phase_id: str) -> None:
    """Remove all fills, orders, quotes, and reasoning traces for a phase_id.

    Called by open_phase to clean up stale data from a previously interrupted
    run that left orphaned rows.
    """
    conn.execute("DELETE FROM fills WHERE phase_id = ?", (phase_id,))
    conn.execute("DELETE FROM orders WHERE phase_id = ?", (phase_id,))
    conn.execute("DELETE FROM quotes WHERE phase_id = ?", (phase_id,))
    conn.execute("DELETE FROM phase_traces WHERE phase_id = ?", (phase_id,))
    conn.commit()


def delete_future_data(conn: sqlite3.Connection, market_id: str, cutoff: float) -> int:
    """Remove data with timestamps after `cutoff` (epoch seconds).

    Used on restart to trim incomplete/future phases while preserving
    historical data so time series continues across runs.
    Returns total number of deleted rows.
    """
    future_ids = [
        row[0] for row in conn.execute(
            "SELECT id FROM phase_states WHERE contract_id = ? AND created_at > ?",
            (market_id, cutoff),
        ).fetchall()
    ]
    total = 0
    for pid in future_ids:
        total += conn.execute("DELETE FROM fills WHERE phase_id = ?", (pid,)).rowcount
        total += conn.execute("DELETE FROM orders WHERE phase_id = ?", (pid,)).rowcount
        total += conn.execute("DELETE FROM quotes WHERE phase_id = ?", (pid,)).rowcount
        total += conn.execute(
            "DELETE FROM phase_traces WHERE phase_id = ?", (pid,),
        ).rowcount
    if future_ids:
        placeholders = ",".join("?" for _ in future_ids)
        total += conn.execute(
            f"DELETE FROM phase_states WHERE id IN ({placeholders})", future_ids,
        ).rowcount
    conn.commit()
    return total


def delete_market_data(conn: sqlite3.Connection, market_id: str) -> None:
    """Remove ALL data associated with a market so it can be re-run fresh.

    Deletes child rows first (fills, orders, quotes, phase traces, phase
    states, accounts, lifetime stats) then the market and contract rows
    themselves.
    """
    conn.execute("DELETE FROM fills WHERE contract_id = ?", (market_id,))
    conn.execute("DELETE FROM orders WHERE contract_id = ?", (market_id,))
    conn.execute("DELETE FROM quotes WHERE contract_id = ?", (market_id,))
    conn.execute("DELETE FROM phase_traces WHERE contract_id = ?", (market_id,))
    conn.execute("DELETE FROM phase_states WHERE contract_id = ?", (market_id,))
    conn.execute("DELETE FROM accounts WHERE market_id = ?", (market_id,))
    conn.execute(
        "DELETE FROM agent_lifetime_stats WHERE market_id = ?", (market_id,)
    )
    conn.execute("DELETE FROM markets WHERE id = ?", (market_id,))
    conn.execute("DELETE FROM contracts WHERE id = ?", (market_id,))
    conn.commit()


def insert_fill(conn: sqlite3.Connection, fill: Fill) -> None:
    conn.execute(
        """
        INSERT INTO fills (
            id, phase_id, contract_id, buyer_account_id, seller_account_id,
            price, size, phase, created_at
        ) VALUES (
            :id, :phase_id, :contract_id, :buyer_account_id, :seller_account_id,
            :price, :size, :phase, :created_at
        )
        """,
        fill.__dict__,
    )
    conn.commit()


def list_fills_by_phase(
    conn: sqlite3.Connection,
    phase_id: str,
) -> List[Fill]:
    rows = conn.execute(
        "SELECT * FROM fills WHERE phase_id = ? ORDER BY id",
        (phase_id,),
    ).fetchall()
    return [Fill(**dict(r)) for r in rows]


def list_fills_by_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> List[Fill]:
    rows = conn.execute(
        """
        SELECT f.*
        FROM fills f
        JOIN phase_states p ON f.phase_id = p.id
        WHERE f.contract_id = ?
        ORDER BY p.created_at, f.id
        """,
        (contract_id,),
    ).fetchall()
    return [Fill(**dict(r)) for r in rows]


# ---------- derived queries ----------

def positions_for_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> Dict[str, int]:
    """Aggregate positions across every fill for the contract."""
    positions: Dict[str, int] = {}
    rows = conn.execute(
        "SELECT buyer_account_id, seller_account_id, size "
        "FROM fills WHERE contract_id = ?",
        (contract_id,),
    ).fetchall()
    for r in rows:
        b, s, sz = r["buyer_account_id"], r["seller_account_id"], r["size"]
        positions[b] = positions.get(b, 0) + sz
        positions[s] = positions.get(s, 0) - sz
    return positions


def positions_before_phase(
    conn: sqlite3.Connection,
    contract_id: str,
    before_ts: float,
) -> Dict[str, int]:
    """Positions built from all fills in phases created before `before_ts`."""
    positions: Dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT f.buyer_account_id, f.seller_account_id, f.size
        FROM fills f
        JOIN phase_states p ON f.phase_id = p.id
        WHERE p.contract_id = ? AND p.created_at < ?
        """,
        (contract_id, before_ts),
    ).fetchall()
    for r in rows:
        b, s, sz = r["buyer_account_id"], r["seller_account_id"], r["size"]
        positions[b] = positions.get(b, 0) + sz
        positions[s] = positions.get(s, 0) - sz
    return positions


# ---------- phase_traces ----------

def insert_phase_trace(
    conn: sqlite3.Connection,
    trace: Dict[str, Any],
    contract_id: str,
) -> None:
    """Persist one reasoning-trace entry emitted by an agent.

    `trace` is the dict shape written by `OpenRouterAgent._record` — the
    parsed/decision sub-objects are JSON-encoded here so the table stays
    a flat key/value store.  Uses INSERT OR REPLACE keyed on
    `(phase_id, account_id)` so that a crashed/rerun phase overwrites
    any previous in-progress row for the same (phase, agent) pair
    rather than erroring on the unique constraint.
    """
    parsed = trace.get("parsed")
    decision = trace.get("decision")
    conn.execute(
        """
        INSERT OR REPLACE INTO phase_traces (
            phase_id, account_id, contract_id, phase_type, model,
            request, raw_response, parsed_json, decision_json, error,
            created_at
        ) VALUES (
            :phase_id, :account_id, :contract_id, :phase_type, :model,
            :request, :raw_response, :parsed_json, :decision_json, :error,
            :created_at
        )
        """,
        {
            "phase_id": trace["phase_id"],
            "account_id": trace["account_id"],
            "contract_id": contract_id,
            "phase_type": trace.get("phase_type") or trace.get("phase") or "",
            "model": trace.get("model") or "",
            "request": trace.get("request") or "",
            "raw_response": trace.get("raw_response"),
            "parsed_json": json.dumps(parsed) if parsed is not None else None,
            "decision_json": json.dumps(decision) if decision is not None else None,
            "error": trace.get("error"),
            "created_at": float(trace.get("timestamp") or 0.0),
        },
    )
    conn.commit()


def list_phase_traces_by_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> List[Dict[str, Any]]:
    """All trace rows for one contract, ordered by phase timestamp.

    Joins through `phase_states` so the ordering lines up with the
    chronological phase sequence even when several phases share the
    same tick epoch (MM before HF inside one pair).  Parsed/decision
    JSON blobs are decoded back into dicts before returning.
    """
    rows = conn.execute(
        """
        SELECT t.*, p.created_at AS phase_ts
          FROM phase_traces t
          JOIN phase_states p ON t.phase_id = p.id
         WHERE t.contract_id = ?
         ORDER BY p.created_at,
                  CASE t.phase_type WHEN 'MM' THEN 0 ELSE 1 END,
                  t.account_id
        """,
        (contract_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        parsed_json = d.pop("parsed_json", None)
        decision_json = d.pop("decision_json", None)
        d["parsed"] = _loads_or_none(parsed_json)
        d["decision"] = _loads_or_none(decision_json)
        out.append(d)
    return out


def _loads_or_none(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# ---------- markets ----------

def upsert_market(conn: sqlite3.Connection, market: Market) -> None:
    conn.execute(
        """
        INSERT INTO markets (
            id, name, description, multiplier, position_limit,
            max_size, settlement_date, state, pending_mm, last_phase_ts,
            created_at
        ) VALUES (
            :id, :name, :description, :multiplier, :position_limit,
            :max_size, :settlement_date, :state, :pending_mm, :last_phase_ts,
            :created_at
        )
        ON CONFLICT(id) DO UPDATE SET
            name            = excluded.name,
            description     = excluded.description,
            multiplier      = excluded.multiplier,
            position_limit  = excluded.position_limit,
            max_size        = excluded.max_size,
            settlement_date = excluded.settlement_date,
            state           = excluded.state,
            pending_mm      = excluded.pending_mm,
            last_phase_ts   = excluded.last_phase_ts
        """,
        market.__dict__,
    )
    conn.commit()


def get_market(conn: sqlite3.Connection, market_id: str) -> Optional[Market]:
    row = conn.execute(
        "SELECT * FROM markets WHERE id = ?", (market_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    # Drop legacy columns that may still exist in old databases.
    d.pop("num_cycles", None)
    d.pop("current_cycle", None)
    return Market(**d)


def list_markets(conn: sqlite3.Connection) -> List[Market]:
    rows = conn.execute(
        "SELECT * FROM markets ORDER BY created_at, id"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("num_cycles", None)
        d.pop("current_cycle", None)
        out.append(Market(**d))
    return out


def update_market_progress(
    conn: sqlite3.Connection,
    market_id: str,
    state: str,
    pending_mm: int,
    last_phase_ts: float = 0.0,
) -> None:
    """Persist a market's runtime progress fields after a phase step."""
    conn.execute(
        """
        UPDATE markets
           SET state = ?, pending_mm = ?, last_phase_ts = ?
         WHERE id = ?
        """,
        (state, pending_mm, last_phase_ts, market_id),
    )
    conn.commit()


# ---------- lifetime stats ----------

def upsert_lifetime_stat(conn: sqlite3.Connection, stat: LifetimeStat) -> None:
    conn.execute(
        """
        INSERT INTO agent_lifetime_stats (
            account_id, market_id, role, total_pnl, sharpe, volume, settled_at
        ) VALUES (
            :account_id, :market_id, :role, :total_pnl, :sharpe, :volume, :settled_at
        )
        ON CONFLICT(account_id, market_id) DO UPDATE SET
            role       = excluded.role,
            total_pnl  = excluded.total_pnl,
            sharpe     = excluded.sharpe,
            volume     = excluded.volume,
            settled_at = excluded.settled_at
        """,
        stat.__dict__,
    )
    conn.commit()


def list_lifetime_stats(
    conn: sqlite3.Connection,
    account_id: Optional[str] = None,
) -> List[LifetimeStat]:
    if account_id is not None:
        rows = conn.execute(
            "SELECT * FROM agent_lifetime_stats WHERE account_id = ? "
            "ORDER BY settled_at, market_id",
            (account_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_lifetime_stats ORDER BY account_id, settled_at"
        ).fetchall()
    return [LifetimeStat(**dict(r)) for r in rows]
