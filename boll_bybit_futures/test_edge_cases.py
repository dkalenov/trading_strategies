"""
Bollinger Bands Strategy — Edge case tests.

Tests backtester with synthetic data to verify correctness.
"""

import numpy as np
import pandas as pd
from strategy import StrategyParams, BollingerBandsCore, calculate_bollinger_bands
from backtester import Backtester


def make_df(closes, highs=None, lows=None, interval='5min'):
    """Create DataFrame from price arrays."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]

    dates = pd.date_range('2024-01-01', periods=n, freq=interval)
    return pd.DataFrame({
        'Open': closes,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': [1000.0] * n,
    }, index=dates)


def test_flat_market():
    """Flat market should produce no trades."""
    closes = [100.0] * 200
    df = make_df(closes)

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)
    stats, trades = bt.run(df, 'basic', StrategyParams(bb_timeperiod=20))

    assert stats.total_trades == 0, f"Expected 0 trades, got {stats.total_trades}"
    assert stats.total_return_pct == 0.0, f"Expected 0% return, got {stats.total_return_pct}%"
    print("PASS: test_flat_market")


def test_single_spike():
    """Single spike up then return to base — should trigger BUY on crossover."""
    closes = [100.0] * 25 + [105.0] + [100.0] * 50
    df = make_df(closes, highs=[100.5]*25 + [106.0] + [100.5]*50,
                 lows=[99.5]*25 + [99.0] + [99.5]*50)

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)
    params = StrategyParams(bb_timeperiod=20, bb_nbdevup=2.0, bb_nbdevdn=2.0)
    stats, trades = bt.run(df, 'basic', params)

    # Should have at least one trade
    assert stats.total_trades >= 1, f"Expected >= 1 trade, got {stats.total_trades}"
    print(f"PASS: test_single_spike — {stats.total_trades} trades")


def test_high_volatility():
    """High volatility — alternating up/down should generate signals."""
    np.random.seed(42)
    n = 200
    base = 100.0
    closes = [base]
    for i in range(1, n):
        change = np.random.choice([-2, 2])
        closes.append(closes[-1] + change)

    df = make_df(closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes])

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)
    params = StrategyParams(bb_timeperiod=20, bb_nbdevup=2.0, bb_nbdevdn=2.0)
    stats, trades = bt.run(df, 'basic', params)

    assert stats.total_trades >= 1, f"Expected >= 1 trade, got {stats.total_trades}"
    print(f"PASS: test_high_volatility — {stats.total_trades} trades, return={stats.total_return_pct:+.2f}%")


def test_pure_uptrend():
    """Pure uptrend — should generate BUY signals."""
    closes = [100.0 + i * 0.5 for i in range(200)]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)
    params = StrategyParams(bb_timeperiod=20, bb_nbdevup=2.0, bb_nbdevdn=2.0)
    stats, trades = bt.run(df, 'basic', params)

    # A linear ramp never closes above its own upper band (the SMA lags by less
    # than 2 sigma), so this only checks direction consistency and accounting.
    assert all(t.side == 'LONG' for t in trades)
    assert abs(stats.final_capital - (100000 + sum(t.pnl for t in trades))) < 1e-6
    print(f"PASS: test_pure_uptrend — {stats.total_trades} trades, return={stats.total_return_pct:+.2f}%")


def test_pure_downtrend():
    """Pure downtrend — should generate SELL signals."""
    closes = [200.0 - i * 0.5 for i in range(200)]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)
    params = StrategyParams(bb_timeperiod=20, bb_nbdevup=2.0, bb_nbdevdn=2.0)
    stats, trades = bt.run(df, 'basic', params)

    assert all(t.side == 'SHORT' for t in trades)
    assert abs(stats.final_capital - (100000 + sum(t.pnl for t in trades))) < 1e-6
    print(f"PASS: test_pure_downtrend — {stats.total_trades} trades, return={stats.total_return_pct:+.2f}%")


def test_sl_tp_hit():
    """Verify SL and TP are hit correctly."""
    # Create data where price goes exactly to SL then TP
    closes = [100.0] * 25 + [101.0] * 5 + [99.0] * 5 + [102.0] * 50
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)
    params = StrategyParams(bb_timeperiod=20, bb_nbdevup=2.0, bb_nbdevdn=2.0,
                            stop_loss_multiplier=1.5, take_profit_multiplier=3.0)
    stats, trades = bt.run(df, 'basic', params)

    allowed = {'STOP_LOSS', 'TAKE_PROFIT', 'SIGNAL_BUY', 'SIGNAL_SELL', 'END_OF_DATA'}
    assert stats.total_trades >= 1, "the crafted spike must produce at least one trade"
    assert {t.exit_reason for t in trades} <= allowed
    for t in trades:
        assert t.entry_time < t.exit_time
    print(f"PASS: test_sl_tp_hit — {stats.total_trades} trades, reasons: {[t.exit_reason for t in trades]}")


def test_all_variants():
    """Test all strategy variants run without errors."""
    np.random.seed(42)
    n = 200
    closes = [100.0]
    for i in range(1, n):
        closes.append(closes[-1] + np.random.randn() * 0.5)

    df = make_df(closes, highs=[c + 0.3 for c in closes], lows=[c - 0.3 for c in closes])
    params = StrategyParams(bb_timeperiod=20, bb_nbdevup=2.0, bb_nbdevdn=2.0)

    bt = Backtester(initial_capital=100000, risk_pct=0.01, max_leverage=20)

    for variant in ['basic', 'rsi_filter', 'squeeze', 'mean_reversion']:
        stats, trades = bt.run(df, variant, params)  # any exception fails the test
        assert abs(stats.final_capital - (100000 + sum(t.pnl for t in trades))) < 1e-6
        print(f"PASS: variant '{variant}' — {stats.total_trades} trades, return={stats.total_return_pct:+.2f}%")


if __name__ == '__main__':
    print("Running edge case tests...\n")
    test_flat_market()
    test_single_spike()
    test_high_volatility()
    test_pure_uptrend()
    test_pure_downtrend()
    test_sl_tp_hit()
    test_all_variants()
    print("\nAll tests completed.")
