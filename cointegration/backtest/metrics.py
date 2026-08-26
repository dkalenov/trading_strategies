"""Performance metrics for a backtest result. 4h bars -> 6 bars/day."""

from __future__ import annotations

import numpy as np
import pandas as pd

BARS_PER_DAY = 6
BARS_PER_YEAR = BARS_PER_DAY * 365


def compute_metrics(equity_curve: pd.Series, trades: list, capital: float, btc_prices: pd.Series) -> dict:
    eq = equity_curve.copy()
    ret = eq.pct_change().dropna()

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    n_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400.0
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (365.0 / n_days) - 1.0 if n_days > 0 else np.nan

    running_max = eq.cummax()
    drawdown = eq / running_max - 1.0
    max_dd = drawdown.min()

    sharpe = np.nan
    if ret.std() > 0:
        sharpe = ret.mean() / ret.std() * np.sqrt(BARS_PER_YEAR)

    cagr_over_mdd = cagr / abs(max_dd) if max_dd < 0 else np.nan

    n_trades = len(trades)
    if n_trades > 0:
        pnl = np.array([tr.net_pnl for tr in trades])
        win_rate = float((pnl > 0).mean())
        avg_pnl = float(pnl.mean())
        avg_win = float(pnl[pnl > 0].mean()) if (pnl > 0).any() else 0.0
        avg_loss = float(pnl[pnl <= 0].mean()) if (pnl <= 0).any() else 0.0
        gross_profit = pnl[pnl > 0].sum()
        gross_loss = -pnl[pnl <= 0].sum()
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else np.nan
        holding_bars = np.array([tr.exit_bar - tr.entry_bar for tr in trades])
        avg_holding_days = float(holding_bars.mean() / BARS_PER_DAY)
        total_costs = float(sum(tr.costs for tr in trades))
        exit_reasons = pd.Series([tr.exit_reason for tr in trades]).value_counts().to_dict()
    else:
        win_rate = avg_pnl = avg_win = avg_loss = profit_factor = avg_holding_days = total_costs = np.nan
        exit_reasons = {}

    # beta of strategy returns vs BTC returns, to check the market-neutral claim
    btc_ret = btc_prices.pct_change().reindex(ret.index)
    aligned = pd.concat([ret, btc_ret], axis=1).dropna()
    aligned.columns = ["strategy", "btc"]
    beta_vs_btc = np.nan
    corr_vs_btc = np.nan
    if len(aligned) > 5 and aligned["btc"].var() > 0:
        beta_vs_btc = float(np.cov(aligned["strategy"], aligned["btc"])[0, 1] / np.var(aligned["btc"]))
        corr_vs_btc = float(aligned["strategy"].corr(aligned["btc"]))

    return {
        "start_date": str(eq.index[0]),
        "end_date": str(eq.index[-1]),
        "n_days": round(n_days, 1),
        "starting_capital": capital,
        "ending_equity": float(eq.iloc[-1]),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if not np.isnan(cagr) else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "cagr_over_mdd": round(cagr_over_mdd, 2) if cagr_over_mdd == cagr_over_mdd else None,
        "sharpe": round(sharpe, 2) if sharpe == sharpe else None,
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate * 100, 2) if win_rate == win_rate else None,
        "avg_trade_pnl": round(avg_pnl, 2) if avg_pnl == avg_pnl else None,
        "avg_win": round(avg_win, 2) if avg_win == avg_win else None,
        "avg_loss": round(avg_loss, 2) if avg_loss == avg_loss else None,
        "profit_factor": round(profit_factor, 2) if profit_factor == profit_factor else None,
        "avg_holding_days": round(avg_holding_days, 2) if avg_holding_days == avg_holding_days else None,
        "total_costs": round(total_costs, 2) if total_costs == total_costs else None,
        "beta_vs_btc": round(beta_vs_btc, 3) if beta_vs_btc == beta_vs_btc else None,
        "corr_vs_btc": round(corr_vs_btc, 3) if corr_vs_btc == corr_vs_btc else None,
        "exit_reasons": exit_reasons,
    }
