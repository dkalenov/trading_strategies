"""
utils.py - logging setup and a retry decorator. Nothing exciting,
just the plumbing that every other module leans on.
"""
from __future__ import annotations

import functools
import logging
import time
from datetime import datetime, timezone


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def retry(attempts: int = 3, delay_seconds: float = 2.0, exceptions: tuple = (Exception,)):
    """Retry a call a few times with a flat delay, then let the last
    exception raise. The original bot's only error handling was a
    single bare `except: sleep(61)` around the very first API call -
    every call after that, including the ones that place real orders,
    had none at all."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    logging.getLogger(fn.__module__).warning(
                        "%s failed (attempt %d/%d): %s",
                        fn.__name__, attempt, attempts, exc,
                    )
                    if attempt < attempts:
                        time.sleep(delay_seconds)
            raise last_exc
        return wrapper
    return decorator
