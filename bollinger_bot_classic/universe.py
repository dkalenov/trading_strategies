"""Universe definition - single source of tradeable symbols.

Ported in spirit from algofactory_bot/universe.py. Loads from
deploy/bot_config.toml [universe] section, falls back to a small
hardcoded list if the file isn't found. All sorted.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_FALLBACK_SYMBOLS: tuple[str, ...] = tuple(sorted({
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT",
    "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT",
}))


def _load_from_config() -> tuple[str, ...]:
    try:
        from config_loader import load_universe_from_config
        symbols = load_universe_from_config()
        return symbols if symbols else _FALLBACK_SYMBOLS
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Could not load universe from config: %s - using fallback", exc)
        return _FALLBACK_SYMBOLS


UNIVERSE_SYMBOLS: tuple[str, ...] = _load_from_config()

logger.info("Universe loaded: %d symbols", len(UNIVERSE_SYMBOLS))


def validate_against_exchange(exchange_symbols: dict[str, object]) -> tuple[list[str], list[str]]:
    """Validate universe against exchange symbols. Returns (valid, missing)."""
    valid: list[str] = []
    missing: list[str] = []
    exchange_set = set(exchange_symbols.keys()) if exchange_symbols else set()

    for sym in UNIVERSE_SYMBOLS:
        if sym in exchange_set:
            valid.append(sym)
        else:
            missing.append(sym)
            logger.warning("Universe symbol %s not found on exchange - skipping", sym)

    if missing:
        logger.warning("Universe validation: %d/%d symbols missing", len(missing), len(UNIVERSE_SYMBOLS))
    return valid, missing


def validate_against_dataset(available_symbols: set[str]) -> tuple[list[str], list[str]]:
    """Same as validate_against_exchange but for an offline backtest dataset."""
    return validate_against_exchange({s: None for s in available_symbols})
