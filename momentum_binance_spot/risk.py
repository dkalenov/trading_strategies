"""
risk.py - turning "spend $X on this symbol" into a real, exchange-legal
order quantity.

The original bot computed quantity as `round(amount / price, 1)` and
sent that straight to `create_order`. Binance would reject most of
those orders: every symbol has its own LOT_SIZE step (you can't buy
0.13 of a coin if the exchange only allows multiples of 1), its own
minQty, and its own minNotional (the order has to be worth at least
some minimum dollar amount). Rounding everything to 1 decimal place
ignores all three rules. This module is the fix: every quantity goes
through the symbol's real filters before it's allowed anywhere near
an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True)
class SymbolInfo:
    """The exchange's own rules for a symbol, pulled from exchangeInfo."""
    symbol: str
    step_size: float
    tick_size: float
    min_qty: float
    min_notional: float


@dataclass(frozen=True)
class PositionSizing:
    quantity: float
    notional: float


def floor_to_step(value: float, step: float) -> float:
    """Round down to the nearest multiple of step (never round up - an
    order slightly under budget is fine, one slightly over isn't).

    Uses Decimal so this doesn't inherit float's rounding surprises
    (e.g. 0.1 + 0.2 != 0.3) on step sizes like 0.001 that are common
    on low-price coins.
    """
    if step <= 0:
        return value
    step_d = Decimal(str(step))
    value_d = Decimal(str(value))
    steps = (value_d / step_d).to_integral_value(rounding=ROUND_DOWN)
    return float(steps * step_d)


def compute_position_size(
    alloc_quote: float,
    price: float,
    symbol_info: SymbolInfo,
) -> PositionSizing | None:
    """
    alloc_quote: how much quote currency (e.g. USDT) you want to spend.
    Returns None if the resulting order would be rejected by the
    exchange (below minQty or minNotional) - the caller should treat
    that as "skip this trade", not force an order through.
    """
    if price <= 0 or alloc_quote <= 0:
        return None

    raw_qty = alloc_quote / price
    qty = floor_to_step(raw_qty, symbol_info.step_size)
    notional = qty * price

    if qty < symbol_info.min_qty:
        return None
    if notional < symbol_info.min_notional:
        return None
    return PositionSizing(quantity=qty, notional=notional)


def round_price_down(price: float, symbol_info: SymbolInfo) -> float:
    """Round a price down to the symbol's tick size. Used for exit
    orders (take profit, stop trigger, stop limit) - rounding down
    means the take profit fills at least as easily as intended and the
    stop triggers at least as readily, never a tick more favorable to
    us than the price we calculated."""
    return floor_to_step(price, symbol_info.tick_size)
