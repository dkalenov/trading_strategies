"""Exchange symbol filter parsing - tick_size, step_size, notional limits.

Ported 1:1 from algofactory_bot/exchange/filters.py. tick_size and
step_size are stored as decimal-places int (vendor convention):
0.001 -> 3, 0.01 -> 2, 0.1 -> 1, 1.0 -> 0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """Parsed exchange filters for a single symbol."""
    symbol: str
    tick_size: int
    step_size: int
    min_qty: float
    max_qty: float
    min_notional: float
    max_notional: float
    leverage_cap: int


def _decimal_places(value: float) -> int:
    if value > 0:
        return -int(math.log10(value))
    return 0


def _get_float(d: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


def build_symbol_info_map(exchange_info: dict) -> dict[str, SymbolInfo]:
    """Parse exchangeInfo JSON into a SymbolInfo map."""
    result: dict[str, SymbolInfo] = {}
    symbols = exchange_info.get("symbols", []) if isinstance(exchange_info, dict) else []

    for sym_data in symbols:
        symbol = sym_data.get("symbol", "")
        status = sym_data.get("status", "")
        if not symbol or status != "TRADING":
            continue

        tick_size = step_size = 0
        min_qty = max_qty = min_notional = max_notional = 0.0

        for f in sym_data.get("filters", []):
            ftype = f.get("filterType", "")
            if ftype == "PRICE_FILTER":
                tick_size = _decimal_places(_get_float(f, "tickSize"))
            elif ftype == "LOT_SIZE":
                step_size = _decimal_places(_get_float(f, "stepSize"))
                min_qty = _get_float(f, "minQty")
                max_qty = _get_float(f, "maxQty")
            elif ftype == "MIN_NOTIONAL":
                min_notional = _get_float(f, "notional", "minNotional")

        leverage_cap = int(float(sym_data.get("maintMarginPercent", "50")))
        leverage_cap = max(1, min(125, 100 // max(1, leverage_cap)))

        result[symbol] = SymbolInfo(
            symbol=symbol, tick_size=tick_size, step_size=step_size,
            min_qty=min_qty, max_qty=max_qty, min_notional=min_notional,
            max_notional=max_notional, leverage_cap=leverage_cap,
        )
    return result


def default_symbol_info(symbol: str, price: float) -> SymbolInfo:
    """Reasonable filter defaults for backtest/dry-run when exchangeInfo
    hasn't been fetched (offline CSV backtests). Precision scales with
    price so quantities for micro-cap coins aren't rounded to zero.
    """
    if price >= 100:
        step, tick = 3, 2
    elif price >= 1:
        step, tick = 2, 4
    elif price >= 0.01:
        step, tick = 0, 6
    else:
        step, tick = 0, 8
    return SymbolInfo(
        symbol=symbol, tick_size=tick, step_size=step,
        min_qty=0.0, max_qty=1e12, min_notional=5.0, max_notional=1e12,
        leverage_cap=20,
    )
