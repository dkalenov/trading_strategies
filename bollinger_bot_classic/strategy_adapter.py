"""Strategy adapter - protocol, context, category classification, registry dispatch.

Ported 1:1 from algofactory_bot/strategy_adapter.py.

IntentGenerator Protocol: generate(ctx) -> list[OrderIntentDraft]
StrategyContext: equity, current_weights, target_weights, frames, symbol_infos, open_positions
classify_symbol: maps symbol -> category (L1_BIG, AI, MEME, DEFI, OTHER) from TOML
build_adapter(bot_config) -> IntentGenerator via plugin registry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from config import BotConfig
from models import OrderIntentDraft

logger = logging.getLogger(__name__)

# Taxonomy loaded from [taxonomy] section of bot_config.toml
_taxonomy_cache: dict[str, frozenset[str]] | None = None


def _load_taxonomy() -> dict[str, frozenset[str]]:
    """Load taxonomy from bot_config.toml [taxonomy] section. Cached after first call."""
    global _taxonomy_cache
    if _taxonomy_cache is None:
        from config_loader import load_taxonomy_from_config
        _taxonomy_cache = load_taxonomy_from_config()
    return _taxonomy_cache


def preload_taxonomy() -> None:
    """Eagerly load taxonomy at startup (avoids lazy load during signal cycle)."""
    _load_taxonomy()


ALLOWED_CATEGORIES = frozenset({"AI", "MEME", "L1_BIG", "DEFI", "OTHER"})


def classify_symbol(sym: str) -> str:
    """Classify symbol using taxonomy from TOML config."""
    taxonomy = _load_taxonomy()
    for cat, syms in taxonomy.items():
        if sym in syms:
            return cat
    return "OTHER"


class IntentGenerator(Protocol):
    def generate(self, ctx: StrategyContext) -> list[OrderIntentDraft]:
        ...

    def warmup_bars(self) -> int:
        """Minimum number of completed bars needed by this strategy.

        Framework uses this to size the historical warmup fetch. For
        Bollinger reversion: needs at least bb_period bars for the SMA/std,
        plus a couple extra for the ATR(14) stop sizing and the
        prev-bar-cross check -> warmup_bars() = bb_period + 15.
        """
        ...


@dataclass(frozen=True, slots=True)
class StrategyContext:
    equity: float
    current_weights: dict[str, float]
    target_weights: dict[str, float]
    frames: dict[str, object]
    symbol_infos: dict
    open_positions: dict[str, object]


def build_adapter(bot_config: BotConfig) -> IntentGenerator:
    """Load strategy adapter via plugin registry (importlib-based).

    No if/elif - registry maps kind -> module.class.
    """
    from strategies.registry import load_adapter
    return load_adapter(bot_config.strategy_kind, bot_config)
