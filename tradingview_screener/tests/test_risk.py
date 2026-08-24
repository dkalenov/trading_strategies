import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from decimal import Decimal

from risk import RiskManager, decide_entry
from config import StrategyConfig
from models import SymbolFilters, Direction


def test_decide_entry_matches_live_condition():
    assert decide_entry("STRONG_BUY", "NEUTRAL") == Direction.LONG.value
    assert decide_entry("STRONG_BUY", "BUY") == Direction.LONG.value
    assert decide_entry("STRONG_BUY", "STRONG_BUY") == Direction.LONG.value
    assert decide_entry("STRONG_BUY", "SELL") is None
    assert decide_entry("STRONG_BUY", "STRONG_SELL") is None

    assert decide_entry("STRONG_SELL", "NEUTRAL") == Direction.SHORT.value
    assert decide_entry("STRONG_SELL", "SELL") == Direction.SHORT.value
    assert decide_entry("STRONG_SELL", "BUY") is None

    assert decide_entry("BUY", "NEUTRAL") is None       # only STRONG_ triggers entry
    assert decide_entry("NEUTRAL", "NEUTRAL") is None


def test_exit_levels_long():
    risk = RiskManager(StrategyConfig(stop_mult=0.45, take1_mult=2.5, take2_mult=5.0))
    lv = risk.compute_exit_levels(Direction.LONG.value, entry_price=100.0, atr=2.0)
    assert float(lv.stop) == pytest.approx(100 - 0.9)
    assert float(lv.take1) == pytest.approx(100 + 5.0)
    assert float(lv.take2) == pytest.approx(100 + 10.0)


def test_exit_levels_short():
    risk = RiskManager(StrategyConfig(stop_mult=0.45, take1_mult=2.5, take2_mult=5.0))
    lv = risk.compute_exit_levels(Direction.SHORT.value, entry_price=100.0, atr=2.0)
    assert float(lv.stop) == pytest.approx(100 + 0.9)
    assert float(lv.take1) == pytest.approx(100 - 5.0)
    assert float(lv.take2) == pytest.approx(100 - 10.0)


def test_breakeven_stop_matches_original_constant():
    risk = RiskManager(StrategyConfig())
    assert risk.breakeven_stop_price(Direction.LONG.value, 100.0) == pytest.approx(99.9)
    assert risk.breakeven_stop_price(Direction.SHORT.value, 100.0) == pytest.approx(100.1)


def test_position_size_respects_step_and_min_notional():
    risk = RiskManager(StrategyConfig(order_size_usd=10.0))
    filters = SymbolFilters(step_size=0.001, tick_size=0.01, min_notional=None)
    sizing = risk.compute_position_size(3.3333, atr=0.05, direction=Direction.LONG.value, filters=filters)
    assert float(sizing.quantity) == pytest.approx(3.0, abs=0.001)

    filters2 = SymbolFilters(step_size=0.001, tick_size=0.01, min_notional=5.0)
    risk2 = RiskManager(StrategyConfig(order_size_usd=1.0))
    sizing2 = risk2.compute_position_size(100.0, atr=1.0, direction=Direction.LONG.value, filters=filters2)
    assert float(sizing2.quantity) * 100.0 >= 5.0 * 1.1 - 1e-6


def test_position_size_output_has_no_float_precision_artifacts():
    risk = RiskManager(StrategyConfig(order_size_usd=10.0))
    filters = SymbolFilters(step_size=0.0001, tick_size=0.1, min_notional=None)
    sizing = risk.compute_position_size(14696.844346455337, atr=100.0,
                                         direction=Direction.LONG.value, filters=filters)
    assert sizing.quantity == Decimal("0.0006")


def test_position_size_stop_take_prices_are_tick_aligned():
    risk = RiskManager(StrategyConfig(order_size_usd=10.0))
    filters = SymbolFilters(step_size=0.001, tick_size=0.1, min_notional=None)
    sizing = risk.compute_position_size(63289.17064050636, atr=200.0,
                                         direction=Direction.LONG.value, filters=filters)
    for price in (sizing.stop, sizing.take1, sizing.take2):
        cents = (price * 10) % 1
        assert cents == 0, f"{price} is not aligned to tick_size 0.1"


def test_config_validation_rejects_bad_multipliers():
    from config import ConfigError
    with pytest.raises(ConfigError):
        StrategyConfig(stop_mult=-0.1)
    with pytest.raises(ConfigError):
        StrategyConfig(take1_portion=1.5)
    with pytest.raises(ConfigError):
        StrategyConfig(order_size_usd=0)
