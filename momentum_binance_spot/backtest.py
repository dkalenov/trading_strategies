"""
backtest.py - historical simulation of the momentum strategy.

The original bot shipped with no backtest at all - its README just
says "No backtest included." This script runs the same rule from
strategy.py (pick_top_gainer + momentum_confirmed + check_exit)
against historical candles and reports what actually would have
happened.

Read "How the backtest differs from live" in README.md before
trusting these numbers as a prediction of live performance. The short
version: the original strategy watches 1-minute candles with a
2-hour lookback; the only historical data available here is 4-hour
candles, so this is a translation of the same idea onto a timeframe
the data can actually support, not a frame-by-frame replay of the
original bot. That's disclosed here, not hidden.

Usage:
    python backtest.py --csv path/to/klines.csv
    python backtest.py --csv path/to/klines.csv --no-momentum-filter
    python backtest.py --csv path/to/klines.csv --fee 0 --slip 0
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import is_eligible_symbol, leveraged_token_symbols, momentum_confirmed

OUTPUT_DIR = Path("backtest_results")


def estimate_upper_first_probability(
    open_: float, high: float, low: float, close: float,
    upper: float, lower: float,
    rng: np.random.Generator,
    n_paths: int = 3000,
    n_steps: int = 200,
) -> float:
    """
    For a bar where both the take-profit and the stop-loss fell inside
    its high/low range, models the probability that price reached the
    upper barrier before the lower one - instead of just guessing.

    Method: treat the within-bar log-price path as a Brownian bridge
    from open to close (the two points we actually know), with
    volatility calibrated from the bar's own high/low range via the
    Parkinson estimator (sigma^2 = ln(H/L)^2 / (4 ln 2), a standard
    single-bar volatility estimate). Simulate many such bridges and
    count how often the upper barrier is crossed before the lower one,
    among the simulated paths that cross at least one of them.

    This is a model, not a measurement - it doesn't know anything about
    the bar that its own open, high, low, and close don't already
    imply. It's here because "assume the worst" and "assume the best"
    are also both models, just cruder ones (they implicitly assume
    p=0 or p=1 for every single ambiguous bar). This one at least
    responds sensibly to how the bar actually traded: a bar that
    closes near its high biases toward the upper barrier being hit
    first, a bar that closes near its low biases the other way. See
    README for how much this changes on real data, and its own
    limitations.
    """
    if high <= low or n_paths <= 0 or n_steps <= 0:
        return 0.5

    x0, x1 = np.log(open_), np.log(close)
    u, d = np.log(upper), np.log(lower)
    sigma = np.sqrt((np.log(high / low)) ** 2 / (4 * np.log(2)))
    if sigma <= 0:
        return 0.5

    dt = 1.0 / n_steps
    increments = rng.normal(0.0, sigma * np.sqrt(dt), size=(n_paths, n_steps))
    raw = np.cumsum(increments, axis=1)
    raw = np.concatenate([np.zeros((n_paths, 1)), raw], axis=1)
    t = np.linspace(0.0, 1.0, n_steps + 1)
    bridge = x0 + (raw - t * raw[:, -1:]) + t * (x1 - x0)

    hit_upper = bridge >= u
    hit_lower = bridge <= d
    any_upper = hit_upper.any(axis=1)
    any_lower = hit_lower.any(axis=1)
    first_upper = np.where(any_upper, hit_upper.argmax(axis=1), n_steps + 1)
    first_lower = np.where(any_lower, hit_lower.argmax(axis=1), n_steps + 1)

    hit_either = any_upper | any_lower
    n_valid = hit_either.sum()
    if n_valid == 0:
        return 0.5
    upper_first = (first_upper < first_lower) & hit_either
    return float(upper_first.sum() / n_valid)


@dataclass
class BacktestConfig:
    take_profit_pct: float = 0.02
    stop_loss_pct: float = 0.015
    fee_pct: float = 0.001          # 0.1% taker fee per side (Binance spot VIP0 default)
    slippage_pct: float = 0.0005    # 0.05% assumed slippage per side on a market order
    max_hold_bars: int = 60         # safety cap (~10 days of 4h bars) - see README
    starting_equity: float = 1000.0
    fixed_stake: float = 15.0       # matches the original script's hardcoded 15 USDT
    require_momentum_filter: bool = True
    quote_asset: str = "USDT"
    tie_break: str = "conservative"  # "conservative" (stop wins ties), "optimistic"
                                       # (target wins ties), or "probabilistic"
                                       # (modeled - see estimate_upper_first_probability)
    seed: int = 42                    # RNG seed for the probabilistic tie-break, for
                                       # reproducible results run to run


@dataclass
class TradeLog:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    return_pct: float
    reason: str
    bars_held: int
    ambiguous_exit: bool = False   # True if the exit bar's range touched
                                    # BOTH the target and the stop - see
                                    # README "How the backtest differs from
                                    # live". On 4h bars this happens often;
                                    # which one actually triggered first
                                    # cannot be known from OHLC alone.
    modeled_prob_take_profit_first: float | None = None  # set only when
                                    # tie_break="probabilistic" and this
                                    # trade's exit bar was ambiguous


def load_klines(csv_path: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reads the multi-symbol 4h klines CSV and pivots it into four
    wide frames (Date x Symbol): open, high, low, close."""
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    df = df.sort_values(["Symbol", "Date"])

    excluded = leveraged_token_symbols(quote_asset="USDT")
    eligible = sorted(s for s in df["Symbol"].unique() if is_eligible_symbol(s, "USDT", excluded))

    close = df.pivot(index="Date", columns="Symbol", values="Close")[eligible]
    openp = df.pivot(index="Date", columns="Symbol", values="Open")[eligible]
    high = df.pivot(index="Date", columns="Symbol", values="High")[eligible]
    low = df.pivot(index="Date", columns="Symbol", values="Low")[eligible]
    return openp, high, low, close


def run_backtest(csv_path: str, cfg: BacktestConfig) -> list[TradeLog]:
    openp, high, low, close = load_klines(csv_path)

    # ~24h trailing return, used as the "top gainer" ranking - the live
    # bot uses Binance's rolling 24h ticker; the closest equivalent
    # available in 4h historical candles is a 6-bar (6 x 4h = 24h) return.
    chg24 = close / close.shift(6) - 1
    bullish_last_bar = close > openp  # proxy for "short-term momentum still positive"

    dates = close.index
    cols = list(close.columns)
    col_idx = {c: i for i, c in enumerate(cols)}
    close_np, open_np, high_np, low_np = close.values, openp.values, high.values, low.values
    chg_np, bull_np = chg24.values, bullish_last_bar.values
    n = len(dates)

    entry_cost = cfg.fee_pct + cfg.slippage_pct
    exit_cost = cfg.fee_pct + cfg.slippage_pct
    rng = np.random.default_rng(cfg.seed)

    trades: list[TradeLog] = []
    in_position = False
    pos_symbol = None
    pos_entry_price = None
    pos_entry_idx = None

    i = 0
    while i < n:
        if not in_position:
            row_chg = chg_np[i]
            valid = ~np.isnan(row_chg)
            if cfg.require_momentum_filter:
                valid = valid & bull_np[i]
            if valid.any():
                cand_idx = np.where(valid)[0]
                best = cand_idx[np.argmax(row_chg[cand_idx])]
                sig_price = close_np[i, best]
                if not np.isnan(sig_price):
                    in_position = True
                    pos_symbol = cols[best]
                    # Entry filled at the signal bar's close plus cost -
                    # see README "How the backtest differs from live" for
                    # why this (rather than next bar's open) was chosen.
                    pos_entry_price = sig_price * (1 + entry_cost)
                    pos_entry_idx = i
            i += 1
            continue

        j = col_idx[pos_symbol]
        tp_price = pos_entry_price * (1 + cfg.take_profit_pct)
        sl_price = pos_entry_price * (1 - cfg.stop_loss_pct)
        exit_price, reason, bars_held = None, None, 0
        ambiguous_exit = False
        modeled_prob = None
        blended_return = None
        k = pos_entry_idx + 1

        while k < n:
            bars_held += 1
            lo, hi, cl = low_np[k, j], high_np[k, j], close_np[k, j]
            op = open_np[k, j]
            if np.isnan(lo) or np.isnan(hi):
                prev_close = close_np[k - 1, j]
                exit_price = prev_close if not np.isnan(prev_close) else pos_entry_price
                reason = "data_gap"
                break
            hit_sl = lo <= sl_price
            hit_tp = hi >= tp_price
            if hit_sl and hit_tp:
                # A single 4h bar spans both the target and the stop.
                # OHLC data can't tell us which was actually touched
                # first - see README "How the backtest differs from
                # live" for how often this happens and why it matters.
                ambiguous_exit = True
                if cfg.tie_break == "optimistic":
                    exit_price, reason = tp_price, "take_profit"
                elif cfg.tie_break == "probabilistic":
                    modeled_prob = estimate_upper_first_probability(
                        op, hi, lo, cl, tp_price, sl_price, rng,
                    )
                    tp_return = tp_price * (1 - exit_cost) / pos_entry_price - 1
                    sl_return = sl_price * (1 - exit_cost) / pos_entry_price - 1
                    blended_return = modeled_prob * tp_return + (1 - modeled_prob) * sl_return
                    reason = "probabilistic_blend"
                else:
                    exit_price, reason = sl_price, "stop_loss"
                break
            if hit_sl:
                exit_price, reason = sl_price, "stop_loss"
                break
            if hit_tp:
                exit_price, reason = tp_price, "take_profit"
                break
            if bars_held >= cfg.max_hold_bars:
                exit_price, reason = cl, "timeout"
                break
            k += 1
        else:
            last_valid = k - 1
            prev_close = close_np[last_valid, j] if last_valid >= 0 else np.nan
            exit_price = prev_close if not np.isnan(prev_close) else pos_entry_price
            reason = "end_of_data"
            k = n

        if blended_return is not None:
            trade_return = blended_return
            exit_fill = pos_entry_price * (1 + trade_return)  # for display only
        else:
            exit_fill = exit_price * (1 - exit_cost)
            trade_return = exit_fill / pos_entry_price - 1

        trades.append(TradeLog(
            symbol=pos_symbol,
            entry_time=dates[pos_entry_idx],
            exit_time=dates[min(k, n - 1)],
            entry_price=pos_entry_price,
            exit_price=exit_fill,
            return_pct=trade_return * 100,
            reason=reason,
            bars_held=bars_held,
            ambiguous_exit=ambiguous_exit,
            modeled_prob_take_profit_first=modeled_prob,
        ))

        in_position = False
        pos_symbol = None
        i = k + 1

    return trades


def summarize(trades: list[TradeLog], cfg: BacktestConfig) -> tuple[dict, pd.DataFrame]:
    if not trades:
        return {"n_trades": 0}, pd.DataFrame()

    tdf = pd.DataFrame([t.__dict__ for t in trades])
    wins = tdf[tdf.return_pct > 0]
    losses = tdf[tdf.return_pct <= 0]
    gross_win = wins.return_pct.sum()
    gross_loss = -losses.return_pct.sum()

    # Full-equity compounding curve: everything redeployed into a single
    # position at a time. This is the standard "how did the strategy do"
    # equity curve, and it is NOT how the original script sizes trades -
    # see below for that number too.
    equity = cfg.starting_equity
    compounding_curve = []
    for _, row in tdf.iterrows():
        equity *= (1 + row.return_pct / 100)
        compounding_curve.append(equity)
    tdf["compounding_equity"] = compounding_curve
    peak = tdf["compounding_equity"].cummax()
    dd = (tdf["compounding_equity"] - peak) / peak
    max_dd_compounding = dd.min()

    # Fixed-stake $ P&L: what the original script's hardcoded 15 USDT
    # per trade would literally have made or lost, independent of
    # account size, no compounding at all (which is also literally how
    # the original code behaves - it never scales the stake).
    tdf["fixed_stake_pnl"] = cfg.fixed_stake * tdf.return_pct / 100
    cum_fixed = tdf["fixed_stake_pnl"].cumsum()
    peak_fixed = cum_fixed.cummax()
    max_dd_fixed = (cum_fixed - peak_fixed).min()

    ambiguous_pct = round(tdf.ambiguous_exit.mean() * 100, 1)
    n_probabilistic = int((tdf.reason == "probabilistic_blend").sum())

    win_rate_note = None
    if n_probabilistic > 0:
        win_rate_note = (
            f"{n_probabilistic} of these trades are probability-weighted blends "
            "(tie_break='probabilistic'), not clean wins or losses - each one's "
            "return_pct is an expected value, not a realized outcome. Treat "
            "win_rate_pct here as a rough guide, not a literal count of trades "
            "that 'won'."
        )

    result = {
        "n_trades": len(tdf),
        "date_range": [str(tdf.entry_time.min()), str(tdf.exit_time.max())],
        "win_rate_pct": round(len(wins) / len(tdf) * 100, 2),
        "avg_win_pct": round(wins.return_pct.mean(), 3) if len(wins) else 0.0,
        "avg_loss_pct": round(losses.return_pct.mean(), 3) if len(losses) else 0.0,
        "expectancy_pct_per_trade": round(tdf.return_pct.mean(), 3),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
        "avg_bars_held": round(tdf.bars_held.mean(), 2),
        "exit_reason_counts": tdf.reason.value_counts().to_dict(),
        "compounding_final_equity": round(equity, 2),
        "compounding_max_drawdown_pct": round(max_dd_compounding * 100, 2),
        "fixed_stake_total_pnl_usd": round(cum_fixed.iloc[-1], 2),
        "fixed_stake_max_drawdown_usd": round(max_dd_fixed, 2),
        "fixed_stake_amount_usd": cfg.fixed_stake,
        "DATA_RESOLUTION_WARNING": (
            f"{ambiguous_pct}% of trades hit a bar where both the take-profit "
            "and the stop-loss fell inside that single 4h candle's high/low "
            "range. 4h OHLC data cannot tell you which one actually happened "
            "first. This run resolved ties using tie_break="
            f"'{cfg.tie_break}'. Run backtest.py with --tie-break optimistic "
            "vs --tie-break conservative vs --tie-break probabilistic to see "
            "how much that single assumption moves the result - on this "
            "dataset it's the difference between the strategy looking clearly "
            "unprofitable and looking clearly profitable. See README.md."
        ),
        "assumptions": {
            "take_profit_pct": cfg.take_profit_pct,
            "stop_loss_pct": cfg.stop_loss_pct,
            "fee_pct_per_side": cfg.fee_pct,
            "slippage_pct_per_side": cfg.slippage_pct,
            "max_hold_bars": cfg.max_hold_bars,
            "momentum_filter_applied": cfg.require_momentum_filter,
            "tie_break": cfg.tie_break,
            "tie_break_seed": cfg.seed,
        },
    }
    if win_rate_note:
        result["PROBABILISTIC_MODE_NOTE"] = win_rate_note
    return result, tdf


def plot_equity(tdf: pd.DataFrame, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)

    axes[0].plot(pd.to_datetime(tdf.exit_time), tdf.fixed_stake_pnl.cumsum(), color="#2563eb")
    axes[0].axhline(0, color="grey", linewidth=0.8)
    axes[0].set_title("Cumulative P&L at a fixed 15 USDT stake per trade (matches the original script's sizing)")
    axes[0].set_ylabel("USD")

    axes[1].plot(pd.to_datetime(tdf.exit_time), tdf.compounding_equity, color="#dc2626")
    axes[1].axhline(1000, color="grey", linewidth=0.8, linestyle="--")
    axes[1].set_yscale("log")
    axes[1].set_title("Account equity if 100% compounded into one position at a time (log scale)")
    axes[1].set_ylabel("USD (log)")
    axes[1].set_xlabel("Date")

    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Backtest the momentum spot strategy")
    parser.add_argument("--csv", required=True, help="Path to the multi-symbol klines CSV")
    parser.add_argument("--tp", type=float, default=0.02, help="Take profit pct, e.g. 0.02 for 2%%")
    parser.add_argument("--sl", type=float, default=0.015, help="Stop loss pct, e.g. 0.015 for 1.5%%")
    parser.add_argument("--fee", type=float, default=0.001, help="Taker fee per side")
    parser.add_argument("--slip", type=float, default=0.0005, help="Slippage assumption per side")
    parser.add_argument("--max-hold-bars", type=int, default=60)
    parser.add_argument("--stake", type=float, default=15.0, help="Fixed stake in quote asset, USD")
    parser.add_argument("--starting-equity", type=float, default=1000.0)
    parser.add_argument("--no-momentum-filter", action="store_true",
                         help="Skip the 'last bar bullish' filter, buy the top gainer unconditionally")
    parser.add_argument("--tie-break", choices=["conservative", "optimistic", "probabilistic"],
                         default="conservative",
                         help="When one 4h bar touches both TP and SL: 'conservative' assumes the "
                              "stop hit first (default), 'optimistic' assumes the target did, "
                              "'probabilistic' models each ambiguous bar with a volatility-"
                              "calibrated Brownian bridge instead of guessing either extreme. "
                              "See README - this assumption matters a lot on 4h data.")
    parser.add_argument("--seed", type=int, default=42,
                         help="RNG seed for --tie-break probabilistic, for reproducible runs")
    parser.add_argument("--skip-bracket", action="store_true",
                         help="Skip auto-computing the other tie-break scenario(s) for comparison "
                              "(faster, but you lose the sensitivity check in the output)")
    parser.add_argument("--out", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    base_kwargs = dict(
        take_profit_pct=args.tp,
        stop_loss_pct=args.sl,
        fee_pct=args.fee,
        slippage_pct=args.slip,
        max_hold_bars=args.max_hold_bars,
        starting_equity=args.starting_equity,
        fixed_stake=args.stake,
        require_momentum_filter=not args.no_momentum_filter,
    )

    cfg = BacktestConfig(tie_break=args.tie_break, seed=args.seed, **base_kwargs)
    trades = run_backtest(args.csv, cfg)
    summary, tdf = summarize(trades, cfg)

    if not args.skip_bracket:
        # Always compute the deterministic bounds too, so the sensitivity
        # is visible in every run's output, not just the one you happened
        # to pick with --tie-break. If the primary run IS one of the two
        # deterministic bounds, only the other one needs computing; if the
        # primary run is 'probabilistic', both bounds are useful context
        # (they're cheap - the Monte Carlo model is the slow part, and
        # this doesn't repeat it).
        bracket = {}
        for label in ("conservative", "optimistic"):
            if label == args.tie_break:
                bracket[label] = {
                    "expectancy_pct_per_trade": summary.get("expectancy_pct_per_trade"),
                    "win_rate_pct": summary.get("win_rate_pct"),
                    "profit_factor": summary.get("profit_factor"),
                }
                continue
            other_cfg = BacktestConfig(tie_break=label, seed=args.seed, **base_kwargs)
            other_trades = run_backtest(args.csv, other_cfg)
            other_summary, _ = summarize(other_trades, other_cfg)
            bracket[label] = {
                "expectancy_pct_per_trade": other_summary.get("expectancy_pct_per_trade"),
                "win_rate_pct": other_summary.get("win_rate_pct"),
                "profit_factor": other_summary.get("profit_factor"),
            }
        summary["TIE_BREAK_BRACKET"] = {
            "this_run_tie_break": args.tie_break,
            **bracket,
            "note": (
                "'conservative' and 'optimistic' are the two extremes (stop-first "
                "vs target-first on every ambiguous bar). 'probabilistic' (if not "
                "shown above, run with --tie-break probabilistic) models each "
                "ambiguous bar individually instead of picking an extreme - see "
                "README."
            ),
        }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))

    if tdf.empty:
        print("\nNo trades were generated - nothing to plot.")
        return

    tdf.to_csv(out_dir / "trades.csv", index=False)
    plot_equity(tdf, out_dir / "equity_curve.png")
    print(f"\nWrote {out_dir/'trades.csv'}, {out_dir/'summary.json'}, {out_dir/'equity_curve.png'}")


if __name__ == "__main__":
    main()
