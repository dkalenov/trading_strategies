import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from utils import wilder_atr, wilder_atr_incremental, quantize_down, quantize_up, quantize_price


def test_wilder_atr_matches_hand_calc():
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


def test_quantize_down_no_float_artifacts():
    # real reproduction of a reported bug: naive int(value/step)*step
    # float arithmetic gives 0.0006000000000000001 for this exact input.
    price, step = 14696.844346455337, 0.0001
    result = quantize_down(10.0 / price, step)
    assert result == 0.0006
    assert repr(result) == "0.0006"


def test_quantize_down_rounds_toward_zero_not_nearest():
    assert quantize_down(0.0129, 0.001) == 0.012
    assert quantize_down(0.0009, 0.001) == 0.0


def test_quantize_up_bumps_to_next_step():
    assert quantize_up(0.0121, 0.001) == 0.013
    assert quantize_up(0.001, 0.001) == 0.001


def test_quantize_price_rounds_to_nearest_tick():
    # the reported bug: 63289.17064050636 must land on a 0.1 grid for BTCUSDT
    assert quantize_price(63289.17064050636, 0.1) == 63289.2
    assert quantize_price(63289.14, 0.1) == 63289.1
    assert quantize_price(100.0, 0.01) == 100.0
