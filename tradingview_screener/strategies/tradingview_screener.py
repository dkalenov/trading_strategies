"""
The one strategy this repo trades: TradingView's technical rating,
filtered against BTC's rating as a regime check. Fetches the real rating
via the `tradingview_ta` package - deliberately not a local
reimplementation of TradingView's formula, which is exactly what the
original project's backtest did and which its own notes admit didn't
match the real thing (see docs/AUDIT.md, C4).

TradingView rate-limits programmatic access (HTTP 429). This module
adds a small inter-request delay and retries with backoff so the bot
doesn't hammer the endpoint on every candle for every symbol in the
watchlist.
"""
from __future__ import annotations
import logging
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tradingview_ta import TA_Handler, Interval
from models import Rating
from risk import decide_entry
from strategies.base import Strategy

log = logging.getLogger("strategy.tv")

INTERVAL_MAP = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
}


class TradingViewScreenerStrategy(Strategy):
    def __init__(self, exchange_name: str = "BINANCE", screener: str = "crypto",
                 request_delay_sec: float = 1.0, max_retries: int = 3):
        self.exchange_name = exchange_name
        self.screener = screener
        self.request_delay_sec = request_delay_sec
        self.max_retries = max_retries
        self._last_request_ts = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.request_delay_sec:
            time.sleep(self.request_delay_sec - elapsed)

    def get_rating(self, symbol: str, interval: str = "4h") -> Rating:
        handler = TA_Handler(
            symbol=symbol, exchange=self.exchange_name,
            screener=self.screener, interval=INTERVAL_MAP[interval],
        )
        last_exc = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                summary = handler.get_analysis().summary
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                if "429" in msg and attempt < self.max_retries - 1:
                    backoff = self.request_delay_sec * (2 ** attempt)
                    log.warning("TradingView 429 for %s, retry %d/%d after %.1fs",
                                symbol, attempt + 1, self.max_retries, backoff)
                    time.sleep(backoff)
                    continue
                raise
            self._last_request_ts = time.monotonic()
            return Rating(
                symbol=symbol,
                recommendation=summary["RECOMMENDATION"],
                buy_count=summary.get("BUY", 0),
                sell_count=summary.get("SELL", 0),
                neutral_count=summary.get("NEUTRAL", 0),
            )
        raise last_exc  # type: ignore[misc]

    def decide(self, symbol_rating: Rating, reference_rating: Rating) -> str | None:
        return decide_entry(symbol_rating.recommendation, reference_rating.recommendation)


class FakeSignalProvider(Strategy):
    """Test double: returns whatever you pre-load, no network call ever.
    Used by tests/ and by anyone smoke-testing execution/position_manager.py
    without hitting TradingView."""

    def __init__(self):
        self._ratings: dict[str, Rating] = {}

    def set(self, symbol: str, recommendation: str) -> None:
        self._ratings[symbol] = Rating(symbol, recommendation)

    def get_rating(self, symbol: str, interval: str = "4h") -> Rating:
        if symbol not in self._ratings:
            raise KeyError(f"no fake rating set for {symbol}")
        return self._ratings[symbol]

    def decide(self, symbol_rating: Rating, reference_rating: Rating) -> str | None:
        return decide_entry(symbol_rating.recommendation, reference_rating.recommendation)


class DebugSignalProvider(Strategy):
    """Debug mode: fetches real TradingView ratings (proves TV connection
    works) but forces LONG for every symbol in the watchlist (proves
    Binance connection + full entry/TP/SL pipeline works). Use with
    --debug-entry."""

    def __init__(self, real_strategy: Strategy, btc_rating: Rating | None = None):
        self._real = real_strategy
        self._btc_rating = btc_rating

    def get_rating(self, symbol: str, interval: str = "4h") -> Rating:
        return self._real.get_rating(symbol, interval)

    def decide(self, symbol_rating: Rating, reference_rating: Rating) -> str | None:
        from models import Direction
        log.warning("DEBUG ENTRY: forcing LONG for %s (real signal=%s, btc=%s)",
                     symbol_rating.symbol, symbol_rating.recommendation,
                     reference_rating.recommendation if reference_rating else "N/A")
        return Direction.LONG.value
