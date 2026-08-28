import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from coint_pairs.sizing import vol_parity_notional, calculate_qty


def test_vol_parity_splits_full_budget():
    rng = np.random.default_rng(0)
    log1 = np.cumsum(rng.normal(scale=0.01, size=100))
    log2 = np.cumsum(rng.normal(scale=0.03, size=100))  # 3x more volatile
    d1, d2 = vol_parity_notional(log1, log2, hedge=1.0, capital=100_000, max_notional_per_pair=0.05)
    assert abs((d1 + d2) - 5_000) < 1e-6, "the two legs should sum to exactly the per-pair cap"
    assert d1 > d2, "the less volatile leg should get the larger dollar allocation"


def test_calculate_qty_respects_hard_cap():
    qty1, qty2 = calculate_qty(
        dollar1=4_000, dollar2=4_000, price1=100, price2=50,
        capital=100_000, max_notional_per_pair=0.05,
    )
    notional = abs(qty1) * 100 + abs(qty2) * 50
    assert notional <= 5_000 + 1e-6


def test_calculate_qty_zero_price_is_safe():
    qty1, qty2 = calculate_qty(dollar1=100, dollar2=100, price1=0, price2=10,
                                capital=100_000, max_notional_per_pair=0.05)
    assert qty1 == 0.0
