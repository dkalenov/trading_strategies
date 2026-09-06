"""
Pair screening and z-score signal generation for live/paper trading.
This is a thin wrapper: the actual math (cointegration test, half-life,
beta, sizing) lives in coint_pairs/ and is shared with the backtester, so
a live signal and a backtested signal are computed by the exact same code
path. Mirrors the role of strategies/*.py in algofactory_bot.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from coint_pairs.stats import calculate_cointegration, calculate_pair_beta
from coint_pairs.sizing import vol_parity_notional, calculate_qty

from .config import BotConfig


def rescan_pairs(price_matrix: pd.DataFrame, cfg: BotConfig) -> dict[tuple[str, str], dict]:
    """Full pairwise cointegration screen over the last cfg.window bars of
    price_matrix (BTCUSDT must be a column, used as the beta benchmark).
    """
    tradable = [c for c in price_matrix.columns if c != "BTCUSDT"]
    log_prices = np.log(price_matrix.iloc[-(cfg.window + 1):])
    log_btc = log_prices["BTCUSDT"].values
    btc_ret = np.diff(log_btc)

    eligible = {}
    for a, b in itertools.combinations(tradable, 2):
        price1 = price_matrix[a].iloc[-(cfg.window + 1):].values
        price2 = price_matrix[b].iloc[-(cfg.window + 1):].values
        flag, hedge, hl, pval = calculate_cointegration(
            price1, price2, max_half_life=cfg.max_half_life, fast=True
        )
        if flag != 1:
            continue
        log1 = log_prices[a].values
        log2 = log_prices[b].values
        pair_ret = np.diff(log1) - hedge * np.diff(log2)
        beta = calculate_pair_beta(pair_ret, btc_ret)
        if np.isnan(beta) or abs(beta) >= cfg.beta_threshold:
            continue
        eligible[(a, b)] = {"hedge": hedge, "half_life": hl, "p_value": pval, "beta": beta}
    return eligible


def current_zscore(price_matrix: pd.DataFrame, a: str, b: str, hedge: float, window: int) -> float:
    log1 = np.log(price_matrix[a].iloc[-(window + 1):].values)
    log2 = np.log(price_matrix[b].iloc[-(window + 1):].values)
    spread = log1 - hedge * log2
    m, sd = spread.mean(), spread.std()
    if sd <= 0:
        return float("nan")
    return float((spread[-1] - m) / sd)


def size_pair(price_matrix: pd.DataFrame, a: str, b: str, hedge: float, cfg: BotConfig) -> tuple[float, float]:
    log1 = np.log(price_matrix[a].iloc[-(cfg.window + 1):].values)
    log2 = np.log(price_matrix[b].iloc[-(cfg.window + 1):].values)
    dollar1, dollar2 = vol_parity_notional(
        log1, log2, hedge, capital=cfg.capital_usd,
        max_notional_per_pair=cfg.max_notional_per_pair, lookback=cfg.vol_lookback,
    )
    price1 = float(price_matrix[a].iloc[-1])
    price2 = float(price_matrix[b].iloc[-1])
    return calculate_qty(
        dollar1, dollar2, price1, price2,
        capital=cfg.capital_usd, max_notional_per_pair=cfg.max_notional_per_pair,
    )
