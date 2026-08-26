"""Capa de persistencia: SQLite local con todo el historial descargado de PPI."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS accounts (
    account_number TEXT PRIMARY KEY,
    name           TEXT,
    raw            TEXT
);

-- Movimientos de cuenta corriente: la fuente de verdad del historial.
-- PPI no expone un id por movimiento, por eso deduplicamos con un hash estable.
CREATE TABLE IF NOT EXISTS movements (
    uid             TEXT PRIMARY KEY,
    account_number  TEXT NOT NULL,
    agreement_date  TEXT,
    settlement_date TEXT,
    currency        TEXT,
    amount          REAL,
    price           REAL,
    description     TEXT,
    ticker          TEXT,
    quantity        REAL,
    balance         REAL,
    ordinal         INTEGER DEFAULT 0,
    raw             TEXT
);
CREATE INDEX IF NOT EXISTS ix_mov_date   ON movements(agreement_date);
CREATE INDEX IF NOT EXISTS ix_mov_ticker ON movements(ticker);

CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY,
    account_number  TEXT NOT NULL,
    instrument_type TEXT,
    operation       TEXT,
    ticker          TEXT,
    status          TEXT,
    date            TEXT,
    settlement      TEXT,
    quantity        REAL,
    order_type      TEXT,
    operation_type  TEXT,
    price           REAL,
    currency        TEXT,
    amount          REAL,
    external_id     TEXT,
    raw             TEXT
);
CREATE INDEX IF NOT EXISTS ix_ord_date ON orders(date);

-- Foto de tenencias tal cual la reporta PPI (para conciliar contra lo calculado).
CREATE TABLE IF NOT EXISTS snapshots (
    ts             TEXT NOT NULL,
    account_number TEXT NOT NULL,
    kind           TEXT NOT NULL,   -- 'cash' | 'instrument'
    group_name     TEXT,
    ticker         TEXT,
    currency       TEXT,
    settlement     TEXT,
    quantity       REAL,
    price          REAL,
    amount         REAL,
    raw            TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS prices (
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,
    settlement TEXT NOT NULL,
    price      REAL,
    volume     REAL,
    opening    REAL,
    max        REAL,
    min        REAL,
    PRIMARY KEY (ticker, date, settlement)
);

CREATE TABLE IF NOT EXISTS instruments (
    ticker      TEXT PRIMARY KEY,
    description TEXT,
    currency    TEXT,
    type        TEXT,
    market      TEXT,
    raw         TEXT
);

-- Serie diaria del dolar implicito (CCL/MEP) para medir en moneda dura.
CREATE TABLE IF NOT EXISTS fx (
    date TEXT PRIMARY KEY,
    ccl  REAL,
    source TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Agrega columnas nuevas sobre bases creadas por versiones anteriores."""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(movements)")}
    if "ordinal" not in have:
        conn.execute("ALTER TABLE movements ADD COLUMN ordinal INTEGER DEFAULT 0")


@contextmanager
def session(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    """INSERT OR REPLACE generico. Devuelve la cantidad de filas escritas."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


def insert_many(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)", (key, value)
    )


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
