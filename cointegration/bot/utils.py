"""Small shared helpers. Mirrors utils.py in algofactory_bot."""

from __future__ import annotations

import logging
import math
import sys


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("coint_bot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
    return logger


def round_step(value: float, step: float) -> float:
    """Round a quantity down to the exchange's lot-size step."""
    if step <= 0:
        return value
    precision = max(0, -int(round(math.log10(step))))
    return math.floor(value / step) * step if precision == 0 else round(
        math.floor(value / step) * step, precision
    )


def today_str() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
