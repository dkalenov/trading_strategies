"""
Bollinger Bands Strategy — Backtest verification.

Verifies no look-ahead bias, correct SL/TP execution, PnL math.
"""

import numpy as np
import pandas as pd
from strategy import StrategyParams
from backtester import Backtester
from utils import download_klines_bybit
from config import Config


def verify_trade_timestamps(df, trades):
    """Structural check only: timestamps exist and are ordered.

    This does NOT prove the absence of look-ahead. See verify_prefix_invariance.
    """
    errors = []
    for t in trades:
        if t.entry_time not in df.index:
            errors.append(f"Entry time {t.entry_time} not in dataframe index")
        if t.exit_time not in df.index:
            errors.append(f"Exit time {t.exit_time} not in dataframe index")
        if t.entry_time >= t.exit_time:
            errors.append(f"Entry >= exit: {t.entry_time} >= {t.exit_time}")
    return errors


def verify_prefix_invariance(df, bt, variant, params, cut_fractions=(0.5, 0.7, 0.9)):
    """Causality test: results on a prefix of the data must match the same
    part of the full run. If any signal used future bars, trades would change
    when future data is removed."""
    errors = []
    _, full = bt.run(df, variant, params)
    key = lambda t: (t.entry_time, t.exit_time, t.side, t.exit_reason)
    for frac in cut_fractions:
        cut = int(len(df) * frac)
        prefix_df = df.iloc[:cut]
        _, pre = bt.run(prefix_df, variant, params)
        end = prefix_df.index[-1]
        got = [key(t) for t in pre if t.exit_reason != 'END_OF_DATA']
        want = [key(t) for t in full if t.exit_time <= end]
        if got != want:
            errors.append(f"prefix {frac:.0%}: {len(got)} trades vs {len(want)} in the full run")
    return errors


def verify_pnl_math(trades):
    """Verify PnL calculations are consistent."""
    errors = []
    for t in trades:
        if t.side == 'LONG':
            expected_gross = (t.exit_price - t.entry_price) * t.quantity
        else:
            expected_gross = (t.entry_price - t.exit_price) * t.quantity

        # Net PnL = gross - commission - funding. Slippage is already inside
        # the fill prices, so it must NOT be subtracted a second time.
        expected_net = expected_gross - t.commission - t.funding_cost
        actual_net = t.pnl

        if abs(expected_net - actual_net) > 0.01:
            errors.append(f"Trade PnL mismatch: expected {expected_net:.2f}, got {actual_net:.2f}")

        if t.notional != t.quantity * t.entry_price:
            errors.append(f"Notional mismatch: {t.notional} != {t.quantity} * {t.entry_price}")

    return errors


def verify_sl_tp(trades, slippage_rate=0.0):
    """Verify SL and TP are respected."""
    errors = []
    for t in trades:
        if t.exit_reason == 'STOP_LOSS':
            tol = 1 + 1e-9
            if t.side == 'LONG' and t.exit_price > t.stop_loss * tol:
                errors.append(f"LONG SL exit {t.exit_price} > SL {t.stop_loss}")
            elif t.side == 'SHORT' and t.exit_price < t.stop_loss / tol:
                errors.append(f"SHORT SL exit {t.exit_price} < SL {t.stop_loss}")
        elif t.exit_reason == 'TAKE_PROFIT':
            if t.side == 'LONG' and t.exit_price < t.take_profit * (1 - slippage_rate) * (1 - 1e-9):
                errors.append(f"LONG TP exit {t.exit_price} < TP {t.take_profit}")
            elif t.side == 'SHORT' and t.exit_price > t.take_profit * (1 + slippage_rate) * (1 + 1e-9):
                errors.append(f"SHORT TP exit {t.exit_price} > TP {t.take_profit}")
    return errors


def main():
    config = Config()
    symbol = config.symbol
    interval = config.interval

    print(f"Verifying backtest: {symbol} {interval}")
    print(f"Period: {config.start_date} — {config.end_date}")

    df = download_klines_bybit(symbol, interval, config.start_date, config.end_date,
                                config.klines_dir)
    print(f"Data: {len(df)} candles")

    params = StrategyParams(**config.strategy_params)

    bt = Backtester(
        initial_capital=config.initial_capital,
        risk_pct=config.risk_pct / 100,
        max_leverage=config.leverage,
        commission_rate=config.commission,
        slippage_rate=config.slippage,
        funding_rate=config.funding_rate,
        interval=interval,
    )

    for variant in ['basic', 'rsi_filter', 'squeeze', 'mean_reversion']:
        print(f"\n--- Verifying variant: {variant} ---")
        stats, trades = bt.run(df, variant, params)

        print(f"  Trades: {stats.total_trades}")
        print(f"  Return: {stats.total_return_pct:+.2f}%")
        print(f"  Sharpe: {stats.sharpe_ratio:.3f}")

        errors = []
        errors.extend(verify_trade_timestamps(df, trades))
        errors.extend(verify_pnl_math(trades))
        errors.extend(verify_sl_tp(trades, config.slippage))
        errors.extend(verify_prefix_invariance(df, bt, variant, params))
        expected_final = config.initial_capital + sum(x.pnl for x in trades)
        if abs(stats.final_capital - expected_final) > 1e-6 * max(1.0, abs(expected_final)):
            errors.append(f"final capital {stats.final_capital} != initial + sum(pnl) {expected_final}")

        if errors:
            print(f"  ERRORS:")
            for e in errors:
                print(f"    - {e}")
        else:
            print(f"  ALL CHECKS PASSED")


if __name__ == '__main__':
    main()
