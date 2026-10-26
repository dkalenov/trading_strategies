"""
Bollinger Bands Strategy — Batch backtest all Bybit USDT perpetual futures.

Parallel downloads + incremental save to JSON.
"""

import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import Config
from utils import download_klines_bybit
from strategy import StrategyParams
from backtester import Backtester


def get_bybit_symbols():
    """Fetch all Bybit linear USDT perpetual symbols."""
    try:
        resp = requests.get('https://api.bybit.com/v5/market/instruments-info',
                            params={'category': 'linear', 'status': 'Trading'},
                            timeout=30)
        data = resp.json()
        if data.get('retCode') != 0:
            print(f"Error: {data.get('retMsg', 'unknown')}")
            return []

        symbols = []
        for inst in data.get('result', {}).get('list', []):
            if inst.get('quoteCoin') == 'USDT' and inst.get('contractType') == 'LinearPerpetual':
                symbols.append(inst['symbol'])
        return sorted(symbols)
    except Exception as e:
        print(f"Error fetching symbols: {e}")
        return []


def backtest_symbol(symbol, config, params, variant='basic'):
    """Download + backtest one symbol. Returns result dict or None."""
    try:
        df = download_klines_bybit(symbol, config.interval, config.start_date,
                                    config.end_date, config.klines_dir, quiet=True)

        if df.empty or len(df) < 100:
            return {'symbol': symbol, 'error': 'insufficient data'}

        backtester = Backtester(
            initial_capital=config.initial_capital,
            risk_pct=config.risk_pct / 100,
            max_leverage=config.leverage,
            commission_rate=config.commission,
            slippage_rate=config.slippage,
            funding_rate=config.funding_rate,
            interval=config.interval,
        )

        stats, trades = backtester.run(df, variant, params)

        return {
            'symbol': symbol,
            'interval': config.interval,
            'variant': variant,
            'return_pct': round(stats.total_return_pct, 2),
            'sharpe': round(stats.sharpe_ratio, 3),
            'max_dd': round(stats.max_drawdown_pct, 2),
            'win_rate': round(stats.win_rate, 1),
            'trades': stats.total_trades,
            'profit_factor': round(stats.profit_factor, 2),
            'expectancy': round(stats.expectancy, 2),
            'long_trades': stats.long_trades,
            'short_trades': stats.short_trades,
            'total_commission': round(stats.total_commission, 2),
            'total_funding': round(stats.total_funding, 2),
        }
    except Exception as e:
        return {'symbol': symbol, 'error': str(e)}


def save_results(results, errors, config, params, variant):
    """Save results to JSON incrementally."""
    output = {
        'config': {
            'symbol': config.symbol,
            'interval': config.interval,
            'start_date': config.start_date,
            'end_date': config.end_date,
            'variant': variant,
            'params': {
                'bb_timeperiod': params.bb_timeperiod,
                'bb_nbdevup': params.bb_nbdevup,
                'bb_nbdevdn': params.bb_nbdevdn,
            },
        },
        'results': sorted(results, key=lambda x: x.get('sharpe', -999), reverse=True),
        'errors': errors,
    }
    with open('batch_results.json', 'w') as f:
        json.dump(output, f, indent=2)


def print_summary(results, errors):
    """Print batch results summary."""
    valid = [r for r in results if 'error' not in r]
    profitable = [r for r in valid if r['trades'] >= 10]

    print(f"\n{'='*80}")
    print(f"BATCH RESULTS — {len(valid)} symbols backtested, {len(errors)} errors/skipped")
    print(f"{'='*80}")

    if not profitable:
        print("No profitable symbols found with >= 10 trades")
        return

    profitable.sort(key=lambda x: x['sharpe'], reverse=True)
    print(f"\nTOP 30 BY SHARPE (min 10 trades):")
    print(f"{'Symbol':<20} {'Return':>10} {'Sharpe':>8} {'WR':>8} {'Trades':>8} {'MaxDD':>10} {'PF':>8}")
    print('-' * 80)
    for r in profitable[:30]:
        print(f"{r['symbol']:<20} {r['return_pct']:>+9.2f}% {r['sharpe']:>8.3f} "
              f"{r['win_rate']:>7.1f}% {r['trades']:>8} {r['max_dd']:>9.2f}% {r['profit_factor']:>8.2f}")

    profitable.sort(key=lambda x: x['return_pct'], reverse=True)
    print(f"\nTOP 30 BY RETURN (min 10 trades):")
    print(f"{'Symbol':<20} {'Return':>10} {'Sharpe':>8} {'WR':>8} {'Trades':>8} {'MaxDD':>10} {'PF':>8}")
    print('-' * 80)
    for r in profitable[:30]:
        print(f"{r['symbol']:<20} {r['return_pct']:>+9.2f}% {r['sharpe']:>8.3f} "
              f"{r['win_rate']:>7.1f}% {r['trades']:>8} {r['max_dd']:>9.2f}% {r['profit_factor']:>8.2f}")

    if valid:
        returns = [r['return_pct'] for r in valid]
        prof_count = sum(1 for r in returns if r > 0)
        print(f"\nSUMMARY:")
        print(f"  Profitable: {prof_count}/{len(valid)} ({prof_count/len(valid)*100:.0f}%)")
        print(f"  Avg Return: {sum(returns)/len(returns):+.2f}%")
        print(f"  Median Return: {sorted(returns)[len(returns)//2]:+.2f}%")


def main():
    config = Config()
    params = StrategyParams(**config.strategy_params)
    variant = 'basic'

    symbols = get_bybit_symbols()
    print(f"Found {len(symbols)} Bybit USDT perpetual symbols")

    if not symbols:
        print("No symbols found, exiting")
        return

    results = []
    errors = []
    done = 0

    # Parallel download + backtest (8 workers)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(backtest_symbol, sym, config, params, variant): sym
            for sym in symbols
        }

        for future in as_completed(futures):
            done += 1
            result = future.result()

            if 'error' in result:
                errors.append(result)
                if done % 50 == 0:
                    print(f"  [{done}/{len(symbols)}] {len(errors)} errors so far...")
            else:
                results.append(result)
                if done % 20 == 0 or result.get('sharpe', 0) > 1.0:
                    sym = result['symbol']
                    print(f"  [{done}/{len(symbols)}] {sym}: "
                          f"Return={result['return_pct']:+.2f}% Sharpe={result['sharpe']:.3f} "
                          f"WR={result['win_rate']:.0f}% Trades={result['trades']}")

            # Incremental save every 50 symbols
            if done % 50 == 0:
                save_results(results, errors, config, params, variant)
                print(f"  Saved progress: {len(results)} results, {len(errors)} errors")

    # Final save
    save_results(results, errors, config, params, variant)
    print_summary(results, errors)


if __name__ == '__main__':
    main()
