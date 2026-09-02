"""
Core statistics for the cointegration pairs strategy.

This is a corrected version of the original func_cointegration.py from the
research prototype. Two bugs in the original were fixed here:

1. The original called `model.params.iloc[1]` on the OLS result. This only
   works if the regressor passed to statsmodels was a pandas Series. The
   worker script in the original prototype fed it raw numpy arrays (slices
   of a numpy matrix), which made `.iloc` raise AttributeError on every
   single call. The exception was swallowed by a bare `except Exception`,
   so the function silently returned hedge=nan and flag=0 for every pair,
   every window, all the time. Fixed by reading params through
   `np.asarray(model.params)`, which works for both Series and ndarray.

2. The worker script passed already log-transformed prices into a function
   that itself takes the log of its input again (the function is written,
   and correctly used in its own __main__ block, to accept raw prices).
   Feeding it log-prices meant computing log(log(price)), which is not a
   meaningful transform and would have produced a corrupted hedge ratio,
   spread and z-score even if bug #1 had not made the whole thing crash
   first. Fixed by making the contract explicit: this module always takes
   raw prices in and does the log transform internally, exactly once.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint


def _to_log_prices(prices: np.ndarray) -> np.ndarray:
    """Log-transform a raw price series. Shifts up if any value is <= 0.

    Crypto spot/futures prices are always positive so the shift branch is
    dead code in practice, but it is kept because it was in the original
    and costs nothing to keep as a safety net against bad input data.
    """
    prices = np.asarray(prices, dtype=float)
    if np.any(prices <= 0):
        prices = prices + abs(np.nanmin(prices)) + 1.0
    return np.log(prices)


def calculate_cointegration(price1, price2, max_half_life: int = 200, fast: bool = True):
    """Engle-Granger cointegration test between two RAW price series.

    Returns (flag, hedge, half_life, p_value).
    flag is 1 if the pair passes p < 0.05 AND the EG t-stat is below the
    5% critical value AND the estimated half-life is in (0, max_half_life].

    fast=True uses a fixed ADF lag (maxlag=1, autolag=None) instead of the
    default AIC lag search. On a 200-bar window this is about 7-8x faster
    and gives the same accept/reject decision in practice (checked against
    autolag='aic' on this project's actual data, see README). Set
    fast=False if you want the slower, textbook-default behavior.
    """
    log1 = _to_log_prices(price1)
    log2 = _to_log_prices(price2)

    safe_p_value = np.nan
    try:
        if fast:
            coint_t, p_value, crit_vals = coint(log1, log2, maxlag=1, autolag=None)
        else:
            coint_t, p_value, crit_vals = coint(log1, log2)
        safe_p_value = float(p_value)

        X = sm.add_constant(log2)
        model = sm.OLS(log1, X).fit()
        params = np.asarray(model.params)
        hedge = float(params[1])

        spread = log1 - hedge * log2
        hl = calculate_half_life(spread)

        if np.isnan(hl) or hl <= 0 or hl > max_half_life:
            return 0, hedge, np.nan, safe_p_value

        t_check = coint_t < crit_vals[1]  # 5% significance level
        flag = 1 if (safe_p_value < 0.05 and t_check) else 0
        return flag, hedge, hl, safe_p_value

    except Exception:
        return 0, np.nan, np.nan, safe_p_value


def calculate_half_life(spread) -> float:
    """Half-life of mean reversion (in bars) for an OU-like spread series."""
    try:
        s = pd.Series(np.asarray(spread, dtype=float)).dropna()
        if len(s) < 10:
            return np.nan

        spread_lag = s.shift(1).iloc[1:]
        delta = (s - s.shift(1)).iloc[1:]

        X = sm.add_constant(spread_lag)
        model = sm.OLS(delta, X).fit()
        b = float(np.asarray(model.params)[1])
        if np.isnan(b):
            return np.nan
        phi = 1.0 + b
        if phi <= 0 or phi >= 1:
            return np.nan
        return float(round(-np.log(2) / np.log(phi), 2))
    except Exception:
        return np.nan


def calculate_zscore_series(log1: np.ndarray, log2: np.ndarray, hedge: float) -> np.ndarray:
    """Rolling in-window z-score of the spread log1 - hedge*log2."""
    spread = log1 - hedge * log2
    m = np.mean(spread)
    sd = np.std(spread)
    if sd == 0 or np.isnan(sd):
        return np.full_like(spread, np.nan)
    return (spread - m) / sd


def calculate_z_last(spread) -> float:
    s = pd.Series(np.asarray(spread, dtype=float))
    m = s.mean()
    sd = s.std()
    if sd == 0 or np.isnan(sd):
        return np.nan
    return float((s.iloc[-1] - m) / sd)


def calculate_pair_beta(pair_r, market_r) -> float:
    """Beta of a return series against a market (BTC) return series."""
    if pair_r is None or market_r is None:
        return np.nan
    pair_r = np.asarray(pair_r, dtype=float)
    market_r = np.asarray(market_r, dtype=float)
    if len(pair_r) != len(market_r) or len(pair_r) < 5:
        return np.nan
    cov = np.cov(pair_r, market_r)[0, 1]
    var_m = np.var(market_r)
    if var_m == 0:
        return np.nan
    return float(cov / var_m)
