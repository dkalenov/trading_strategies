#!/usr/bin/env python3
"""
Run the cointegration pairs backtest end to end and write results to ./results/.

Usage:
    python scripts/run_backtest.py --csv path/to/klines.csv --universe 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from coint_pairs.data import load_klines_long, select_universe, build_price_matrix
from backtest.engine import BacktestConfig, run_backtest
from backtest.metrics import compute_metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="path to klines CSV (Date,Open,High,Low,Close,Volume,Symbol)")
    ap.add_argument("--universe", type=int, default=60, help="number of liquid symbols to trade")
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--rescan-step", type=int, default=30)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results"))
    ap.add_argument("--min-pair-gap-bars", type=int, default=6)
    ap.add_argument("--rescans-checkpoint", default=None,
                     help="path to a rescans.pkl produced by precompute_rescans.py; "
                          "if given, skips the expensive on-the-fly cointegration scan")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    print(f"Loading {args.csv} ...")
    df = load_klines_long(args.csv)
    universe = select_universe(df, top_n=args.universe)
    prices = build_price_matrix(df, universe)
    print(f"Universe: {len(universe)} symbols, price matrix shape {prices.shape}, "
          f"{prices.index.min()} -> {prices.index.max()}")

    cfg = BacktestConfig(
        window=args.window,
        rescan_step=args.rescan_step,
        capital=args.capital,
        min_pair_gap_bars=args.min_pair_gap_bars,
    )
    print(f"Config: {cfg}")

    precomputed = None
    if args.rescans_checkpoint:
        import pickle
        with open(args.rescans_checkpoint, "rb") as f:
            state = pickle.load(f)
        precomputed = state["rescans"]
        print(f"Loaded {len(precomputed)} precomputed rescans from {args.rescans_checkpoint}")

    result = run_backtest(prices, universe, cfg, progress_every=5, precomputed_rescans=precomputed)

    eq = result["equity_curve"]
    trades = result["trades"]
    metrics = compute_metrics(eq, trades, cfg.capital, prices["BTCUSDT"])

    eq.to_csv(os.path.join(args.out, "equity_curve.csv"), header=["equity"])

    trades_rows = []
    for tr in trades:
        trades_rows.append({
            "pair": tr.pair, "leg1": tr.leg1, "leg2": tr.leg2, "direction": tr.direction,
            "entry_date": tr.entry_date, "exit_date": tr.exit_date,
            "hedge": tr.hedge, "qty1": tr.qty1, "qty2": tr.qty2,
            "entry_price1": tr.entry_price1, "entry_price2": tr.entry_price2,
            "exit_price1": tr.exit_price1, "exit_price2": tr.exit_price2,
            "entry_z": tr.entry_z, "exit_z": tr.exit_z, "exit_reason": tr.exit_reason,
            "gross_pnl": tr.gross_pnl, "costs": tr.costs, "net_pnl": tr.net_pnl,
            "holding_bars": tr.exit_bar - tr.entry_bar,
        })
    pd.DataFrame(trades_rows).to_csv(os.path.join(args.out, "trades.csv"), index=False)

    with open(os.path.join(args.out, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    with open(os.path.join(args.out, "universe.json"), "w") as f:
        json.dump(universe, f, indent=2)

    print(json.dumps(metrics, indent=2, default=str))
    print(f"n_rescans={result['n_rescans']} elapsed={result['elapsed_sec']:.0f}s")
    print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()
