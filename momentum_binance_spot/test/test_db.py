import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


def test_log_trade_and_get_open_position(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    buy_id = db.log_trade(conn, symbol="BTCUSDT", side="BUY", quantity=0.01,
                           price=50000.0, reason="momentum entry")

    open_pos = db.get_open_position(conn)
    assert open_pos is not None
    assert open_pos["id"] == buy_id
    assert open_pos["symbol"] == "BTCUSDT"


def test_get_open_position_none_once_closed(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    buy_id = db.log_trade(conn, symbol="ETHUSDT", side="BUY", quantity=1.0, price=3000.0)
    db.log_trade(conn, symbol="ETHUSDT", side="SELL", quantity=1.0, price=3060.0,
                 reason="take_profit", opened_trade_id=buy_id)

    assert db.get_open_position(conn) is None


def test_attach_oco_is_visible_on_recovered_position(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    buy_id = db.log_trade(conn, symbol="SOLUSDT", side="BUY", quantity=2.0, price=100.0)
    db.attach_oco(conn, buy_id, order_list_id=111, stop_order_id=222, limit_order_id=333)

    open_pos = db.get_open_position(conn)
    assert open_pos["oco_order_list_id"] == 111
    assert open_pos["oco_stop_order_id"] == 222
    assert open_pos["oco_limit_order_id"] == 333


def test_open_position_without_oco_has_null_columns(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.log_trade(conn, symbol="ADAUSDT", side="BUY", quantity=10.0, price=0.5)

    open_pos = db.get_open_position(conn)
    assert open_pos["oco_stop_order_id"] is None
    assert open_pos["oco_limit_order_id"] is None
