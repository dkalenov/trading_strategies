"""Utility functions for the bollinger_bot.

Ported 1:1 from algofactory_bot/utils.py. Formatting (fmt_price, fmt_qty)
uses plain decimal strings, never scientific notation. Step-rounding uses
Decimal for exactness. Klines converter matches Binance REST format.
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, TypeVar

import pandas as pd

logger = logging.getLogger(__name__)

T = TypeVar("T")

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "8h": 28800, "1d": 86400,
}


# === Formatting ===========================================================

def fmt_price(price: float | Decimal, precision: int) -> str:
    return f"{price:.{precision}f}"


def fmt_qty(qty: float | Decimal, precision: int) -> str:
    return f"{qty:.{precision}f}"


# === Step rounding (Decimal) ==============================================

def floor_to_step(qty: Decimal, step_size: int) -> Decimal:
    """Round DOWN to nearest step. step_size = decimal-places int."""
    factor = 10 ** step_size
    return Decimal((qty * factor).__floor__()) / factor


def ceil_to_step(qty: Decimal, step_size: int) -> Decimal:
    factor = 10 ** step_size
    return Decimal(-(-qty * factor).__floor__()) / factor


def round_price(price: float | Decimal, tick_size: int) -> float:
    return round(float(price), tick_size)


# === Async helpers =========================================================

async def shutdown_sleep(seconds: float, shutdown_event: asyncio.Event) -> bool:
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
        return True
    except TimeoutError:
        return False


# === Process lock ==========================================================

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock(lock_path: Path) -> bool:
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
            if old_pid != os.getpid() and _pid_alive(old_pid):
                logger.warning("Lock held by alive PID %d - cannot acquire", old_pid)
                return False
        except (ValueError, OSError):
            pass
    lock_path.write_text(str(os.getpid()))
    return True


def release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


# === Klines ================================================================

def klines_to_dataframe(klines: list[list]) -> pd.DataFrame:
    """Binance REST klines -> DataFrame with a UTC DatetimeIndex."""
    if not klines:
        return pd.DataFrame()
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]]


# === Candle wait ============================================================

async def wait_for_next_candle(interval: str, offset_seconds: int = 10,
                                shutdown_event: asyncio.Event | None = None) -> None:
    """Sleep until next candle close + offset. Signals fire AFTER close."""
    interval_sec = _INTERVAL_SECONDS.get(interval, 14400)
    now = datetime.now(UTC)
    epoch = now.timestamp()
    next_close = (int(epoch / interval_sec) + 1) * interval_sec + offset_seconds
    wait = max(0.0, next_close - epoch)
    next_time = datetime.fromtimestamp(next_close, tz=UTC)
    logger.info("Next %s candle: %s (in %.1fm)", interval, next_time.strftime("%H:%M:%S UTC"), wait / 60)
    if shutdown_event is not None:
        await shutdown_sleep(wait, shutdown_event)
    else:
        await asyncio.sleep(wait)


# === Multiprocessing ========================================================

def run_parallel(func: Callable[[T], object], items: list[T], workers: int = 8) -> list[object]:
    """Run func(item) in parallel via ProcessPoolExecutor for large universes."""
    if not items:
        return []
    if len(items) == 1:
        return [func(items[0])]
    effective_workers = min(workers, len(items))
    logger.info("run_parallel: %d items across %d workers", len(items), effective_workers)
    with ProcessPoolExecutor(max_workers=effective_workers) as executor:
        results = list(executor.map(func, items))
    return results
