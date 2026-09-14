"""Risk manager - ATR-based position sizing with adaptive multiplier.

Ported 1:1 from algofactory_bot/risk.py.

Sizing formula:
  risk_amount = equity * risk_pct * adaptive_mult
  stop_distance = atr * stop_atr_mult
  raw_qty = risk_amount / stop_distance
  qty = floor_to_step(raw_qty, step_size)

Adaptive multiplier: derived from signal score (linear interpolation).
  score=0.25 -> min_mult (0.5x), score=0.70 -> max_mult (1.5x)

For bollinger_reversion, stop_price is the hard ATR-based protective stop;
take1/take2 are informational ATR targets logged to Trade for visibility,
but the strategy's real exit is the dynamic middle-band cross computed in
strategies/bollinger_reversion.py each bar, not one of these static prices.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from config import BotConfig
from filters import SymbolInfo
from models import OrderIntentDraft
from utils import floor_to_step

logger = logging.getLogger(__name__)

_ADAPTIVE_MULT_MIN = 0.5
_ADAPTIVE_MULT_MAX = 1.5


@dataclass(frozen=True, slots=True)
class PositionSizing:
    """Computed position sizing for an intent."""
    quantity: Decimal
    stop_price: Decimal
    take1_price: Decimal
    take2_price: Decimal
    risk_amount: float
    initial_risk: float
    risk_multiplier: float
    stop_atr_mult: float
    take1_atr_mult: float
    take2_atr_mult: float
    atr: float = 0.0
    entry_price: float = 0.0


class RiskManager:
    """ATR-based risk and position sizing with adaptive multiplier."""

    def __init__(self, bot_config: BotConfig):
        self._config = bot_config

    def compute_adaptive_multiplier(self, signal_score: float) -> float:
        """score=0.25 -> min_mult (0.5x), score=0.70 -> max_mult (1.5x)."""
        score_min, score_max = 0.25, 0.70
        t = max(0.0, min(1.0, (signal_score - score_min) / (score_max - score_min)))
        mult = _ADAPTIVE_MULT_MIN + t * (_ADAPTIVE_MULT_MAX - _ADAPTIVE_MULT_MIN)
        return round(mult, 2)

    def compute_position_size(
        self,
        draft: OrderIntentDraft,
        equity: float,
        symbol_info: SymbolInfo,
        atr: float,
        signal_score: float = 0.5,
        entry_price: float = 0.0,
    ) -> PositionSizing | None:
        """Compute position size from ATR. Returns None if rejected."""
        adaptive_mult = self.compute_adaptive_multiplier(signal_score)
        risk_pct = self._config.risk_per_trade_pct
        stop_atr = self._config.stop_atr
        take1_atr = self._config.take1_atr
        take2_atr = self._config.take2_atr

        risk_amount = equity * risk_pct * adaptive_mult
        stop_distance = atr * stop_atr
        if stop_distance <= 0:
            logger.warning("Reject %s: stop_distance=0 (atr=%.6f)", draft.symbol, atr)
            return None

        tick_value = 10 ** -symbol_info.tick_size if symbol_info.tick_size > 0 else 0.00001
        min_stop_distance = 2 * tick_value
        if stop_distance < min_stop_distance:
            logger.warning("Reject %s: stop_distance %.8f < min %.8f", draft.symbol, stop_distance, min_stop_distance)
            return None

        raw_qty = Decimal(str(risk_amount)) / Decimal(str(stop_distance))
        qty = floor_to_step(raw_qty, symbol_info.step_size)

        max_exchange_qty = symbol_info.max_qty
        if max_exchange_qty > 0 and float(qty) > max_exchange_qty:
            qty = floor_to_step(Decimal(str(max_exchange_qty)), symbol_info.step_size)

        min_qty = Decimal(str(symbol_info.min_qty))
        if qty < min_qty:
            qty = min_qty

        if entry_price <= 0:
            entry_price = atr * 10
        notional = float(qty) * entry_price
        min_notional = symbol_info.min_notional * 1.15
        if notional < min_notional and entry_price > 0:
            needed = Decimal(str(min_notional)) / Decimal(str(entry_price))
            qty = floor_to_step(needed, symbol_info.step_size)

        max_notional = equity * self._config.max_symbol_notional_pct
        if entry_price > 0:
            max_qty_from_notional = floor_to_step(
                Decimal(str(max_notional)) / Decimal(str(entry_price)), symbol_info.step_size,
            )
            if qty > max_qty_from_notional:
                qty = max_qty_from_notional

        actual_notional = float(qty) * entry_price
        if actual_notional > max_notional and entry_price > 0:
            logger.warning("Reject %s: notional %.2f > max %.2f", draft.symbol, actual_notional, max_notional)
            return None

        initial_risk = float(qty) * stop_distance
        if initial_risk > 2 * risk_amount:
            logger.warning("Reject %s: initial_risk %.2f > 2x risk_amount %.2f", draft.symbol, initial_risk, 2 * risk_amount)
            return None

        direction = draft.direction
        d = Decimal(str(direction))
        a = Decimal(str(atr))
        stop_p = Decimal(str(entry_price)) - d * a * Decimal(str(stop_atr))
        take1_p = Decimal(str(entry_price)) + d * a * Decimal(str(take1_atr))
        take2_p = Decimal(str(entry_price)) + d * a * Decimal(str(take2_atr))

        return PositionSizing(
            quantity=qty, stop_price=stop_p, take1_price=take1_p, take2_price=take2_p,
            risk_amount=risk_amount, initial_risk=initial_risk, risk_multiplier=adaptive_mult,
            stop_atr_mult=stop_atr, take1_atr_mult=take1_atr, take2_atr_mult=take2_atr,
            atr=atr, entry_price=entry_price,
        )

    def recalc_levels(self, entry_price: float, atr: float, direction: int) -> tuple[Decimal, Decimal, Decimal]:
        """Recalculate stop/take from actual fill price (not the sizing estimate)."""
        stop_atr = self._config.stop_atr
        take1_atr = self._config.take1_atr
        take2_atr = self._config.take2_atr
        d = Decimal(str(direction))
        a = Decimal(str(atr))
        stop = Decimal(str(entry_price)) - d * a * Decimal(str(stop_atr))
        take1 = Decimal(str(entry_price)) + d * a * Decimal(str(take1_atr))
        take2 = Decimal(str(entry_price)) + d * a * Decimal(str(take2_atr))
        return stop, take1, take2

    def can_open_new(self, open_count: int, entries_this_bar: int, max_per_bar: int = 1) -> bool:
        if open_count >= self._config.max_positions:
            return False
        if entries_this_bar >= max_per_bar:
            return False
        return True

    def is_on_cooldown(self, last_close_ms: int, now_ms: int, bar_ms: int) -> bool:
        if last_close_ms <= 0:
            return False
        return (now_ms - last_close_ms) < bar_ms
