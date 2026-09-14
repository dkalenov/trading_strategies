"""Binance error code classification.

Ported in spirit from algofactory_bot/exchange/errors.py. Only the codes
this bot's execution path actually branches on are kept - the reference's
much larger table (dozens of -1xxx/-2xxx/-4xxx codes for the full live
order lifecycle) is trimmed to what position_manager.py here handles.
"""
from __future__ import annotations


class BinanceAPIError(Exception):
    """Raised by binance/client.py on a non-2xx REST response."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# Codes this bot explicitly handles (see execution/position_manager.py)
REDUCE_ONLY_REJECTED = -2022        # retry without reduceOnly (position already flat)
ALGO_ORDER_NOT_SUPPORTED = -4120    # fall back to STOP_MARKET without algo endpoint
MARGIN_TYPE_UNCHANGED = -4046       # change_margin_type no-op, safe to ignore
UNAUTHORIZED = -2015
INVALID_SIGNATURE = -1022


def is_retryable(code: int) -> bool:
    """Transient errors worth a short retry (rate limits, timeouts)."""
    return code in (-1003, -1006, -1007, -1021)


def is_fatal_auth(code: int) -> bool:
    """Auth errors that should stop the bot, not retry forever."""
    return code in (UNAUTHORIZED, INVALID_SIGNATURE)
