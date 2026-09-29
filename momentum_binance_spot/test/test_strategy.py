import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy import (
    build_signal,
    check_exit,
    is_eligible_symbol,
    momentum_confirmed,
    pick_top_gainer,
)


def test_is_eligible_symbol_requires_quote_asset():
    assert is_eligible_symbol("BTCUSDT", "USDT")
    assert not is_eligible_symbol("BTCUSDC", "USDT")


def test_is_eligible_symbol_excludes_known_leveraged_tokens():
    assert not is_eligible_symbol("BTCUPUSDT", "USDT")
    assert not is_eligible_symbol("ETHDOWNUSDT", "USDT")


def test_is_eligible_symbol_does_not_false_positive_on_substring():
    # The original bot used `symbol.str.contains('UP')`, which wrongly
    # excludes real coins whose ticker happens to contain "UP" or "DOWN".
    assert is_eligible_symbol("JUPUSDT", "USDT")
    assert is_eligible_symbol("SUPERUSDT", "USDT")


def test_pick_top_gainer_returns_highest_change():
    changes = {"AAAUSDT": 1.0, "BBBUSDT": 5.5, "CCCUSDT": -2.0}
    assert pick_top_gainer(changes) == "BBBUSDT"


def test_pick_top_gainer_skips_held_symbol():
    changes = {"AAAUSDT": 1.0, "BBBUSDT": 5.5}
    assert pick_top_gainer(changes, skip_symbol="BBBUSDT") == "AAAUSDT"


def test_pick_top_gainer_empty_when_nothing_eligible():
    assert pick_top_gainer({"BTCUPUSDT": 10.0}) is None


def test_momentum_confirmed_requires_price_above_start_of_window():
    assert momentum_confirmed([100, 101, 105])
    assert not momentum_confirmed([100, 99, 95])
    assert not momentum_confirmed([100])


def test_check_exit_take_profit_and_stop_loss():
    signal = build_signal("BTCUSDT", entry_price=100.0, take_profit_pct=0.02, stop_loss_pct=0.015)
    assert signal.take_profit == 102.0
    assert signal.stop_loss == 98.5
    assert check_exit(102.5, signal) == "take_profit"
    assert check_exit(98.0, signal) == "stop_loss"
    assert check_exit(100.5, signal) is None
