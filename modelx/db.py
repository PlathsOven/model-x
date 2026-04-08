"""SQLite persistence for ModelX.

Single-file store with one table per dataclass (accounts, contracts,
cycle_states, quotes, orders, fills). The schema is created on `connect`
and every operation commits, so a restart reads what the previous process
wrote. Row shapes line up exactly with `modelx.models` field names, so
we can rebuild dataclasses via `Type(**dict(row))`.

All functions take a `sqlite3.Connection`; use `connect(path)` to open one.
Pass `":memory:"` for an ephemeral in-process db (tests, one-off runs).
"""

import sqlite3
from typing import Dict, List, Optional

from .models import Account, Contract, CycleState, Fill, Order, Quote


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    role             TEXT NOT NULL,
    model            TEXT NOT NULL,
    points           REAL NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS cycle_states (
    id                 TEXT PRIMARY KEY,
    contract_id        TEXT NOT NULL,
    cycle_index        INTEGER NOT NULL,
    phase              TEXT NOT NULL,
    mm_mark            REAL,
    hf_mark            REAL,
    created_at         REAL NOT NULL DEFAULT 0,
    mm_phase_ended_at  REAL,
    hf_phase_ended_at  REAL,
    UNIQUE (contract_id, cycle_index)
);

CREATE TABLE IF NOT EXISTS quotes (
    id           TEXT PRIMARY KEY,
    cycle_id     TEXT NOT NULL,
    contract_id  TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    bid_price    REAL NOT NULL,
    bid_size     INTEGER NOT NULL,
    ask_price    REAL NOT NULL,
    ask_size     INTEGER NOT NULL,
    created_at   REAL NOT NULL DEFAULT 0,
    UNIQUE (cycle_id, account_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id           TEXT PRIMARY KEY,
    cycle_id     TEXT NOT NULL,
    contract_id  TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    side         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    created_at   REAL NOT NULL DEFAULT 0,
    UNIQUE (cycle_id, account_id)
);

CREATE TABLE IF NOT EXISTS fills (
    id                 TEXT PRIMARY KEY,
    cycle_id           TEXT NOT NULL,
    contract_id        TEXT NOT NULL,
    buyer_account_id   TEXT NOT NULL,
    seller_account_id  TEXT NOT NULL,
    price              REAL NOT NULL,
    size               INTEGER NOT NULL,
    phase              TEXT NOT NULL,
    created_at         REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_quotes_cycle  ON quotes  (cycle_id);
CREATE INDEX IF NOT EXISTS idx_orders_cycle  ON orders  (cycle_id);
CREATE INDEX IF NOT EXISTS idx_fills_cycle   ON fills   (cycle_id);
CREATE INDEX IF NOT EXISTS idx_fills_contract ON fills  (contract_id);
CREATE INDEX IF NOT EXISTS idx_cycles_contract ON cycle_states (contract_id);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with the ModelX schema applied.

    `path` can be a filesystem path or `":memory:"` for an ephemeral db.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------- accounts ----------

def upsert_account(conn: sqlite3.Connection, account: Account) -> None:
    conn.execute(
        """
        INSERT INTO accounts (id, name, role, model, points)
        VALUES (:id, :name, :role, :model, :points)
        ON CONFLICT(id) DO UPDATE SET
            name   = excluded.name,
            role   = excluded.role,
            model  = excluded.model,
            points = excluded.points
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


# ---------- cycle_states ----------

def upsert_cycle_state(conn: sqlite3.Connection, state: CycleState) -> None:
    conn.execute(
        """
        INSERT INTO cycle_states (
            id, contract_id, cycle_index, phase, mm_mark, hf_mark,
            created_at, mm_phase_ended_at, hf_phase_ended_at
        ) VALUES (
            :id, :contract_id, :cycle_index, :phase, :mm_mark, :hf_mark,
            :created_at, :mm_phase_ended_at, :hf_phase_ended_at
        )
        ON CONFLICT(id) DO UPDATE SET
            phase             = excluded.phase,
            mm_mark           = excluded.mm_mark,
            hf_mark           = excluded.hf_mark,
            mm_phase_ended_at = excluded.mm_phase_ended_at,
            hf_phase_ended_at = excluded.hf_phase_ended_at
        """,
        state.__dict__,
    )
    conn.commit()


def get_cycle_state(conn: sqlite3.Connection, cycle_id: str) -> Optional[CycleState]:
    row = conn.execute("SELECT * FROM cycle_states WHERE id = ?", (cycle_id,)).fetchone()
    return CycleState(**dict(row)) if row else None


def list_cycle_states(
    conn: sqlite3.Connection,
    contract_id: str,
) -> List[CycleState]:
    rows = conn.execute(
        "SELECT * FROM cycle_states WHERE contract_id = ? ORDER BY cycle_index",
        (contract_id,),
    ).fetchall()
    return [CycleState(**dict(r)) for r in rows]


# ---------- quotes ----------

def insert_quote(conn: sqlite3.Connection, quote: Quote) -> None:
    conn.execute(
        """
        INSERT INTO quotes (
            id, cycle_id, contract_id, account_id,
            bid_price, bid_size, ask_price, ask_size, created_at
        ) VALUES (
            :id, :cycle_id, :contract_id, :account_id,
            :bid_price, :bid_size, :ask_price, :ask_size, :created_at
        )
        """,
        quote.__dict__,
    )
    conn.commit()


def list_quotes_by_cycle(
    conn: sqlite3.Connection,
    cycle_id: str,
) -> List[Quote]:
    rows = conn.execute(
        "SELECT * FROM quotes WHERE cycle_id = ? ORDER BY id",
        (cycle_id,),
    ).fetchall()
    return [Quote(**dict(r)) for r in rows]


# ---------- orders ----------

def insert_order(conn: sqlite3.Connection, order: Order) -> None:
    conn.execute(
        """
        INSERT INTO orders (
            id, cycle_id, contract_id, account_id, side, size, created_at
        ) VALUES (
            :id, :cycle_id, :contract_id, :account_id, :side, :size, :created_at
        )
        """,
        order.__dict__,
    )
    conn.commit()


def list_orders_by_cycle(
    conn: sqlite3.Connection,
    cycle_id: str,
) -> List[Order]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE cycle_id = ? ORDER BY id",
        (cycle_id,),
    ).fetchall()
    return [Order(**dict(r)) for r in rows]


# ---------- fills ----------

def insert_fill(conn: sqlite3.Connection, fill: Fill) -> None:
    conn.execute(
        """
        INSERT INTO fills (
            id, cycle_id, contract_id, buyer_account_id, seller_account_id,
            price, size, phase, created_at
        ) VALUES (
            :id, :cycle_id, :contract_id, :buyer_account_id, :seller_account_id,
            :price, :size, :phase, :created_at
        )
        """,
        fill.__dict__,
    )
    conn.commit()


def list_fills_by_cycle(
    conn: sqlite3.Connection,
    cycle_id: str,
) -> List[Fill]:
    rows = conn.execute(
        "SELECT * FROM fills WHERE cycle_id = ? ORDER BY id",
        (cycle_id,),
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
        JOIN cycle_states c ON f.cycle_id = c.id
        WHERE f.contract_id = ?
        ORDER BY c.cycle_index, f.id
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


def positions_before_cycle(
    conn: sqlite3.Connection,
    contract_id: str,
    cycle_index: int,
) -> Dict[str, int]:
    """Positions built from all fills in cycles with index < `cycle_index`."""
    positions: Dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT f.buyer_account_id, f.seller_account_id, f.size
        FROM fills f
        JOIN cycle_states c ON f.cycle_id = c.id
        WHERE c.contract_id = ? AND c.cycle_index < ?
        """,
        (contract_id, cycle_index),
    ).fetchall()
    for r in rows:
        b, s, sz = r["buyer_account_id"], r["seller_account_id"], r["size"]
        positions[b] = positions.get(b, 0) + sz
        positions[s] = positions.get(s, 0) - sz
    return positions
