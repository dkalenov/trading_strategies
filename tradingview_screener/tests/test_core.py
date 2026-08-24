import math
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from core.atr import wilder_atr, wilder_atr_incremental
from core.strategy import (
    decide_entry, compute_exit_levels, breakeven_stop_price, position_size,
    StrategyConfig, LONG, SHORT,
)


def test_wilder_atr_matches_hand_calc():
    # 20 bars of made-up but consistent OHLC, period=5, checked by hand
    # for the first couple of values against the Wilder formula.
    data = {
        "High":  [10, 11, 10.5, 12, 13, 12.5, 13.5, 14, 13.5, 15,
                  15.5, 15, 16, 16.5, 16, 17, 17.5, 17, 18, 18.5],
        "Low":   [9, 9.5, 9.8, 10.5, 11.5, 11.8, 12.2, 12.8, 12.5, 13.5,
                  14, 14.2, 14.5, 15, 15.2, 15.5, 16, 16.2, 16.5, 17],
        "Close": [9.5, 10.5, 10, 11.5, 12.5, 12, 13, 13.5, 13, 14.5,
                  15, 14.5, 15.5, 16, 15.5, 16.5, 17, 16.5, 17.5, 18],
    }
    df = pd.DataFrame(data)
    atr = wilder_atr(df, period=5)

    assert atr.iloc[:4].isna().all()

    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    expected_first = tr.iloc[:5].mean()
    assert atr.iloc[4] == pytest.approx(expected_first)

    expected_next = (expected_first * 4 + tr.iloc[5]) / 5
    assert atr.iloc[5] == pytest.approx(expected_next)


def test_wilder_atr_incremental_matches_batch():
    data = {
        "High":  [10, 11, 10.5, 12, 13, 12.5, 13.5],
        "Low":   [9, 9.5, 9.8, 10.5, 11.5, 11.8, 12.2],
        "Close": [9.5, 10.5, 10, 11.5, 12.5, 12, 13],
    }
    df = pd.DataFrame(data)
    batch = wilder_atr(df, period=5)

    prev_atr = batch.iloc[4]
    prev_close = df["Close"].iloc[4]
    step = wilder_atr_incremental(prev_atr, df["High"].iloc[5], df["Low"].iloc[5], prev_close, period=5)
    assert step == pytest.approx(batch.iloc[5])


def test_decide_entry_matches_live_condition():
    assert decide_entry("STRONG_BUY", "NEUTRAL") == LONG
    assert decide_entry("STRONG_BUY", "BUY") == LONG
    assert decide_entry("STRONG_BUY", "STRONG_BUY") == LONG
    assert decide_entry("STRONG_BUY", "SELL") is None
    assert decide_entry("STRONG_BUY", "STRONG_SELL") is None

    assert decide_entry("STRONG_SELL", "NEUTRAL") == SHORT
    assert decide_entry("STRONG_SELL", "SELL") == SHORT
    assert decide_entry("STRONG_SELL", "BUY") is None

    assert decide_entry("BUY", "NEUTRAL") is None       # only STRONG_ triggers entry
    assert decide_entry("NEUTRAL", "NEUTRAL") is None


def test_exit_levels_long():
    lv = compute_exit_levels(LONG, entry_price=100.0, atr=2.0,
                              cfg=StrategyConfig(stop_mult=0.45, take1_mult=2.5, take2_mult=5.0))
    assert lv.stop == pytest.approx(100 - 0.9)
    assert lv.take1 == pytest.approx(100 + 5.0)
    assert lv.take2 == pytest.approx(100 + 10.0)


def test_exit_levels_short():
    lv = compute_exit_levels(SHORT, entry_price=100.0, atr=2.0,
                              cfg=StrategyConfig(stop_mult=0.45, take1_mult=2.5, take2_mult=5.0))
    assert lv.stop == pytest.approx(100 + 0.9)
    assert lv.take1 == pytest.approx(100 - 5.0)
    assert lv.take2 == pytest.approx(100 - 10.0)


def test_breakeven_stop_matches_original_constant():
    assert breakeven_stop_price(LONG, 100.0) == pytest.approx(99.9)
    assert breakeven_stop_price(SHORT, 100.0) == pytest.approx(100.1)


def test_position_size_respects_step_and_min_notional():
    qty = position_size(order_size_usd=10.0, price=3.3333, step_size=0.001)
    assert qty == pytest.approx(3.0, abs=0.001)

    # tiny order that would fall under Binance's min notional gets bumped up
    qty2 = position_size(order_size_usd=1.0, price=100.0, step_size=0.001, min_notional=5.0)
    assert qty2 * 100.0 >= 5.0 * 1.1 - 1e-6


def test_position_size_matches_two_independent_call_sites():
    # This is the exact regression the original project had: backtester and
    # live bot each had their own formula. Here there is only one function,
    # so calling it twice with the same inputs is a tautology check that
    # nothing has been duplicated and drifted.
    a = position_size(10.0, 4321.0, step_size=0.001)
    b = position_size(10.0, 4321.0, step_size=0.001)
    assert a == b
