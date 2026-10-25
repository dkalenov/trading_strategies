"""Regression tests for accounting, walk-forward windows and live safety."""

import numpy as np
import pandas as pd
import pytest

import live_testnet as live
from backtester import Backtester
from filter_optimization import apply_filter, evaluate_filter
from strategy import StrategyParams
from walk_forward import make_windows


def random_walk_df(seed, n=600, freq="1h"):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 0.6, n))
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    return pd.DataFrame({
        "Open": closes, "High": closes + 0.4, "Low": closes - 0.4,
        "Close": closes, "Volume": 1000.0}, index=idx)


PARAMS = StrategyParams(bb_timeperiod=20)


def test_slippage_is_counted_once():
    s = 0.001
    bt = Backtester(commission_rate=0.0, slippage_rate=s, funding_rate=0.0)
    checked = 0
    for seed in range(5):
        _, trades = bt.run(random_walk_df(seed), "basic", PARAMS)
        for t in trades:
            gross = ((t.exit_price - t.entry_price) if t.side == "LONG"
                     else (t.entry_price - t.exit_price)) * t.quantity
            assert t.pnl == pytest.approx(gross)  # no second deduction
            checked += 1
    assert checked > 10


@pytest.mark.parametrize("variant", ["basic", "rsi_filter", "squeeze", "mean_reversion"])
def test_final_equity_matches_trade_log(variant):
    bt = Backtester()
    for seed in range(5):
        stats, trades = bt.run(random_walk_df(seed), variant, PARAMS)
        if trades:
            assert stats.final_capital == pytest.approx(bt.initial_capital + sum(t.pnl for t in trades))


def test_open_position_at_end_is_reflected_in_equity():
    # Force an END_OF_DATA close and make sure its costs are in final equity.
    bt = Backtester()
    hit = 0
    for seed in range(20):
        stats, trades = bt.run(random_walk_df(seed, n=300), "basic", PARAMS)
        if trades and trades[-1].exit_reason == "END_OF_DATA":
            hit += 1
            assert stats.final_capital == pytest.approx(bt.initial_capital + sum(t.pnl for t in trades))
    assert hit > 0, "no seed ended with an open position; test data needs adjusting"


def test_funding_period_follows_bar_size():
    assert Backtester(interval="1h").funding_interval_bars == pytest.approx(8)
    assert Backtester(interval="5m").funding_interval_bars == pytest.approx(96)
    assert Backtester(interval="4h").funding_interval_bars == pytest.approx(2)
    assert Backtester(interval="5m").bars_per_year == 105120


def test_mean_reversion_exit_signal_is_not_a_new_entry():
    bt = Backtester()
    exits_by_signal = 0
    for seed in range(15):
        _, trades = bt.run(random_walk_df(seed, n=800), "mean_reversion", PARAMS)
        for prev, nxt in zip(trades, trades[1:]):
            assert nxt.entry_time >= prev.exit_time
            if prev.exit_reason.startswith("SIGNAL"):
                exits_by_signal += 1
                assert nxt.entry_time > prev.exit_time, "exit signal reopened a trade on the same bar"
    assert exits_by_signal > 0


@pytest.mark.parametrize("cfg", [(12, 3, 3), (6, 3, 3), (6, 1, 1), (3, 1, 1)])
def test_walk_forward_windows_do_not_overlap(cfg):
    train, test, step = cfg
    idx = pd.date_range("2024-01-01", "2025-12-31 23:00", freq="1h")
    df = pd.DataFrame({"x": 1}, index=idx)
    tests = []
    for a, b, c, d in make_windows("2024-01", "2025-12", train, test, step):
        tr, te = df[a:b], df[c:d]
        assert tr.index.intersection(te.index).empty
        assert tr.index.max() < te.index.min()
        tests.append(te.index)
    for i in range(len(tests)):
        for j in range(i + 1, len(tests)):
            assert tests[i].intersection(tests[j]).empty


def test_prefix_invariance_no_future_data():
    bt = Backtester()
    df = random_walk_df(3, n=800)
    _, full = bt.run(df, "basic", PARAMS)
    cut = 500
    _, pre = bt.run(df.iloc[:cut], "basic", PARAMS)
    end = df.index[cut - 1]
    key = lambda t: (t.entry_time, t.exit_time, t.side, t.exit_reason)
    assert [key(t) for t in pre if t.exit_reason != "END_OF_DATA"] == \
           [key(t) for t in full if t.exit_time <= end]


# ---- live safety -----------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(live, "_bybit_request",
                        lambda method, endpoint, params, base_url=None: calls.append((method, endpoint, params)) or {})
    return calls


@pytest.mark.parametrize("closing_side,sl_dir,tp_dir", [("Sell", 2, 1), ("Buy", 1, 2)])
def test_trigger_directions(captured, closing_side, sl_dir, tp_dir):
    live.place_stop_loss("BTCUSDT", closing_side, 100.0, 1.0, 2, 3)
    live.place_take_profit("BTCUSDT", closing_side, 110.0, 1.0, 2, 3)
    assert captured[0][2]["triggerDirection"] == sl_dir
    assert captured[1][2]["triggerDirection"] == tp_dir


def test_dry_run_blocks_all_writes(monkeypatch):
    monkeypatch.setattr(live, "WRITES_ENABLED", False)
    with pytest.raises(RuntimeError):
        live._bybit_request("POST", "/v5/order/create", {"symbol": "BTCUSDT"})
    with pytest.raises(RuntimeError):
        live._bybit_request("POST", "/v5/order/cancel-all", {"symbol": "BTCUSDT"})


def test_unknown_position_is_not_flat(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(live, "_bybit_request", boom)
    with pytest.raises(live.ExchangeStateUnknown):
        live.get_position("BTCUSDT")
    with pytest.raises(live.ExchangeStateUnknown):
        live.get_account_balance()


def test_filter_optimization_reads_batch_fast_field_names():
    rows = [{"symbol": "A", "sharpe": 1, "win_rate": 50, "max_dd": 40, "trades": 30, "return_pct": 5, "profit_factor": 1.2},
            {"symbol": "B", "sharpe": 1, "win_rate": 50, "max_dd": 10, "trades": 30, "return_pct": 5, "profit_factor": 1.2}]
    kept = apply_filter(rows, {"dd_max": 20})
    assert [r["symbol"] for r in kept] == ["B"]     # drawdown filter really filters
    assert evaluate_filter(rows, kept)["avg_dd"] == 10
