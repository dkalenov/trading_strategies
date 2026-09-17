"""
strategy.py - the actual trading rule.

This module has no idea Binance exists. It doesn't place orders, it
doesn't know your balance, it doesn't sleep or retry anything. It
just answers two questions from price data:

  1. given a list of tickers, which symbol should I look at?
  2. given some recent candles for that symbol, is the signal live?

bot.py (live trading) and backtest.py (historical simulation) both
import this file and call the same functions, so the rule being
tested is provably the same rule being traded. That's the whole
point of splitting it out like this - if the two ever disagreed,
the backtest would be lying to you.

The rule itself is unchanged from the original script: buy whatever
USDT pair has the largest recent gain, but only if its most recent
price move is also still pointing up. Exit at a fixed take-profit or
stop-loss. See README.md for an honest opinion on whether that's a
good idea.
"""
from __future__ import annotations

from dataclasses import dataclass

# Binance delisted its leveraged UP/DOWN/BULL/BEAR tokens back in 2021,
# so in practice this list matches nothing on the exchange today - it's
# kept only so old configs/tests that reference it keep working, and so
# the filter does the right thing if Binance ever brings tokens like
# this back. See AUDIT.md for why this replaced the original's
# substring check, which had a real bug (see AUDIT.md item 3).
_LEVERAGED_BASES = (
    "BTC", "ETH", "ADA", "BNB", "EOS", "XRP", "LINK", "TRX",
    "LTC", "DOT", "UNI", "FIL", "YFI", "SXP", "SUSHI",
)
_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def leveraged_token_symbols(quote_asset: str = "USDT") -> frozenset[str]:
    return frozenset(
        f"{base}{suffix}{quote_asset}"
        for base in _LEVERAGED_BASES
        for suffix in _LEVERAGED_SUFFIXES
    )


@dataclass(frozen=True)
class Signal:
    """An open position's plan: what we paid and where we get out."""
    symbol: str
    entry_price: float
    take_profit: float
    stop_loss: float


def is_eligible_symbol(
    symbol: str,
    quote_asset: str = "USDT",
    excluded: frozenset[str] | None = None,
) -> bool:
    """Plain spot pair against the quote asset, not a leveraged token."""
    if not symbol.endswith(quote_asset):
        return False
    excluded = excluded if excluded is not None else leveraged_token_symbols(quote_asset)
    return symbol not in excluded


def pick_top_gainer(
    changes: dict[str, float],
    quote_asset: str = "USDT",
    excluded: frozenset[str] | None = None,
    skip_symbol: str | None = None,
) -> str | None:
    """
    changes: {symbol: recent % change}. Works whether that number came
    from Binance's live 24h ticker or from a backtest computing its own
    trailing return over historical candles - the selection rule itself
    doesn't care where the number came from.

    Returns the symbol with the largest change among eligible symbols,
    or None if nothing qualifies.
    """
    excluded = excluded if excluded is not None else leveraged_token_symbols(quote_asset)
    candidates = {
        symbol: change
        for symbol, change in changes.items()
        if is_eligible_symbol(symbol, quote_asset, excluded) and symbol != skip_symbol
    }
    if not candidates:
        return None
    return max(candidates, key=candidates.get)


def momentum_confirmed(closes: list[float]) -> bool:
    """
    The original bot's entry filter, unchanged: is the most recent
    price higher than the first price in the lookback window? Same
    formula as the original `(pct_change+1).cumprod()` check, just
    written directly as closes[-1] > closes[0] since that's all that
    calculation reduces to.

    Live, this window is the last N minutes of 1m candles. In the
    backtest, it's fed a 2-point [open, close] of the most recent
    completed bar, since that's the finest resolution the historical
    data has - see README.md "How the backtest differs from live"
    for why that's an approximation and not a like-for-like replay.
    """
    if len(closes) < 2:
        return False
    return closes[-1] > closes[0]


def build_signal(
    symbol: str,
    entry_price: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> Signal:
    return Signal(
        symbol=symbol,
        entry_price=entry_price,
        take_profit=entry_price * (1 + take_profit_pct),
        stop_loss=entry_price * (1 - stop_loss_pct),
    )


def check_exit(price: float, signal: Signal) -> str | None:
    """Returns 'take_profit', 'stop_loss', or None if still open."""
    if price >= signal.take_profit:
        return "take_profit"
    if price <= signal.stop_loss:
        return "stop_loss"
    return None
