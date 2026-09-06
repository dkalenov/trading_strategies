"""
Portfolio-level risk checks: capacity limits and kill switches that sit
above individual pair signals. Mirrors risk.py in algofactory_bot.
"""

from __future__ import annotations

from .config import BotConfig
from .db import Db
from .utils import today_str


class RiskManager:
    def __init__(self, cfg: BotConfig, db: Db, logger):
        self.cfg = cfg
        self.db = db
        self.log = logger

    def trading_allowed(self) -> bool:
        if self.cfg.disable_trading:
            self.log.warning("DISABLE_TRADING is set - no new positions will be opened")
            return False
        day_pnl = self._today_pnl()
        limit = -abs(self.cfg.max_daily_loss_pct) * self.cfg.capital_usd
        if day_pnl <= limit:
            self.log.warning(
                f"Daily loss limit hit: realized {day_pnl:.2f} <= {limit:.2f}. "
                f"No new positions today."
            )
            return False
        return True

    def has_capacity(self, open_positions: dict, gross_exposure: float) -> bool:
        if len(open_positions) >= self.cfg.max_concurrent_pairs:
            return False
        if gross_exposure >= self.cfg.max_gross_exposure_pct * self.cfg.capital_usd:
            return False
        return True

    def record_realized_pnl(self, pnl: float) -> None:
        self.db.add_daily_pnl(today_str(), pnl)

    def _today_pnl(self) -> float:
        with self.db.cursor() as cur:
            cur.execute("SELECT realized_pnl FROM daily_pnl WHERE date=?", (today_str(),))
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
