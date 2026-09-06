"""
Local state persistence: open positions and closed trades, in SQLite.
Mirrors the role of db.py in algofactory_bot - the bot restarting should
never lose track of what it currently holds.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass


SCHEMA = """
CREATE TABLE IF NOT EXISTS open_positions (
    pair_key TEXT PRIMARY KEY,
    leg1 TEXT NOT NULL,
    leg2 TEXT NOT NULL,
    direction INTEGER NOT NULL,
    hedge REAL NOT NULL,
    qty1 REAL NOT NULL,
    qty2 REAL NOT NULL,
    entry_price1 REAL NOT NULL,
    entry_price2 REAL NOT NULL,
    entry_bar_time TEXT NOT NULL,
    entry_z REAL NOT NULL,
    entry_cost REAL NOT NULL,
    formed_at_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT NOT NULL,
    leg1 TEXT NOT NULL,
    leg2 TEXT NOT NULL,
    direction INTEGER NOT NULL,
    hedge REAL,
    qty1 REAL,
    qty2 REAL,
    entry_price1 REAL,
    entry_price2 REAL,
    exit_price1 REAL,
    exit_price2 REAL,
    entry_time TEXT,
    exit_time TEXT,
    entry_z REAL,
    exit_z REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    costs REAL,
    net_pnl REAL,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT PRIMARY KEY,
    realized_pnl REAL NOT NULL DEFAULT 0
);
"""


@dataclass
class OpenPositionRow:
    pair_key: str
    leg1: str
    leg2: str
    direction: int
    hedge: float
    qty1: float
    qty2: float
    entry_price1: float
    entry_price2: float
    entry_bar_time: str
    entry_z: float
    entry_cost: float
    formed_at_time: str


class Db:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        finally:
            cur.close()

    def get_open_positions(self) -> dict[str, dict]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM open_positions")
            return {row["pair_key"]: dict(row) for row in cur.fetchall()}

    def upsert_open_position(self, row: OpenPositionRow) -> None:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO open_positions
                   (pair_key, leg1, leg2, direction, hedge, qty1, qty2,
                    entry_price1, entry_price2, entry_bar_time, entry_z,
                    entry_cost, formed_at_time)
                   VALUES (:pair_key, :leg1, :leg2, :direction, :hedge, :qty1, :qty2,
                           :entry_price1, :entry_price2, :entry_bar_time, :entry_z,
                           :entry_cost, :formed_at_time)
                   ON CONFLICT(pair_key) DO UPDATE SET
                       hedge=excluded.hedge, qty1=excluded.qty1, qty2=excluded.qty2""",
                asdict(row),
            )

    def remove_open_position(self, pair_key: str) -> None:
        with self.cursor() as cur:
            cur.execute("DELETE FROM open_positions WHERE pair_key=?", (pair_key,))

    def record_trade(self, trade: dict) -> None:
        trade = {**trade, "created_at": time.time()}
        cols = ", ".join(trade.keys())
        placeholders = ", ".join(f":{k}" for k in trade.keys())
        with self.cursor() as cur:
            cur.execute(f"INSERT INTO trades ({cols}) VALUES ({placeholders})", trade)

    def add_daily_pnl(self, date: str, pnl: float) -> float:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO daily_pnl (date, realized_pnl) VALUES (?, ?)
                   ON CONFLICT(date) DO UPDATE SET realized_pnl = realized_pnl + excluded.realized_pnl""",
                (date, pnl),
            )
            cur.execute("SELECT realized_pnl FROM daily_pnl WHERE date=?", (date,))
            return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
