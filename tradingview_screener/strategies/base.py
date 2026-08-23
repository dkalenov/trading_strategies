"""
The interface a strategy needs to expose for execution/position_manager.py
to use it. One concrete strategy exists in this repo
(tradingview_screener.py); this base class exists so a second strategy
has a contract to implement rather than a copy of position_manager.py to
fork. Deliberately small - no registry/plugin-loading system like the
reference architecture's strategies/registry.py, since with exactly one
strategy that machinery has no second caller to justify it yet.
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class Strategy(ABC):
    @abstractmethod
    def get_rating(self, symbol: str, interval: str):
        """Return a models.Rating for `symbol`."""

    @abstractmethod
    def decide(self, symbol_rating, reference_rating) -> str | None:
        """Return Direction.LONG.value, Direction.SHORT.value, or None."""
