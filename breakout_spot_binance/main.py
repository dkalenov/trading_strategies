"""
Breakout Spot Strategy — Main entry point.

Runs backtests, optimization, and generates reports.
"""

import argparse
import json
import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime
from itertools import product

from config import Config
from db import Database, BacktestResult, OptimizationResult
from utils import download_klines, export_trades_csv
from strategy import StrategyParams
from backtester import Backtester, BacktestStats


def interval_to_bars_per_year(interval: str) -> int:
    mapping = {
        '1m': 525600, '3m': 175200, '5m': 105120, '15m': 35040, '30m': 17520,
        '1h': 8760, '2h': 4380, '4h': 2190, '6h': 1460, '8h': 1095, '12h': 730,
        '1d': 365, '3d': 122, '1w': 52, '1M': 12,
    }
    return mapping.get(interval, 2190)


def run_backtest(config: Config, params: StrategyParams = None,
                 verbose: bool = True) -> tuple[BacktestStats, list[dict]]:
    if params is None:
        params = StrategyParams(
            lookback=config.getint('strategy', 'lookback', 20),
            volume_multiplier=config.getfloat('strategy', 'volume_multiplier', 2.0),
            atr_period=config.getint('strategy', 'atr_period', 14),
            stop_loss_multiplier=config.getfloat('strategy', 'stop_loss_multiplier', 1.5),
            trailing_stop_pct=config.getfloat('strategy', 'trailing_stop_pct', 3.0),
            min_volume_usdt=config.getfloat('strategy', 'min_volume_usdt', 1_000_000),
        )

    if verbose:
        print(f"Loading data: {config.symbol} {config.interval} {config.start_date} — {config.end_date}")
    df = download_klines(config.symbol, config.interval, config.start_date, config.end_date,
                         config.get('data', 'klines_dir', 'klines'))
    if verbose:
        print(f"Loaded {len(df)} candles: {df.index[0]} — {df.index[-1]}")

    bt = Backtester(
        initial_capital=config.initial_capital,
        risk_pct=0.02,
        commission_rate=config.commission,
        slippage_rate=config.slippage,
        bars_per_year=interval_to_bars_per_year(config.interval),
    )

    stats, trades = bt.run(df, params)

    trades_dicts = []
    for t in trades:
        trades_dicts.append({
            'entry_time': str(t.entry_time),
            'exit_time': str(t.exit_time),
            'side': t.side,
            'entry_price': round(t.entry_price, 4),
            'exit_price': round(t.exit_price, 4),
            'quantity': round(t.quantity, 8),
            'notional': round(t.notional, 2),
            'pnl': round(t.pnl, 2),
            'pnl_pct': round(t.pnl_pct, 4),
            'commission': round(t.commission, 4),
            'slippage_cost': round(t.slippage_cost, 4),
            'exit_reason': t.exit_reason,
            'stop_loss': round(t.stop_loss, 4),
            'peak_price': round(t.peak_price, 4),
            'max_favorable_pct': round(t.max_favorable_pct, 4),
            'max_adverse_pct': round(t.max_adverse_pct, 4),
        })

    if verbose:
        print_stats(stats, config.symbol)
        print(f"\nTrade log: {len(trades)} trades")

    return stats, trades_dicts


def print_stats(stats: BacktestStats, symbol: str):
    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS — {symbol} / breakout")
    print(f"{'='*60}")
    print(f"  Initial Capital:    ${stats.initial_capital:,.2f}")
    print(f"  Final Capital:      ${stats.final_capital:,.2f}")
    print(f"  Total Return:       {stats.total_return_pct:+.2f}%")
    print(f"  Max Drawdown:       {stats.max_drawdown_pct:.2f}%")
    print(f"  Sharpe Ratio:       {stats.sharpe_ratio:.3f}")
    print(f"  Sortino Ratio:      {stats.sortino_ratio:.3f}")
    print(f"  Calmar Ratio:       {stats.calmar_ratio:.3f}  (annualized return / max DD)")
    print(f"  Return / Max DD:    {stats.return_over_maxdd:.3f}")
    print(f"  Win Rate:           {stats.win_rate:.1f}% ({stats.winning_trades}W / {stats.losing_trades}L)")
    print(f"  Profit Factor:      {stats.profit_factor:.2f}")
    print(f"  Expectancy:         ${stats.expectancy:.2f}")
    print(f"  Avg Win:            {stats.avg_win_pct:+.2f}%")
    print(f"  Avg Loss:           {stats.avg_loss_pct:+.2f}%")
    print(f"  Avg Duration:       {stats.avg_trade_duration_hours:.1f}h")
    print(f"  Max Consec Wins:    {stats.max_consecutive_wins}")
    print(f"  Max Consec Losses:  {stats.max_consecutive_losses}")
    print(f"  Total Commission:   ${stats.total_commission:.2f}")
    print(f"  Total Slippage:     ${stats.total_slippage:.2f}")
    print(f"{'='*60}")


def run_monthly_analysis(config: Config, params: StrategyParams = None) -> dict:
    if params is None:
        params = StrategyParams(
            lookback=config.getint('strategy', 'lookback', 20),
            volume_multiplier=config.getfloat('strategy', 'volume_multiplier', 2.0),
            atr_period=config.getint('strategy', 'atr_period', 14),
            stop_loss_multiplier=config.getfloat('strategy', 'stop_loss_multiplier', 1.5),
            trailing_stop_pct=config.getfloat('strategy', 'trailing_stop_pct', 3.0),
            min_volume_usdt=config.getfloat('strategy', 'min_volume_usdt', 1_000_000),
        )

    df = download_klines(config.symbol, config.interval, config.start_date, config.end_date,
                         config.get('data', 'klines_dir', 'klines'))
    df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz else df.index

    monthly_results = {}
    for period, group in df.groupby(df.index.to_period('M')):
        if len(group) < params.lookback + params.atr_period + 2:
            continue

        bt = Backtester(
            initial_capital=config.initial_capital,
            risk_pct=0.02,
            commission_rate=config.commission,
            slippage_rate=config.slippage,
            bars_per_year=interval_to_bars_per_year(config.interval),
        )
        stats, trades = bt.run(group, params)
        monthly_results[str(period)] = {
            'return_pct': round(stats.total_return_pct, 2),
            'trades': stats.total_trades,
            'win_rate': round(stats.win_rate, 1),
            'max_drawdown': round(stats.max_drawdown_pct, 2),
            'sharpe': round(stats.sharpe_ratio, 3),
        }

    return monthly_results


def run_optimization(config: Config, param_grid: dict = None,
                     max_evals: int = 50, verbose: bool = True):
    if param_grid is None:
        param_grid = {
            'lookback': [10, 15, 20, 25, 30],
            'volume_multiplier': [1.5, 2.0, 2.5, 3.0],
            'stop_loss_multiplier': [1.0, 1.5, 2.0],
        }

    if verbose:
        print(f"\nOptimizing breakout on {config.symbol} {config.interval}")
        print(f"Parameter grid: {param_grid}")

    df = download_klines(config.symbol, config.interval, config.start_date, config.end_date,
                         config.get('data', 'klines_dir', 'klines'))
    if verbose:
        print(f"Data: {len(df)} candles")

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(product(*values))
    if len(combinations) > max_evals:
        indices = np.random.choice(len(combinations), max_evals, replace=False)
        combinations = [combinations[i] for i in indices]

    results = []
    best_sharpe = -999
    best_return = -999
    best_params = None

    for idx, combo in enumerate(combinations):
        param_dict = dict(zip(keys, combo))
        params = StrategyParams(
            lookback=param_dict.get('lookback', 20),
            volume_multiplier=param_dict.get('volume_multiplier', 2.0),
            atr_period=config.getint('strategy', 'atr_period', 14),
            stop_loss_multiplier=param_dict.get('stop_loss_multiplier', 1.5),
            trailing_stop_pct=config.getfloat('strategy', 'trailing_stop_pct', 3.0),
            min_volume_usdt=config.getfloat('strategy', 'min_volume_usdt', 1_000_000),
        )

        bt = Backtester(
            initial_capital=config.initial_capital,
            risk_pct=0.02,
            commission_rate=config.commission,
            slippage_rate=config.slippage,
            bars_per_year=interval_to_bars_per_year(config.interval),
        )

        try:
            stats, trades = bt.run(df, params)
        except Exception as e:
            if verbose:
                print(f"  [{idx+1}/{len(combinations)}] Error: {e}")
            continue

        result = {
            'params': param_dict,
            'return_pct': round(stats.total_return_pct, 2),
            'sharpe': round(stats.sharpe_ratio, 3),
            'max_drawdown': round(stats.max_drawdown_pct, 2),
            'win_rate': round(stats.win_rate, 1),
            'trades': stats.total_trades,
            'profit_factor': round(stats.profit_factor, 2),
        }
        results.append(result)

        if stats.sharpe_ratio > best_sharpe or (
            stats.sharpe_ratio == best_sharpe and stats.total_return_pct > best_return
        ):
            best_sharpe = stats.sharpe_ratio
            best_return = stats.total_return_pct
            best_params = param_dict

        if verbose and (idx + 1) % 10 == 0:
            print(f"  [{idx+1}/{len(combinations)}] Best so far: Sharpe={best_sharpe:.3f} Return={best_return:+.2f}% Params={best_params}")

    if verbose:
        print(f"\n{'='*60}")
        print(f"  OPTIMIZATION RESULTS — breakout")
        print(f"{'='*60}")
        print(f"  Evaluated: {len(results)} combinations")
        print(f"  Best Sharpe:  {best_sharpe:.3f}")
        print(f"  Best Return:  {best_return:+.2f}%")
        print(f"  Best Params:  {best_params}")
        print(f"{'='*60}")

        sorted_results = sorted(results, key=lambda x: (-x['sharpe'], -x['return_pct']))
        print(f"\n  Top 5 by Sharpe:")
        for i, r in enumerate(sorted_results[:5]):
            print(f"  {i+1}. Sharpe={r['sharpe']:.3f} Return={r['return_pct']:+.2f}% "
                  f"DD={r['max_drawdown']:.2f}% WR={r['win_rate']:.0f}% "
                  f"PF={r['profit_factor']:.2f} Params={r['params']}")

    db = Database(config.get('database', 'path', 'breakout_results.db'))
    opt_result = OptimizationResult(
        symbol=config.symbol,
        interval=config.interval,
        strategy_name='breakout',
        start_date=config.start_date,
        end_date=config.end_date,
        total_evals=len(results),
        best_return_pct=best_return,
        best_params_json=json.dumps(best_params) if best_params else '{}',
        best_sharpe=best_sharpe,
        best_max_drawdown_pct=next((r['max_drawdown'] for r in results if r['params'] == best_params), 0),  # drawdown of the selected config
        all_results_json=json.dumps(results[:200]),
    )
    db.save_optimization_result(opt_result)
    db.close()

    return best_params, results


def save_result_to_db(db: Database, config: Config, stats: BacktestStats,
                       params: StrategyParams):
    result = BacktestResult(
        symbol=config.symbol,
        interval=config.interval,
        strategy_name='breakout',
        start_date=config.start_date,
        end_date=config.end_date,
        initial_capital=stats.initial_capital,
        final_capital=stats.final_capital,
        total_return_pct=stats.total_return_pct,
        max_drawdown_pct=stats.max_drawdown_pct,
        sharpe_ratio=stats.sharpe_ratio,
        win_rate=stats.win_rate,
        total_trades=stats.total_trades,
        winning_trades=stats.winning_trades,
        losing_trades=stats.losing_trades,
        avg_win_pct=stats.avg_win_pct,
        avg_loss_pct=stats.avg_loss_pct,
        profit_factor=stats.profit_factor,
        avg_trade_duration_hours=stats.avg_trade_duration_hours,
        commission_paid=stats.total_commission,
        params_json=json.dumps({
            'lookback': params.lookback,
            'volume_multiplier': params.volume_multiplier,
            'atr_period': params.atr_period,
            'stop_loss_multiplier': params.stop_loss_multiplier,
            'trailing_stop_pct': params.trailing_stop_pct,
            'min_volume_usdt': params.min_volume_usdt,
        }),
    )
    return db.save_backtest_result(result)


def main():
    parser = argparse.ArgumentParser(description='Breakout Spot Strategy Backtester')
    parser.add_argument('--symbol', default=None, help='Trading pair (e.g. BTCUSDT)')
    parser.add_argument('--interval', default=None, help='Candle interval (e.g. 4h, 1h)')
    parser.add_argument('--start', default=None, help='Start date YYYY-MM')
    parser.add_argument('--end', default=None, help='End date YYYY-MM')
    parser.add_argument('--lookback', type=int, default=None, help='Lookback period')
    parser.add_argument('--volume-mult', type=float, default=None, help='Volume multiplier')
    parser.add_argument('--atr-period', type=int, default=None, help='ATR period')
    parser.add_argument('--sl-mult', type=float, default=None, help='Stop loss ATR multiplier')
    parser.add_argument('--trailing-stop', type=float, default=None, help='Trailing stop %%')
    parser.add_argument('--capital', type=float, default=None, help='Initial capital')
    parser.add_argument('--monthly', action='store_true', help='Run monthly breakdown')
    parser.add_argument('--optimize', action='store_true', help='Run hyperparameter optimization')
    parser.add_argument('--max-evals', type=int, default=None, help='Max optimization evaluations')
    parser.add_argument('--export', default=None, help='Export trades to CSV path')
    parser.add_argument('--config', default='config.ini', help='Config file path')

    args = parser.parse_args()
    config = Config(args.config)

    if args.symbol:
        config.parser.set('exchange', 'symbol', args.symbol)
    if args.interval:
        config.parser.set('exchange', 'interval', args.interval)
    if args.start:
        config.parser.set('exchange', 'start_date', args.start)
    if args.end:
        config.parser.set('exchange', 'end_date', args.end)
    if args.capital:
        config.parser.set('backtest', 'initial_capital', str(args.capital))

    params = StrategyParams(
        lookback=args.lookback or config.getint('strategy', 'lookback', 20),
        volume_multiplier=args.volume_mult or config.getfloat('strategy', 'volume_multiplier', 2.0),
        atr_period=args.atr_period or config.getint('strategy', 'atr_period', 14),
        stop_loss_multiplier=args.sl_mult or config.getfloat('strategy', 'stop_loss_multiplier', 1.5),
        trailing_stop_pct=args.trailing_stop or config.getfloat('strategy', 'trailing_stop_pct', 3.0),
        min_volume_usdt=config.getfloat('strategy', 'min_volume_usdt', 1_000_000),
    )

    print(f"\nBreakout Spot Strategy Backtester")
    print(f"{'─'*50}")
    print(f"Symbol:     {config.symbol}")
    print(f"Interval:   {config.interval}")
    print(f"Period:     {config.start_date} — {config.end_date}")
    print(f"Params:     Lookback={params.lookback}, VolMult={params.volume_multiplier}x, "
          f"ATR={params.atr_period}, SL={params.stop_loss_multiplier}x, TS={params.trailing_stop_pct}%")
    print(f"Capital:    ${config.initial_capital:,.0f}")
    print(f"Commission: {config.commission*100:.2f}%")
    print(f"Slippage:   {config.slippage*100:.3f}%")
    print()

    if args.monthly:
        monthly = run_monthly_analysis(config, params)
        print(f"\n{'='*60}")
        print(f"  MONTHLY BREAKDOWN — {config.symbol}")
        print(f"{'='*60}")
        print(f"  {'Month':<12} {'Return':>10} {'Trades':>8} {'WR%':>8} {'MaxDD%':>8} {'Sharpe':>8}")
        print(f"  {'─'*54}")
        for month, data in sorted(monthly.items()):
            print(f"  {month:<12} {data['return_pct']:>+9.2f}% {data['trades']:>8} "
                  f"{data['win_rate']:>7.1f}% {data['max_drawdown']:>7.2f}% {data['sharpe']:>8.3f}")
        print(f"{'='*60}")
        return

    if args.optimize:
        max_evals = args.max_evals or config.max_evals
        best_params_dict, results = run_optimization(config, max_evals=max_evals)
        if best_params_dict:
            print(f"\nRun backtest with best params:")
            print(f"  python main.py --lookback {best_params_dict.get('lookback', 20)} "
                  f"--volume-mult {best_params_dict.get('volume_multiplier', 2.0)} "
                  f"--sl-mult {best_params_dict.get('stop_loss_multiplier', 1.5)}")
        return

    stats, trades = run_backtest(config, params)

    db = Database(config.get('database', 'path', 'breakout_results.db'))
    db_id = save_result_to_db(db, config, stats, params)
    print(f"\nResult saved to database (ID: {db_id})")

    if args.export:
        export_trades_csv(trades, args.export)
    else:
        export_trades_csv(trades, f"trades_{config.symbol}_breakout.csv")

    db.close()


if __name__ == '__main__':
    main()
