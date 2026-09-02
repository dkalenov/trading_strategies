"""
Load klines data from a CSV shaped like:

    Date,Open,High,Low,Close,Volume,Symbol

(long format, one row per symbol per bar) and turn it into a wide matrix of
close prices, one column per symbol, aligned on a common timestamp index.

No historical data file is shipped in this repo (that's on you to source,
e.g. from Binance's public klines endpoint or your own archive). Point
`DATA_CSV` at your own file with the same columns and this will work.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def load_klines_long(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    required = {"Date", "Open", "High", "Low", "Close", "Volume", "Symbol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing expected columns: {missing}")
    return df


def select_universe(
    df_long: pd.DataFrame,
    top_n: int = 60,
    require_full_history: bool = True,
) -> list[str]:
    """Pick the most liquid symbols (by mean dollar volume) that have a
    complete bar count matching the most common bar count in the file
    (i.e. no missing history / no late listing), so all series in the
    universe share the same time index without needing to drop bars.
    """
    df = df_long.copy()
    df["dollar_vol"] = df["Volume"] * df["Close"]
    counts = df.groupby("Symbol").size()
    full_count = counts.mode().iloc[0]
    eligible = counts[counts == full_count].index if require_full_history else counts.index
    liquidity = df[df["Symbol"].isin(eligible)].groupby("Symbol")["dollar_vol"].mean()
    universe = liquidity.sort_values(ascending=False).head(top_n).index.tolist()
    if "BTCUSDT" not in universe and "BTCUSDT" in eligible:
        universe.append("BTCUSDT")  # always keep BTC in, it's the beta benchmark
    return universe


def build_price_matrix(df_long: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Wide matrix of Close prices, index=Date, columns=symbols. Drops any
    row where at least one symbol in the universe has a missing value, so
    downstream code never has to special-case NaNs.
    """
    sub = df_long[df_long["Symbol"].isin(symbols)]
    wide = sub.pivot_table(index="Date", columns="Symbol", values="Close")
    wide = wide[symbols]
    wide = wide.dropna(axis=0, how="any")
    wide = wide.sort_index()
    return wide
