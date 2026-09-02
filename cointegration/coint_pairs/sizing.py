"""
Position sizing for a pair trade: volatility parity between the two legs,
capped at a maximum notional per pair. This is a lightly cleaned version of
the original cointegration_calculate_size.py - the logic was already correct,
no bugs found here.
"""

from __future__ import annotations

import numpy as np


def vol_parity_notional(
    log1: np.ndarray,
    log2: np.ndarray,
    hedge: float,
    capital: float,
    max_notional_per_pair: float = 0.05,
    lookback: int = 60,
) -> tuple[float, float]:
    """Dollar allocation for each leg so both legs contribute equal risk.

    Weight is inverse to each leg's realized volatility over `lookback`
    bars of log returns, with the second leg additionally scaled by the
    hedge ratio (since qty2 in "spread units" is hedge times qty1).
    """
    cap_pair_usd = capital * max_notional_per_pair
    r1 = np.diff(log1[-lookback:]) if len(log1) >= lookback else np.diff(log1)
    r2 = np.diff(log2[-lookback:]) if len(log2) >= lookback else np.diff(log2)
    sigma1 = np.std(r1) if len(r1) > 0 else 0.0
    sigma2 = np.std(r2) if len(r2) > 0 else 0.0
    w1_raw = 1.0 / sigma1 if sigma1 > 0 else 0.0
    w2_raw = abs(hedge) / sigma2 if sigma2 > 0 else 0.0
    total_w = w1_raw + w2_raw
    if total_w <= 0:
        return 0.0, 0.0
    w1 = w1_raw / total_w
    w2 = w2_raw / total_w
    return float(cap_pair_usd * w1), float(cap_pair_usd * w2)


def calculate_qty(
    dollar1: float,
    dollar2: float,
    price1: float,
    price2: float,
    capital: float,
    max_notional_per_pair: float = 0.05,
) -> tuple[float, float]:
    """Convert dollar allocations to quantities, enforcing a hard notional cap."""
    max_notional = capital * max_notional_per_pair
    total = abs(dollar1) + abs(dollar2)
    if total > max_notional and total > 0:
        scale = max_notional / total
        dollar1 *= scale
        dollar2 *= scale
    qty1 = dollar1 / price1 if price1 > 0 else 0.0
    qty2 = dollar2 / price2 if price2 > 0 else 0.0
    return float(qty1), float(qty2)
