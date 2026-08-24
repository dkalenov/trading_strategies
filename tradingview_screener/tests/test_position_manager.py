import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import db
from strategies.tradingview_screener import FakeSignalProvider
from execution.position_manager import PositionManager
from risk import RiskManager
from config import StrategyConfig
from models import Direction, SymbolFilters


class FakeExchange:
    """Enough of the Futures client surface for PositionManager to run
    against, entirely in memory."""

    def __init__(self, klines_by_symbol: dict, filters=None):
        self.klines_by_symbol = klines_by_symbol
        self.mark_prices = {}
        self.orders = []
        self.cancels = []
        self._next_order_id = 1
        self.filters = filters or SymbolFilters(step_size=0.001, tick_size=0.01, min_notional=5.0)

    def get_klines(self, symbol, interval="4h", limit=200):
        return self.klines_by_symbol[symbol][-limit:]

    def get_symbol_filters(self, symbol):
        return self.filters

    def set_leverage(self, symbol, leverage):
        return {"leverage": leverage}

    def get_mark_price(self, symbol):
        return self.mark_prices[symbol]

    def _order(self, **kwargs):
        self._next_order_id += 1
        order = {"orderId": self._next_order_id, **kwargs}
        self.orders.append(order)
        return order

    def new_market_order(self, symbol, side, quantity, reduce_only=False):
        return self._order(symbol=symbol, side=side, type="MARKET", quantity=quantity, reduceOnly=reduce_only)

    def new_algo_stop_market_order(self, symbol, side, trigger_price, close_position=True,
                                    quantity=None, order_type="STOP_MARKET", working_type="CONTRACT_PRICE"):
        order = self._order(symbol=symbol, side=side, type=order_type, triggerPrice=trigger_price,
                             algoType="CONDITIONAL")
        order["algoId"] = order["orderId"]
        return order

    def cancel_algo_order(self, symbol, algo_id):
        self.cancels.append(("algo", symbol, algo_id))
        return {"algoId": algo_id, "algoStatus": "CANCELED"}

    def new_limit_order(self, symbol, side, price, quantity, reduce_only=True, time_in_force="GTC"):
        return self._order(symbol=symbol, side=side, type="LIMIT", price=price, quantity=quantity)

    def cancel_order(self, symbol, order_id):
        self.cancels.append(("limit", symbol, order_id))
        return {"orderId": order_id, "status": "CANCELED"}


def make_klines(closes):
    return [[i, c, c * 1.01, c * 0.99, c, 100, i, 0, 0, 0, 0, 0] for i, c in enumerate(closes)]


@pytest.fixture
def fresh_db(tmp_path):
    db.connect(str(tmp_path / "state.sqlite3"))
    yield db
    db.close()


def build_manager(closes, dry_run=True):
    fake_signals = FakeSignalProvider()
    fake_exchange = FakeExchange({"BTCUSDT": make_klines(closes), "ETHUSDT": make_klines(closes)})
    risk = RiskManager(StrategyConfig(atr_length=14, order_size_usd=10.0))
    pm = PositionManager(fake_exchange, fake_signals, risk, watchlist=["ETHUSDT"],
                          scfg=risk._config, dry_run=dry_run)
    return pm, fake_exchange, fake_signals


def test_no_entry_without_strong_signal(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "BUY")  # BUY, not STRONG_BUY -> no entry
    pm.run_entry_cycle()
    assert db.get_open_trades() == []


def test_entry_opens_long_dry_run(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=True)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()

    open_trades = db.get_open_trades()
    assert len(open_trades) == 1
    t = open_trades[0]
    assert t.direction == Direction.LONG.value
    assert t.symbol == "ETHUSDT"
    assert t.stop < t.entry_price < t.take1 < t.take2
    assert exch.orders == [], "dry run must never place a real order"


def test_entry_opens_short_and_places_orders_when_live(fresh_db):
    closes = [100 - i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_SELL")
    pm.run_entry_cycle()

    open_trades = db.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].direction == Direction.SHORT.value
    assert len(exch.orders) == 3
    assert exch.orders[0]["type"] == "MARKET"
    assert exch.orders[1]["type"] == "STOP_MARKET"
    assert "algoId" in exch.orders[1]
    assert exch.orders[2]["type"] == "LIMIT"


def test_no_second_entry_while_position_open(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    pm.run_entry_cycle()
    assert len(db.get_open_trades()) == 1


def test_take1_cancels_and_resizes_take2_order(fresh_db):
    """The bug: after a 5% partial close at take1, the take2 LIMIT order
    used to keep resting at the full pre-take1 quantity - oversized
    against what's actually left in the position. Binance would reject
    it as reduce-only exceeding the position, or partially fill it and
    leave the qty accounting wrong for the eventual market-close."""
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    t = db.get_open_trades()[0]
    original_take2_id = t.take2_order_id
    original_qty = t.qty_full

    exch.mark_prices["ETHUSDT"] = t.take1 + 0.01
    pm.poll_open_positions()
    t = db.get_open_trades()[0]

    limit_cancels = [c for c in exch.cancels if c[0] == "limit"]
    assert len(limit_cancels) == 1, "the stale full-size take2 order must be cancelled at take1"
    assert limit_cancels[0][2] == original_take2_id

    take2_orders = [o for o in exch.orders if o["type"] == "LIMIT"]
    assert len(take2_orders) == 2, "one at entry, one resized after take1"
    new_take2 = take2_orders[-1]
    assert new_take2["quantity"] == pytest.approx(t.qty_remaining)
    assert new_take2["quantity"] < original_qty
    assert new_take2["price"] == pytest.approx(t.take2)
    assert str(new_take2["orderId"]) == t.take2_order_id, "db must be updated with the new take2 order id"


def test_take1_then_take2_full_lifecycle(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    t = db.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.take1 + 0.01
    pm.poll_open_positions()
    t = db.get_open_trades()[0]
    assert t.take1_done is True
    assert t.qty_remaining < t.qty_full

    exch.mark_prices["ETHUSDT"] = t.take2 + 0.01
    pm.poll_open_positions()
    assert db.get_open_trades() == []


def test_stop_before_take1_closes_full_position_as_loss(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    t = db.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.stop - 0.01
    pm.poll_open_positions()
    assert db.get_open_trades() == []


def test_breakeven_stop_after_take1_closes_remainder(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    t = db.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.take1 + 0.01
    pm.poll_open_positions()
    t = db.get_open_trades()[0]
    assert t.take1_done

    exch.mark_prices["ETHUSDT"] = t.breakeven_stop - 0.01
    pm.poll_open_positions()
    assert db.get_open_trades() == []


def test_stop_uses_algo_order_endpoint_not_regular_order(fresh_db):
    closes = [100 - i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_SELL")
    pm.run_entry_cycle()

    stop_orders = [o for o in exch.orders if o["type"] == "STOP_MARKET"]
    assert len(stop_orders) == 1
    assert "algoId" in stop_orders[0]
    assert stop_orders[0].get("algoType") == "CONDITIONAL"


def test_take2_exit_cancels_stops_via_algo_endpoint(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    t = db.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.take1 + 0.01
    pm.poll_open_positions()
    t = db.get_open_trades()[0]
    exch.mark_prices["ETHUSDT"] = t.take2 + 0.01
    pm.poll_open_positions()

    algo_cancels = [c for c in exch.cancels if c[0] == "algo"]
    # one when take1 replaces the original stop with a breakeven stop,
    # one more when take2 fires and the breakeven stop needs cancelling
    assert len(algo_cancels) == 2


def test_stop_exit_cancels_take2_via_regular_order_endpoint(fresh_db):
    closes = [100 + i * 0.1 for i in range(20)]
    pm, exch, sig = build_manager(closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    pm.run_entry_cycle()
    t = db.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.stop - 0.01
    pm.poll_open_positions()

    limit_cancels = [c for c in exch.cancels if c[0] == "limit"]
    assert len(limit_cancels) == 1
