"""
Event-driven backtest for the cointegration pairs strategy.

What the original prototype (worker_process_cointegration.py) did: scan
rolling windows and log a row every time a pair crossed the entry
threshold. That is a signal scanner, not a backtest - there is no concept
of an open position, no exit, no PnL, no costs, no equity curve anywhere
in the original code.

What this module does instead, walking forward bar by bar with no lookahead:

  1. Every `rescan_step` bars, re-run the cointegration/half-life/beta
     screen over the trailing `window` bars for the whole universe, using
     only data up to and including the rescan bar. Pairs that pass become
     "eligible" until the next rescan, with a hedge ratio fixed at the
     value estimated at rescan time.
  2. On every bar, for each eligible pair compute a z-score from the
     spread over the trailing `window` bars, using the hedge fixed at the
     last rescan (this is the "trading" side, distinct from the
     "formation" side that only runs at rescan time).
  3. Open a position if flat and |z| >= z_entry (capacity permitting).
     Close a position if in it and |z| <= z_exit, or the position has
     been open longer than `max_holding_bars`, or its mark-to-market loss
     exceeds `max_loss_per_pair_pct` of capital, or the pair failed the
     screen at the most recent rescan. The holding-period cap, the
     per-pair stop-loss and the forced close on screen failure are not in
     the original design doc - they're added here because a strategy with
     entries and no risk control on the exit side is not something you
     can honestly call a backtest. This is flagged in the README.
  4. Every fill pays taker fee + slippage (both configurable, in bps).
     Funding is not modeled - see README for why.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from coint_pairs.stats import calculate_cointegration, calculate_pair_beta, calculate_zscore_series
from coint_pairs.sizing import vol_parity_notional, calculate_qty


@dataclass
class BacktestConfig:
    window: int = 200                 # bars used for formation + z-score
    rescan_step: int = 30              # bars between universe rescans
    max_half_life: float = 200.0
    beta_threshold: float = 0.1        # |beta vs BTC| must be below this
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_stop: float = 4.0                # hard stop if spread keeps diverging (addition)
    max_holding_bars: int = 60         # ~10 days on 4h bars (addition)
    capital: float = 100_000.0
    max_notional_per_pair: float = 0.05
    max_loss_per_pair_pct: float = 0.01   # our operationalization of "max risk per pair" (addition)
    max_concurrent_pairs: int = 15         # portfolio-level cap (addition)
    max_gross_exposure_pct: float = 0.75   # portfolio-level cap (addition)
    vol_lookback: int = 60
    fee_bps: float = 5.0                # taker fee, one side, one leg
    slippage_bps: float = 5.0           # assumed slippage, one side, one leg
    fast_coint: bool = True
    min_pair_gap_bars: int = 0          # cooldown before re-entering same pair (0 = off)


@dataclass
class Trade:
    pair: str
    leg1: str
    leg2: str
    direction: int          # +1 = long spread (long leg1/short leg2), -1 = short spread
    entry_bar: int
    entry_date: pd.Timestamp
    exit_bar: int = -1
    exit_date: pd.Timestamp = None
    hedge: float = np.nan
    qty1: float = 0.0
    qty2: float = 0.0
    entry_price1: float = np.nan
    entry_price2: float = np.nan
    exit_price1: float = np.nan
    exit_price2: float = np.nan
    entry_z: float = np.nan
    exit_z: float = np.nan
    exit_reason: str = ""
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0


@dataclass
class _OpenPosition:
    pair: str
    leg1: str
    leg2: str
    direction: int
    hedge: float
    qty1: float
    qty2: float
    entry_price1: float
    entry_price2: float
    entry_bar: int
    entry_date: pd.Timestamp
    entry_z: float
    entry_cost: float
    last_price1: float
    last_price2: float


def _rescan_universe(
    log_prices: pd.DataFrame,
    log_btc: np.ndarray,
    symbols: list[str],
    end_idx: int,
    cfg: BacktestConfig,
) -> dict[tuple[str, str], dict]:
    """Full pairwise cointegration screen over the trailing window ending
    at end_idx (inclusive). Returns eligible pairs -> {hedge, half_life, p_value, beta}.
    """
    start_idx = end_idx - cfg.window
    window_log = log_prices.iloc[start_idx:end_idx + 1]
    window_btc_log = log_btc[start_idx:end_idx + 1]
    btc_ret = np.diff(window_btc_log)

    eligible = {}
    for a, b in itertools.combinations(symbols, 2):
        log1 = window_log[a].values
        log2 = window_log[b].values
        # calculate_cointegration expects raw prices (it logs internally),
        # so undo the log we already applied for this screen.
        price1 = np.exp(log1)
        price2 = np.exp(log2)
        flag, hedge, hl, pval = calculate_cointegration(
            price1, price2, max_half_life=cfg.max_half_life, fast=cfg.fast_coint
        )
        if flag != 1:
            continue
        pair_ret = np.diff(log1) - hedge * np.diff(log2)
        beta = calculate_pair_beta(pair_ret, btc_ret)
        if np.isnan(beta) or abs(beta) >= cfg.beta_threshold:
            continue
        eligible[(a, b)] = {"hedge": hedge, "half_life": hl, "p_value": pval, "beta": beta}
    return eligible


def run_backtest(
    price_matrix: pd.DataFrame,
    symbols: list[str],
    cfg: BacktestConfig,
    progress_every: int = 10,
    log_fn=print,
    precomputed_rescans: dict | None = None,
) -> dict:
    """Run the walk-forward backtest. `price_matrix` must be a wide frame
    of raw close prices (Date index, one column per symbol), and BTCUSDT
    must be one of the columns. `symbols` is the tradable universe
    (BTCUSDT can be included or excluded from it - it's always used as the
    beta benchmark regardless).
    """
    assert "BTCUSDT" in price_matrix.columns, "BTCUSDT must be in the price matrix"
    tradable = [s for s in symbols if s != "BTCUSDT"]

    log_prices = np.log(price_matrix)
    log_btc = log_prices["BTCUSDT"].values
    dates = price_matrix.index

    n_bars = len(price_matrix)
    fee_frac = cfg.fee_bps / 10_000.0
    slip_frac = cfg.slippage_bps / 10_000.0
    cost_frac = fee_frac + slip_frac

    equity = cfg.capital
    equity_curve = np.zeros(n_bars)
    open_positions: dict[tuple[str, str], _OpenPosition] = {}
    eligible_pairs: dict[tuple[str, str], dict] = {}
    closed_trades: list[Trade] = []
    last_exit_bar: dict[tuple[str, str], int] = {}

    t_start = time.time()
    n_rescans = 0

    for t in range(cfg.window, n_bars):
        # ---- rescan (formation) ----
        if (t - cfg.window) % cfg.rescan_step == 0:
            if precomputed_rescans is not None:
                if t not in precomputed_rescans:
                    raise KeyError(
                        f"No precomputed rescan for bar {t}. Run "
                        f"scripts/precompute_rescans.py to completion first."
                    )
                eligible_pairs = precomputed_rescans[t]
            else:
                eligible_pairs = _rescan_universe(log_prices, log_btc, tradable, t, cfg)
            n_rescans += 1
            if progress_every and n_rescans % progress_every == 0:
                elapsed = time.time() - t_start
                log_fn(f"[rescan {n_rescans}] bar {t}/{n_bars} date={dates[t]} "
                       f"eligible={len(eligible_pairs)} open={len(open_positions)} "
                       f"equity={equity:,.0f} elapsed={elapsed:.0f}s")

        realized_this_bar = 0.0

        # ---- manage open positions: mark-to-market + exit checks ----
        for key in list(open_positions.keys()):
            pos = open_positions[key]
            a, b = key
            price1 = price_matrix[a].iloc[t]
            price2 = price_matrix[b].iloc[t]

            # mark-to-market PnL since last bar
            realized_this_bar += pos.qty1 * (price1 - pos.last_price1)
            realized_this_bar += pos.qty2 * (price2 - pos.last_price2)
            pos.last_price1, pos.last_price2 = price1, price2

            unrealized_total = pos.qty1 * (price1 - pos.entry_price1) + pos.qty2 * (price2 - pos.entry_price2)

            # current z-score using the hedge fixed at last rescan for this pair
            start_idx = t - cfg.window
            spread_window = log_prices[a].values[start_idx:t + 1] - pos.hedge * log_prices[b].values[start_idx:t + 1]
            m, sd = spread_window.mean(), spread_window.std()
            z = (spread_window[-1] - m) / sd if sd > 0 else np.nan

            held = t - pos.entry_bar
            exit_reason = ""
            # priority: price-driven outcomes (target/stop) take precedence over the
            # relationship-based screen_failed check, since it's possible for both to
            # be true on the same bar (e.g. the spread reverted right as the pair also
            # stopped passing the cointegration screen at that rescan) - what actually
            # happened to the trade's price is the more informative label in that case.
            if not np.isnan(z) and abs(z) <= cfg.z_exit:
                exit_reason = "target"
            elif not np.isnan(z) and abs(z) >= cfg.z_stop:
                exit_reason = "stop_divergence"
            elif unrealized_total <= -cfg.max_loss_per_pair_pct * cfg.capital:
                exit_reason = "stop_loss"
            elif held >= cfg.max_holding_bars:
                exit_reason = "max_holding"
            elif key not in eligible_pairs:
                exit_reason = "screen_failed"

            if exit_reason:
                exit_notional = abs(pos.qty1) * price1 + abs(pos.qty2) * price2
                exit_cost = exit_notional * cost_frac
                realized_this_bar -= exit_cost

                trade = Trade(
                    pair=f"{a}-{b}", leg1=a, leg2=b, direction=pos.direction,
                    entry_bar=pos.entry_bar, entry_date=pos.entry_date,
                    exit_bar=t, exit_date=dates[t], hedge=pos.hedge,
                    qty1=pos.qty1, qty2=pos.qty2,
                    entry_price1=pos.entry_price1, entry_price2=pos.entry_price2,
                    exit_price1=price1, exit_price2=price2,
                    entry_z=pos.entry_z, exit_z=z, exit_reason=exit_reason,
                    gross_pnl=unrealized_total, costs=pos.entry_cost + exit_cost,
                    net_pnl=unrealized_total - pos.entry_cost - exit_cost,
                )
                closed_trades.append(trade)
                last_exit_bar[key] = t
                del open_positions[key]

        equity += realized_this_bar

        # ---- entries ----
        if len(open_positions) < cfg.max_concurrent_pairs:
            gross_exposure = sum(
                abs(p.qty1) * p.last_price1 + abs(p.qty2) * p.last_price2
                for p in open_positions.values()
            )
            for key, info in eligible_pairs.items():
                if len(open_positions) >= cfg.max_concurrent_pairs:
                    break
                if key in open_positions:
                    continue
                if cfg.min_pair_gap_bars and t - last_exit_bar.get(key, -10**9) < cfg.min_pair_gap_bars:
                    continue
                a, b = key
                hedge = info["hedge"]
                start_idx = t - cfg.window
                spread_window = (log_prices[a].values[start_idx:t + 1]
                                 - hedge * log_prices[b].values[start_idx:t + 1])
                m, sd = spread_window.mean(), spread_window.std()
                if sd <= 0:
                    continue
                z = (spread_window[-1] - m) / sd
                if abs(z) < cfg.z_entry:
                    continue
                direction = -1 if z >= cfg.z_entry else 1

                if gross_exposure >= cfg.max_gross_exposure_pct * cfg.capital:
                    continue

                dollar1, dollar2 = vol_parity_notional(
                    log_prices[a].values[start_idx:t + 1],
                    log_prices[b].values[start_idx:t + 1],
                    hedge, capital=cfg.capital,
                    max_notional_per_pair=cfg.max_notional_per_pair,
                    lookback=cfg.vol_lookback,
                )
                price1 = price_matrix[a].iloc[t]
                price2 = price_matrix[b].iloc[t]
                qty1, qty2 = calculate_qty(
                    dollar1, dollar2, price1, price2,
                    capital=cfg.capital, max_notional_per_pair=cfg.max_notional_per_pair,
                )
                if qty1 == 0.0 and qty2 == 0.0:
                    continue
                qty1 *= direction
                qty2 *= -direction

                entry_notional = abs(qty1) * price1 + abs(qty2) * price2
                entry_cost = entry_notional * cost_frac
                equity -= entry_cost

                open_positions[key] = _OpenPosition(
                    pair=f"{a}-{b}", leg1=a, leg2=b, direction=direction, hedge=hedge,
                    qty1=qty1, qty2=qty2, entry_price1=price1, entry_price2=price2,
                    entry_bar=t, entry_date=dates[t], entry_z=z, entry_cost=entry_cost,
                    last_price1=price1, last_price2=price2,
                )
                gross_exposure += entry_notional

        equity_curve[t] = equity

    equity_curve[:cfg.window] = cfg.capital

    # close anything still open at the last bar, marked to the final price
    t_last = n_bars - 1
    for key, pos in list(open_positions.items()):
        a, b = key
        price1 = price_matrix[a].iloc[t_last]
        price2 = price_matrix[b].iloc[t_last]
        unrealized_total = pos.qty1 * (price1 - pos.entry_price1) + pos.qty2 * (price2 - pos.entry_price2)
        exit_notional = abs(pos.qty1) * price1 + abs(pos.qty2) * price2
        exit_cost = exit_notional * cost_frac
        closed_trades.append(Trade(
            pair=f"{a}-{b}", leg1=a, leg2=b, direction=pos.direction,
            entry_bar=pos.entry_bar, entry_date=pos.entry_date,
            exit_bar=t_last, exit_date=dates[t_last], hedge=pos.hedge,
            qty1=pos.qty1, qty2=pos.qty2,
            entry_price1=pos.entry_price1, entry_price2=pos.entry_price2,
            exit_price1=price1, exit_price2=price2,
            entry_z=pos.entry_z, exit_z=np.nan, exit_reason="end_of_backtest",
            gross_pnl=unrealized_total, costs=pos.entry_cost + exit_cost,
            net_pnl=unrealized_total - pos.entry_cost - exit_cost,
        ))

    return {
        "equity_curve": pd.Series(equity_curve, index=dates),
        "trades": closed_trades,
        "n_rescans": n_rescans,
        "elapsed_sec": time.time() - t_start,
    }
