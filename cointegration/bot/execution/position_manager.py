"""
Opens and closes pair positions: sizes the trade, calls the order
manager, places a protective stop, and keeps the database in sync.
Mirrors execution/position_manager.py in algofactory_bot. Uses a
per-pair asyncio.Lock so two overlapping cycles (or a future
webhook-triggered check) can't both act on the same pair at once.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import pandas as pd

from ..config import BotConfig
from ..db import Db, OpenPositionRow
from .order_manager import OrderManager


def pair_key(a: str, b: str) -> str:
    return f"{a}-{b}"


class PositionManager:
    def __init__(self, cfg: BotConfig, db: Db, order_manager: OrderManager, logger):
        self.cfg = cfg
        self.db = db
        self.orders = order_manager
        self.log = logger
        self.cost_frac = (cfg.fee_bps + cfg.slippage_bps) / 10_000.0
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock(self, a: str, b: str) -> asyncio.Lock:
        return self._locks[pair_key(a, b)]

    async def open_position(self, a: str, b: str, direction: int, hedge: float,
                             qty1: float, qty2: float, price1: float, price2: float,
                             entry_z: float, bar_time: pd.Timestamp) -> None:
        async with self._lock(a, b):
            qty1_signed = qty1 * direction
            qty2_signed = qty2 * -direction

            await self.orders.open_pair(a, b, qty1_signed, qty2_signed)

            entry_notional = abs(qty1_signed) * price1 + abs(qty2_signed) * price2
            entry_cost = entry_notional * self.cost_frac

            row = OpenPositionRow(
                pair_key=pair_key(a, b), leg1=a, leg2=b, direction=direction, hedge=hedge,
                qty1=qty1_signed, qty2=qty2_signed, entry_price1=price1, entry_price2=price2,
                entry_bar_time=str(bar_time), entry_z=entry_z, entry_cost=entry_cost,
                formed_at_time=str(bar_time),
            )
            self.db.upsert_open_position(row)
            self.log.info(
                f"OPEN {a}-{b} dir={direction} hedge={hedge:.4f} "
                f"qty1={qty1_signed:.6f} qty2={qty2_signed:.6f} z={entry_z:.2f}"
            )

            # exchange-side circuit breaker, best-effort, see order_manager.py
            await self.orders.place_protective_stops(
                a, b, qty1_signed, qty2_signed, price1, price2, self.cfg.leg_stop_loss_pct,
            )

    async def close_position(self, pos: dict, price1: float, price2: float,
                              exit_z: float, exit_reason: str, exit_time: pd.Timestamp) -> float:
        a, b = pos["leg1"], pos["leg2"]
        async with self._lock(a, b):
            qty1_signed, qty2_signed = pos["qty1"], pos["qty2"]

            # cancel the protective stop before closing at market, otherwise
            # the reduce-only stop can end up fighting the closing order
            await asyncio.gather(
                self.orders.client.cancel_all_open_orders(a, self.orders.dry_run),
                self.orders.client.cancel_all_open_orders(b, self.orders.dry_run),
            )
            await self.orders.close_pair(a, b, qty1_signed, qty2_signed)

            gross_pnl = qty1_signed * (price1 - pos["entry_price1"]) + qty2_signed * (price2 - pos["entry_price2"])
            exit_notional = abs(qty1_signed) * price1 + abs(qty2_signed) * price2
            exit_cost = exit_notional * self.cost_frac
            net_pnl = gross_pnl - pos["entry_cost"] - exit_cost

            self.db.record_trade({
                "pair_key": pos["pair_key"], "leg1": a, "leg2": b, "direction": pos["direction"],
                "hedge": pos["hedge"], "qty1": qty1_signed, "qty2": qty2_signed,
                "entry_price1": pos["entry_price1"], "entry_price2": pos["entry_price2"],
                "exit_price1": price1, "exit_price2": price2,
                "entry_time": pos["entry_bar_time"], "exit_time": str(exit_time),
                "entry_z": pos["entry_z"], "exit_z": exit_z, "exit_reason": exit_reason,
                "gross_pnl": gross_pnl, "costs": pos["entry_cost"] + exit_cost, "net_pnl": net_pnl,
            })
            self.db.remove_open_position(pos["pair_key"])
            self.log.info(f"CLOSE {a}-{b} reason={exit_reason} net_pnl={net_pnl:.2f}")
            return net_pnl
