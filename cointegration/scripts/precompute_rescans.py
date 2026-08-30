#!/usr/bin/env python3
"""
Run the expensive part of the backtest (the periodic full-universe
cointegration rescans) with a checkpoint file, so it can be stopped and
resumed across multiple invocations instead of needing one uninterrupted
run. The event-driven trade simulation itself is cheap and does not need
this - only the O(n_pairs * n_rescans) screening does.

Usage:
    python scripts/precompute_rescans.py --csv <path> --universe 60 \
        --checkpoint results/rescans.pkl --max-seconds 240
Run it repeatedly (it resumes automatically) until it prints "ALL DONE".
"""

from __future__ import annotations

import argparse
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from coint_pairs.data import load_klines_long, select_universe, build_price_matrix
from backtest.engine import BacktestConfig, _rescan_universe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--universe", type=int, default=60)
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--rescan-step", type=int, default=30)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--max-seconds", type=float, default=240.0)
    args = ap.parse_args()

    df = load_klines_long(args.csv)
    universe = select_universe(df, top_n=args.universe)
    prices = build_price_matrix(df, universe)
    tradable = [s for s in universe if s != "BTCUSDT"]
    log_prices = np.log(prices)
    log_btc = log_prices["BTCUSDT"].values
    n_bars = len(prices)

    cfg = BacktestConfig(window=args.window, rescan_step=args.rescan_step)

    rescan_points = list(range(cfg.window, n_bars, cfg.rescan_step))

    state = {"universe": universe, "rescans": {}}
    if os.path.exists(args.checkpoint):
        with open(args.checkpoint, "rb") as f:
            state = pickle.load(f)
        print(f"Resuming: {len(state['rescans'])}/{len(rescan_points)} rescans already done")

    t_start = time.time()
    done_this_run = 0
    for t in rescan_points:
        if t in state["rescans"]:
            continue
        if time.time() - t_start > args.max_seconds:
            print(f"Time budget reached, stopping at {done_this_run} new rescans this run")
            break
        eligible = _rescan_universe(log_prices, log_btc, tradable, t, cfg)
        state["rescans"][t] = eligible
        done_this_run += 1
        if done_this_run % 5 == 0:
            with open(args.checkpoint, "wb") as f:
                pickle.dump(state, f)
            elapsed = time.time() - t_start
            print(f"  {len(state['rescans'])}/{len(rescan_points)} rescans done "
                  f"(+{done_this_run} this run, {elapsed:.0f}s elapsed)")

    with open(args.checkpoint, "wb") as f:
        pickle.dump(state, f)

    if len(state["rescans"]) >= len(rescan_points):
        print(f"ALL DONE: {len(state['rescans'])}/{len(rescan_points)} rescans complete")
    else:
        print(f"PARTIAL: {len(state['rescans'])}/{len(rescan_points)} rescans complete, run again to continue")


if __name__ == "__main__":
    main()
