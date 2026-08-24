import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.signals import FakeSignalProvider
from bot.state import TradeStore
from bot.trader import Trader
from core.strategy import StrategyConfig, LONG, SHORT


class FakeExchange:
    """Enough of the BinanceFuturesClient surface for Trader to run against,
    entirely in memory. Klines are handed a rising/falling price series so
    ATR and Close come out as fixed, known numbers."""

    def __init__(self, klines_by_symbol: dict, filters=None):
        self.klines_by_symbol = klines_by_symbol
        self.mark_prices = {}
        self.orders = []
        self._next_order_id = 1
        from bot.exchange import SymbolFilters
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

    def new_stop_market_order(self, symbol, side, stop_price, close_position=True, quantity=None):
        return self._order(symbol=symbol, side=side, type="STOP_MARKET", stopPrice=stop_price)

    def new_limit_order(self, symbol, side, price, quantity, reduce_only=True, time_in_force="GTC"):
        return self._order(symbol=symbol, side=side, type="LIMIT", price=price, quantity=quantity)

    def cancel_order(self, symbol, order_id):
        return {"orderId": order_id, "status": "CANCELED"}


def make_klines(closes):
    """Binance kline rows: [open_time, open, high, low, close, volume, ...6 more]"""
    rows = []
    for i, c in enumerate(closes):
        rows.append([i, c, c * 1.01, c * 0.99, c, 100, i, 0, 0, 0, 0, 0])
    return rows


def build_trader(tmp_path, closes, dry_run=True):
    fake_signals = FakeSignalProvider()
    store = TradeStore(str(tmp_path / "state.sqlite3"))
    fake_exchange = FakeExchange({"BTCUSDT": make_klines(closes), "ETHUSDT": make_klines(closes)})
    trader = Trader(fake_exchange, fake_signals, store, watchlist=["ETHUSDT"],
                     scfg=StrategyConfig(atr_length=14, order_size_usd=10.0),
                     dry_run=dry_run)
    return trader, fake_exchange, fake_signals, store


def test_no_entry_without_strong_signal(tmp_path):
    closes = [100 + i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "BUY")  # BUY, not STRONG_BUY -> no entry
    trader.run_entry_cycle()
    assert store.get_open_trades() == []


def test_entry_opens_long_dry_run(tmp_path):
    closes = [100 + i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes, dry_run=True)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    trader.run_entry_cycle()

    open_trades = store.get_open_trades()
    assert len(open_trades) == 1
    t = open_trades[0]
    assert t.direction == LONG
    assert t.symbol == "ETHUSDT"
    assert t.stop < t.entry_price < t.take1 < t.take2
    assert exch.orders == [], "dry run must never place a real order"


def test_entry_opens_short_and_places_orders_when_live(tmp_path):
    closes = [100 - i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_SELL")
    trader.run_entry_cycle()

    open_trades = store.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].direction == SHORT
    # market entry + stop + take2 = 3 orders
    assert len(exch.orders) == 3
    assert exch.orders[0]["type"] == "MARKET"
    assert exch.orders[1]["type"] == "STOP_MARKET"
    assert exch.orders[2]["type"] == "LIMIT"


def test_no_second_entry_while_position_open(tmp_path):
    closes = [100 + i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    trader.run_entry_cycle()
    trader.run_entry_cycle()  # signal still STRONG_BUY on the next check
    assert len(store.get_open_trades()) == 1


def test_take1_then_take2_full_lifecycle(tmp_path):
    closes = [100 + i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    trader.run_entry_cycle()
    t = store.get_open_trades()[0]

    # price rallies through take1
    exch.mark_prices["ETHUSDT"] = t.take1 + 0.01
    trader.poll_open_positions()
    t = store.get_open_trades()[0]
    assert t.take1_done is True
    assert t.qty_remaining < t.qty_full

    # then through take2
    exch.mark_prices["ETHUSDT"] = t.take2 + 0.01
    trader.poll_open_positions()
    assert store.get_open_trades() == []


def test_stop_before_take1_closes_full_position_as_loss(tmp_path):
    closes = [100 + i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    trader.run_entry_cycle()
    t = store.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.stop - 0.01
    trader.poll_open_positions()
    assert store.get_open_trades() == []


def test_breakeven_stop_after_take1_closes_remainder(tmp_path):
    closes = [100 + i * 0.1 for i in range(20)]
    trader, exch, sig, store = build_trader(tmp_path, closes, dry_run=False)
    sig.set("BTCUSDT", "NEUTRAL")
    sig.set("ETHUSDT", "STRONG_BUY")
    trader.run_entry_cycle()
    t = store.get_open_trades()[0]

    exch.mark_prices["ETHUSDT"] = t.take1 + 0.01
    trader.poll_open_positions()
    t = store.get_open_trades()[0]
    assert t.take1_done

    exch.mark_prices["ETHUSDT"] = t.breakeven_stop - 0.01
    trader.poll_open_positions()
    assert store.get_open_trades() == []
