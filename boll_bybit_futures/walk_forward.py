"""
Bollinger Bands Strategy — Walk-Forward Optimization module.

Splits data into rolling train/test windows:
1. Optimize parameters on training set
2. Test on out-of-sample period
3. Aggregate walk-forward results
"""

import json
import sys
import os
import pandas as pd
import numpy as np
from itertools import product
from datetime import datetime
from typing import Optional

from config import Config
from strategy import StrategyParams
from backtester import Backtester, BacktestStats
from utils import download_klines_bybit


def interval_to_bars_per_year(interval: str) -> int:
    mapping = {
        '1m': 525600, '3m': 175200, '5m': 105120, '15m': 35040, '30m': 17520,
        '1h': 8760, '2h': 4380, '4h': 2190, '6h': 1460, '8h': 1095, '12h': 730,
        '1d': 365, '3d': 122, '1w': 52, '1M': 12,
    }
    return mapping.get(interval, 8760)


def make_windows(start_date: str, end_date: str,
                 train_months: int = 12, test_months: int = 3,
                 step_months: int = 3) -> list[tuple[str, str, str, str]]:
    from dateutil.relativedelta import relativedelta

    s = datetime.strptime(start_date, '%Y-%m')
    e = datetime.strptime(end_date, '%Y-%m')

    windows = []
    train_start = s

    while True:
        train_end = train_start + relativedelta(months=train_months) - relativedelta(days=1)
        test_start = train_end + relativedelta(days=1)
        test_end = test_start + relativedelta(months=test_months) - relativedelta(days=1)

        if test_end > e:
            test_end = e
            if test_start >= test_end:
                break

        windows.append((
            train_start.strftime('%Y-%m'),
            train_end.strftime('%Y-%m'),
            test_start.strftime('%Y-%m'),
            test_end.strftime('%Y-%m'),
        ))

        train_start += relativedelta(months=step_months)

        if train_start + relativedelta(months=train_months) > e:
            break

    return windows


def optimize_on_train(df_train: pd.DataFrame, strategy_variant: str,
                      param_grid: dict, bars_per_year: int,
                      initial_capital: float = 100000,
                      max_evals: int = 100) -> tuple[dict, list[dict]]:
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(product(*values))

    if len(combinations) > max_evals:
        indices = np.random.choice(len(combinations), max_evals, replace=False)
        combinations = [combinations[i] for i in indices]

    results = []
    best_sharpe = -999
    best_return = -999
    best_params = {}

    for combo in combinations:
        param_dict = dict(zip(keys, combo))
        params = StrategyParams(
            bb_timeperiod=param_dict.get('bb_timeperiod', 20),
            bb_nbdevup=param_dict.get('bb_nbdevup', 2.0),
            bb_nbdevdn=param_dict.get('bb_nbdevdn', param_dict.get('bb_nbdevup', 2.0)),
            rsi_period=14,
            squeeze_atr_period=14,
            squeeze_threshold=0.5,
            take_profit_multiplier=param_dict.get('take_profit_multiplier', 3.0),
            stop_loss_multiplier=param_dict.get('stop_loss_multiplier', 1.5),
            trailing_stop_pct=2.0,
        )

        bt = Backtester(
            initial_capital=initial_capital,
            risk_pct=0.01,
            commission_rate=0.00055,  # Bybit standard non-VIP taker fee
            slippage_rate=0.0002,
            funding_rate=0.0001,
            max_leverage=20,
            bars_per_year=bars_per_year,
        )

        try:
            stats, trades = bt.run(df_train, strategy_variant, params)
        except Exception:
            continue

        result = {
            'params': param_dict,
            'return_pct': round(stats.total_return_pct, 2),
            'sharpe': round(stats.sharpe_ratio, 3),
            'max_drawdown': round(stats.max_drawdown_pct, 2),
            'win_rate': round(stats.win_rate, 1),
            'trades': stats.total_trades,
        }
        results.append(result)

        if stats.sharpe_ratio > best_sharpe or (
            stats.sharpe_ratio == best_sharpe and stats.total_return_pct > best_return
        ):
            best_sharpe = stats.sharpe_ratio
            best_return = stats.total_return_pct
            best_params = param_dict

    return best_params, results


def run_single_window(df_train, df_test, strategy_variant, params, bars_per_year,
                      initial_capital=100000):
    bt = Backtester(
        initial_capital=initial_capital,
        risk_pct=0.01,
        commission_rate=0.00055,  # Bybit standard non-VIP taker fee
        slippage_rate=0.0002,
        funding_rate=0.0001,
        max_leverage=20,
        bars_per_year=bars_per_year,
    )

    sp = StrategyParams(
        bb_timeperiod=params.get('bb_timeperiod', 20),
        bb_nbdevup=params.get('bb_nbdevup', 2.0),
        bb_nbdevdn=params.get('bb_nbdevdn', params.get('bb_nbdevup', 2.0)),
        rsi_period=14,
        squeeze_atr_period=14,
        squeeze_threshold=0.5,
        take_profit_multiplier=params.get('take_profit_multiplier', 3.0),
        stop_loss_multiplier=params.get('stop_loss_multiplier', 1.5),
        trailing_stop_pct=2.0,
    )

    train_stats, train_trades = bt.run(df_train, strategy_variant, sp)
    test_stats, test_trades = bt.run(df_test, strategy_variant, sp)

    return train_stats, test_stats, train_trades, test_trades


def walk_forward(symbol: str, interval: str, start_date: str, end_date: str,
                 strategy_variant: str = 'basic',
                 train_months: int = 12, test_months: int = 3, step_months: int = 3,
                 param_grid: dict = None, max_evals: int = 100,
                 klines_dir: str = 'klines', verbose: bool = True) -> dict:
    if param_grid is None:
        param_grid = {
            'bb_timeperiod': [10, 15, 20, 25, 30],
            'bb_nbdevup': [1.5, 2.0, 2.5, 3.0],
            'take_profit_multiplier': [2.0, 3.0, 4.0],
            'stop_loss_multiplier': [1.0, 1.5, 2.0],
        }

    bars_per_year = interval_to_bars_per_year(interval)

    if verbose:
        print(f"\nWalk-Forward Analysis: {symbol} {interval}")
        print(f"Period: {start_date} — {end_date}")
        print(f"Window: {train_months}m train / {test_months}m test / {step_months}m step")

    df_full = download_klines_bybit(symbol, interval, start_date, end_date, klines_dir)
    df_full.index = pd.to_datetime(df_full.index).tz_localize(None) if df_full.index.tz else df_full.index
    if verbose:
        print(f"Data: {len(df_full)} candles: {df_full.index[0]} — {df_full.index[-1]}")

    windows = make_windows(start_date, end_date, train_months, test_months, step_months)
    if verbose:
        print(f"Windows: {len(windows)}")
        for i, (ts, te, os_, oe) in enumerate(windows):
            print(f"  [{i+1}] Train: {ts}—{te} | Test: {os_}—{oe}")

    window_results = []
    all_test_trades = []

    for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
        if verbose:
            print(f"\n--- Window {i+1}/{len(windows)} ---")
            print(f"  Train: {train_start} — {train_end}")
            print(f"  Test:  {test_start} — {test_end}")

        df_train = df_full[train_start:train_end].copy()
        df_test = df_full[test_start:test_end].copy()
        # Out-of-sample must be strictly after in-sample: fail loudly, never silently overlap.
        assert df_train.index.intersection(df_test.index).empty, (
            f"train/test overlap in window {train_start}..{train_end} / {test_start}..{test_end}")
        if len(df_train) and len(df_test):
            assert df_train.index.max() < df_test.index.min(), "test window starts before train ends"

        if len(df_train) < 200:
            if verbose:
                print(f"  SKIP: training data too short ({len(df_train)} bars)")
            continue
        if len(df_test) < 50:
            if verbose:
                print(f"  SKIP: test data too short ({len(df_test)} bars)")
            continue

        best_params, opt_results = optimize_on_train(
            df_train, strategy_variant, param_grid, bars_per_year,
            max_evals=max_evals
        )

        if not best_params:
            if verbose:
                print(f"  SKIP: optimization found no valid params")
            continue

        if verbose:
            print(f"  Best train params: {best_params}")
            if opt_results:
                by_sharpe = sorted(opt_results, key=lambda x: -x['sharpe'])
                if by_sharpe:
                    print(f"  Best train Sharpe: {by_sharpe[0]['sharpe']:.3f} "
                          f"Return: {by_sharpe[0]['return_pct']:+.2f}%")

        train_stats, test_stats, train_trades, test_trades = run_single_window(
            df_train, df_test, strategy_variant, best_params, bars_per_year
        )

        default_bt = Backtester(
            initial_capital=100000, risk_pct=0.01, commission_rate=0.00055,  # Bybit standard non-VIP taker fee
            slippage_rate=0.0002, funding_rate=0.0001,
            max_leverage=20, bars_per_year=bars_per_year,
        )
        default_params = StrategyParams()
        default_test_stats, _ = default_bt.run(df_test, strategy_variant, default_params)

        window_result = {
            'window': i + 1,
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
            'optimal_params': best_params,
            'train_return': round(train_stats.total_return_pct, 2),
            'train_sharpe': round(train_stats.sharpe_ratio, 3),
            'train_maxdd': round(train_stats.max_drawdown_pct, 2),
            'train_winrate': round(train_stats.win_rate, 1),
            'train_trades': train_stats.total_trades,
            'test_return': round(test_stats.total_return_pct, 2),
            'test_sharpe': round(test_stats.sharpe_ratio, 3),
            'test_maxdd': round(test_stats.max_drawdown_pct, 2),
            'test_winrate': round(test_stats.win_rate, 1),
            'test_trades': test_stats.total_trades,
            'test_pf': round(test_stats.profit_factor, 2),
            'test_expectancy': round(test_stats.expectancy, 2),
            'default_test_return': round(default_test_stats.total_return_pct, 2),
            'default_test_sharpe': round(default_test_stats.sharpe_ratio, 3),
        }
        window_results.append(window_result)
        all_test_trades.extend(test_trades)

        if verbose:
            print(f"  Train: {train_stats.total_return_pct:+.2f}% Sharpe={train_stats.sharpe_ratio:.3f} "
                  f"WR={train_stats.win_rate:.0f}% DD={train_stats.max_drawdown_pct:.1f}% "
                  f"Trades={train_stats.total_trades}")
            print(f"  Test:  {test_stats.total_return_pct:+.2f}% Sharpe={test_stats.sharpe_ratio:.3f} "
                  f"WR={test_stats.win_rate:.0f}% DD={test_stats.max_drawdown_pct:.1f}% "
                  f"Trades={test_stats.total_trades} PF={test_stats.profit_factor:.2f}")
            print(f"  Default on test: {default_test_stats.total_return_pct:+.2f}% "
                  f"Sharpe={default_test_stats.sharpe_ratio:.3f}")

    if not window_results:
        return {'windows': [], 'aggregate': {}}

    agg = aggregate_wf_results(window_results)
    agg['symbol'] = symbol
    agg['interval'] = interval
    agg['strategy'] = strategy_variant
    agg['period'] = f"{start_date} — {end_date}"
    agg['window_config'] = f"{train_months}m train / {test_months}m test / {step_months}m step"

    if verbose:
        print_aggregate(agg, window_results)

    return {
        'windows': window_results,
        'aggregate': agg,
        'test_trades': [vars(t) if hasattr(t, '__dict__') else t for t in all_test_trades],
    }


def aggregate_wf_results(window_results: list[dict]) -> dict:
    test_returns = [w['test_return'] for w in window_results]
    train_returns = [w['train_return'] for w in window_results]
    test_sharpes = [w['test_sharpe'] for w in window_results]
    train_sharpes = [w['train_sharpe'] for w in window_results]
    test_maxdds = [w['test_maxdd'] for w in window_results]
    test_winrates = [w['test_winrate'] for w in window_results]
    test_trades = [w['test_trades'] for w in window_results]
    test_pfs = [w['test_pf'] for w in window_results]

    profitable_windows = sum(1 for r in test_returns if r > 0)
    total_windows = len(window_results)

    avg_train = np.mean(train_returns) if train_returns else 0
    avg_test = np.mean(test_returns) if test_returns else 0
    overfit_ratio = avg_train / avg_test if avg_test != 0 else float('inf')

    avg_train_sharpe = np.mean(train_sharpes) if train_sharpes else 0
    avg_test_sharpe = np.mean(test_sharpes) if test_sharpes else 0
    wf_efficiency = avg_test_sharpe / avg_train_sharpe if avg_train_sharpe != 0 else 0

    compound = 1.0
    for r in test_returns:
        compound *= (1 + r / 100)
    compound_return = (compound - 1) * 100

    return {
        'total_windows': total_windows,
        'profitable_windows': profitable_windows,
        'profitable_pct': round(profitable_windows / total_windows * 100, 1),
        'avg_test_return': round(avg_test, 2),
        'avg_test_sharpe': round(avg_test_sharpe, 3),
        'avg_test_maxdd': round(np.mean(test_maxdds), 2),
        'avg_test_winrate': round(np.mean(test_winrates), 1),
        'avg_test_trades': round(np.mean(test_trades), 1),
        'avg_test_pf': round(np.mean(test_pfs), 2),
        'median_test_return': round(np.median(test_returns), 2),
        'worst_test_return': round(min(test_returns), 2),
        'best_test_return': round(max(test_returns), 2),
        'compound_test_return': round(compound_return, 2),
        'avg_train_return': round(avg_train, 2),
        'avg_train_sharpe': round(avg_train_sharpe, 3),
        'overfit_ratio': round(overfit_ratio, 2),
        'wf_efficiency': round(wf_efficiency, 3),
    }


def print_aggregate(agg: dict, window_results: list[dict]):
    print(f"\n{'='*70}")
    print(f"  WALK-FORWARD AGGREGATE — {agg['symbol']} / {agg['strategy']}")
    print(f"  {agg['window_config']}")
    print(f"{'='*70}")
    print(f"  Windows:             {agg['total_windows']}")
    print(f"  Profitable windows:  {agg['profitable_windows']}/{agg['total_windows']} ({agg['profitable_pct']:.0f}%)")
    print(f"  Avg test return:     {agg['avg_test_return']:+.2f}%")
    print(f"  Median test return:  {agg['median_test_return']:+.2f}%")
    print(f"  Best test return:    {agg['best_test_return']:+.2f}%")
    print(f"  Worst test return:   {agg['worst_test_return']:+.2f}%")
    print(f"  Compound return:     {agg['compound_test_return']:+.2f}%")
    print(f"  Avg test Sharpe:     {agg['avg_test_sharpe']:.3f}")
    print(f"  Avg test MaxDD:      {agg['avg_test_maxdd']:.2f}%")
    print(f"  Avg test WinRate:    {agg['avg_test_winrate']:.1f}%")
    print(f"  Avg test PF:         {agg['avg_test_pf']:.2f}")
    print(f"  Avg train return:    {agg['avg_train_return']:+.2f}%")
    print(f"  Overfit ratio:       {agg['overfit_ratio']:.2f}x (train/test)")
    print(f"  WF efficiency:       {agg['wf_efficiency']:.3f} (test_sharpe/train_sharpe)")
    print(f"{'='*70}")

    print(f"\n  Window Details:")
    print(f"  {'#':>3} {'Train Period':<18} {'Test Period':<18} {'Train Ret':>10} {'Test Ret':>10} {'Test Sharpe':>12} {'Test DD':>8}")
    print(f"  {'─'*83}")
    for w in window_results:
        print(f"  {w['window']:>3} "
              f"{w['train_start']}—{w['train_end']:<8} "
              f"{w['test_start']}—{w['test_end']:<8} "
              f"{w['train_return']:>+9.2f}% "
              f"{w['test_return']:>+9.2f}% "
              f"{w['test_sharpe']:>11.3f} "
              f"{w['test_maxdd']:>7.1f}%")

    from collections import Counter
    all_params = [tuple(sorted(w['optimal_params'].items())) for w in window_results]
    param_counts = Counter(all_params)
    print(f"\n  Most common optimal params:")
    for params, count in param_counts.most_common(5):
        pct = count / len(window_results) * 100
        param_dict = dict(params)
        print(f"    BB({param_dict.get('bb_timeperiod', '?')}, {param_dict.get('bb_nbdevup', '?')}) "
              f"— {count}/{len(window_results)} ({pct:.0f}%)")
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Bollinger Bands Walk-Forward Optimization')
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--interval', default='5m')
    parser.add_argument('--start', default='2024-01')
    parser.add_argument('--end', default='2025-06')
    parser.add_argument('--strategy', default='basic',
                        choices=['basic', 'rsi_filter', 'squeeze', 'mean_reversion'])
    parser.add_argument('--train-months', type=int, default=12)
    parser.add_argument('--test-months', type=int, default=3)
    parser.add_argument('--step-months', type=int, default=3)
    parser.add_argument('--max-evals', type=int, default=100)
    parser.add_argument('--output', default=None, help='Save results to JSON')
    parser.add_argument('--seed', type=int, default=42, help='random seed for the parameter search')

    args = parser.parse_args()
    np.random.seed(args.seed)

    results = walk_forward(
        args.symbol, args.interval, args.start, args.end,
        strategy_variant=args.strategy,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
        max_evals=args.max_evals,
    )

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to {args.output}")


if __name__ == '__main__':
    main()
