"""
db.py - local SQLite bookkeeping for the live bot.

The original bot kept no record of anything - no trade history, no
running equity, and if the process died mid-trade it had no way to
know it was still holding a coin when it restarted. It would just
start scanning for a fresh top gainer with the old position sitting
there, unmanaged, no stop-loss watching it anymore.

This module fixes that. Every fill gets written to trades.db, and
get_open_position() lets bot.py check on startup whether it's already
holding something from a previous run - including, now, whether that
position has a protective OCO order sitting on Binance itself, so a
restart can resume watching the *same* order instead of losing track
of it.

This is used by bot.py (live) only. backtest.py does not touch a
database - it works entirely off the CSV in memory and writes its
results as plain files, so you don't need to inspect a SQLite file to
see how a backtest run went.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from utils import now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,          -- BUY or SELL
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    notional REAL NOT NULL,
    reason TEXT,
    opened_trade_id INTEGER,     -- for SELL rows: which BUY this closes
    oco_order_list_id INTEGER,   -- set on a BUY row once a protective OCO is live
    oco_stop_order_id INTEGER,   -- the STOP_LOSS_LIMIT leg's orderId
    oco_limit_order_id INTEGER,  -- the LIMIT_MAKER (take profit) leg's orderId
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db(path: str | Path = "trades.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def log_trade(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    reason: str = "",
    opened_trade_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO trades (symbol, side, quantity, price, notional, reason, "
        "opened_trade_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, side, quantity, price, quantity * price, reason,
         opened_trade_id, now_utc().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def attach_oco(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    order_list_id: int,
    stop_order_id: int,
    limit_order_id: int,
) -> None:
    """Records that a BUY trade's exit is now protected by a real OCO
    order on Binance, so a restart can find and resume watching it."""
    conn.execute(
        "UPDATE trades SET oco_order_list_id = ?, oco_stop_order_id = ?, "
        "oco_limit_order_id = ? WHERE id = ?",
        (order_list_id, stop_order_id, limit_order_id, trade_id),
    )
    conn.commit()


def log_equity(conn: sqlite3.Connection, equity: float) -> None:
    conn.execute(
        "INSERT INTO equity_log (equity, created_at) VALUES (?, ?)",
        (equity, now_utc().isoformat()),
    )
    conn.commit()


def get_open_position(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """A BUY row with no matching SELL is a position that's still open -
    used on startup to recover state after a restart or crash. If it
    has oco_stop_order_id / oco_limit_order_id set, bot.py resumes
    polling those specific orders instead of falling back to
    client-side price checks."""
    buys = conn.execute(
        "SELECT * FROM trades WHERE side='BUY' ORDER BY id DESC"
    ).fetchall()
    closed_ids = {
        row["opened_trade_id"]
        for row in conn.execute(
            "SELECT opened_trade_id FROM trades WHERE side='SELL' "
            "AND opened_trade_id IS NOT NULL"
        ).fetchall()
    }
    for buy in buys:
        if buy["id"] not in closed_ids:
            return buy
    return None
