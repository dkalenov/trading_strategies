import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import db
from models import TradeRecord


@pytest.fixture
def fresh_db(tmp_path):
    db.connect(str(tmp_path / "test.sqlite3"))
    yield db
    db.close()


def _sample_record(symbol="ETHUSDT"):
    return TradeRecord(
        symbol=symbol, direction="LONG", entry_time="2026-01-01T00:00:00+00:00",
        entry_price=100.0, atr=2.0, stop=99.1, take1=105.0, take2=110.0,
        qty_full=0.1, qty_remaining=0.1,
    )


def test_save_and_get_open_trade(fresh_db):
    trade_id = db.save_trade(_sample_record())
    open_trades = db.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].id == trade_id
    assert open_trades[0].symbol == "ETHUSDT"
    assert open_trades[0].status == "open"


def test_has_open_trade(fresh_db):
    assert db.has_open_trade("ETHUSDT") is False
    db.save_trade(_sample_record())
    assert db.has_open_trade("ETHUSDT") is True
    assert db.has_open_trade("BTCUSDT") is False


def test_mark_take1_done_updates_fields(fresh_db):
    trade_id = db.save_trade(_sample_record())
    db.mark_take1_done(trade_id, breakeven_stop=99.9, qty_remaining=0.095, new_stop_order_id="algo-1")
    trade = db.get_open_trades()[0]
    assert trade.take1_done is True
    assert trade.breakeven_stop == 99.9
    assert trade.qty_remaining == 0.095
    assert trade.stop_order_id == "algo-1"


def test_close_trade_removes_from_open(fresh_db):
    trade_id = db.save_trade(_sample_record())
    db.close_trade(trade_id, exit_reason="TAKE2", pnl_usd=1.23)
    assert db.get_open_trades() == []
    assert db.has_open_trade("ETHUSDT") is False


def test_multiple_symbols_independent(fresh_db):
    db.save_trade(_sample_record("ETHUSDT"))
    db.save_trade(_sample_record("BTCUSDT"))
    assert len(db.get_open_trades()) == 2
    assert db.has_open_trade("ETHUSDT")
    assert db.has_open_trade("BTCUSDT")
