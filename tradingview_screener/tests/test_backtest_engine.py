import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from backtest.engine import run_backtest, BacktestConfig
from config import StrategyConfig


def _mk_candle(date, o, h, l, c, symbol):
    return dict(Date=pd.Timestamp(date, tz="UTC"), Open=o, High=h, Low=l, Close=c,
                Volume=1000.0, Symbol=symbol)


def test_synthetic_long_hits_stop_directly():
    rows = []
    for i in range(20):
        rows.append(_mk_candle(f"2025-01-01T{i:02d}:00:00", 100, 101, 99, 100, "TESTUSDT"))
    rows.append(_mk_candle("2025-01-02T00:00:00", 100, 101, 99, 100, "TESTUSDT"))
    rows.append(_mk_candle("2025-01-02T01:00:00", 100, 100.5, 90, 91, "TESTUSDT"))
    klines = pd.DataFrame(rows)

    signals = pd.DataFrame([
        dict(symbol="TESTUSDT", signal="STRONG_BUY", t4h=pd.Timestamp("2025-01-02T00:00:00", tz="UTC")),
        dict(symbol="BTCUSDT", signal="NEUTRAL", t4h=pd.Timestamp("2025-01-02T00:00:00", tz="UTC")),
    ])

    cfg = BacktestConfig(strategy=StrategyConfig(atr_length=5))
    trades, open_at_end = run_backtest(klines, signals, cfg)

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t.symbol == "TESTUSDT"
    assert t.direction == "LONG"
    assert t.exit_reason == "STOP"
    assert t.pnl_usd < 0


def test_synthetic_no_trade_when_btc_filter_blocks():
    rows = []
    for i in range(20):
        rows.append(_mk_candle(f"2025-01-01T{i:02d}:00:00", 100, 101, 99, 100, "TESTUSDT"))
    rows.append(_mk_candle("2025-01-02T00:00:00", 100, 101, 99, 100, "TESTUSDT"))
    klines = pd.DataFrame(rows)

    signals = pd.DataFrame([
        dict(symbol="TESTUSDT", signal="STRONG_BUY", t4h=pd.Timestamp("2025-01-02T00:00:00", tz="UTC")),
        dict(symbol="BTCUSDT", signal="STRONG_SELL", t4h=pd.Timestamp("2025-01-02T00:00:00", tz="UTC")),
    ])

    cfg = BacktestConfig(strategy=StrategyConfig(atr_length=5))
    trades, open_at_end = run_backtest(klines, signals, cfg)
    assert trades.empty
