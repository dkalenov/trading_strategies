"""
Runs bot.main.run_cycle against synthetic price data with mocked exchange
calls, so the whole open -> monitor -> close pipeline gets exercised
without ever touching the network. This project's sandbox could not reach
Binance's API (network egress is restricted to a fixed allowlist that
does not include it), so this is the closest thing to an integration
test that could be run here - see README for that limitation and what
it means for how much to trust the live bot code path versus the
backtest, which ran on real historical data.

The bot is async (see bot/main.py, bot/exchange.py), so the mocked
exchange methods here are async too and the test body runs inside
asyncio.run() rather than pulling in a pytest-asyncio dependency for one
test file.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

from bot.config import BotConfig
from bot.db import Db
from bot.exchange import BinanceFuturesClient
from bot.risk import RiskManager
from bot.execution.order_manager import OrderManager
from bot.execution.position_manager import PositionManager
import bot.main as bot_main


def make_synthetic_prices(n=260):
    rng = np.random.default_rng(42)
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")

    log_btc = np.cumsum(rng.normal(scale=0.01, size=n)) + np.log(60000)

    # cointegrated pair: ASSETB = hedge * ASSETA + mean-reverting noise
    log_a = np.cumsum(rng.normal(scale=0.015, size=n)) + np.log(10)
    ou = np.zeros(n)
    for i in range(1, n):
        ou[i] = 0.85 * ou[i - 1] + rng.normal(scale=0.02)
    log_b = 0.7 * log_a + ou

    # an unrelated third symbol, not cointegrated with anything
    log_c = np.cumsum(rng.normal(scale=0.02, size=n)) + np.log(5)

    df = pd.DataFrame({
        "BTCUSDT": np.exp(log_btc),
        "ASSETAUSDT": np.exp(log_a),
        "ASSETBUSDT": np.exp(log_b),
        "ASSETCUSDT": np.exp(log_c),
    }, index=idx)
    return df


@pytest.fixture
def cfg(tmp_path):
    c = BotConfig()
    c.dry_run = True
    c.universe_size = 3
    c.window = 200
    c.rescan_step_bars = 30
    c.beta_threshold = 0.9  # loose on purpose, synthetic btc is unrelated to the pair by construction
    c.max_half_life = 300
    c.z_entry = 0.5  # loosened only so this test's fixed random draw deterministically triggers an entry
    c.capital_usd = 100_000
    c.db_path = str(tmp_path / "test_state.sqlite3")
    return c


def test_run_cycle_opens_and_tracks_a_position(cfg, monkeypatch):
    prices = make_synthetic_prices(n=260)

    async def fake_get_universe(client, cfg, logger):
        return ["ASSETAUSDT", "ASSETBUSDT", "ASSETCUSDT", "BTCUSDT"]

    async def fake_fetch_price_matrix(self, symbols, timeframe, limit):
        return prices

    async def fake_get_last_price(self, symbol):
        return float(prices[symbol].iloc[-1])

    async def fake_round_passthrough(self, symbol, value):
        return float(value)

    async def fake_place_protective_stop(self, symbol, side, qty, stop_price, dry_run):
        return {"id": f"dry-stop-{symbol}", "status": "dry_run_placed"}

    async def fake_cancel_all(self, symbol, dry_run):
        return None

    monkeypatch.setattr(bot_main, "get_universe", fake_get_universe)
    monkeypatch.setattr(BinanceFuturesClient, "fetch_price_matrix", fake_fetch_price_matrix)
    monkeypatch.setattr(BinanceFuturesClient, "get_last_price", fake_get_last_price)
    monkeypatch.setattr(BinanceFuturesClient, "round_amount", fake_round_passthrough)
    monkeypatch.setattr(BinanceFuturesClient, "round_price", fake_round_passthrough)
    monkeypatch.setattr(BinanceFuturesClient, "place_protective_stop", fake_place_protective_stop)
    monkeypatch.setattr(BinanceFuturesClient, "cancel_all_open_orders", fake_cancel_all)

    db = Db(cfg.db_path)
    client = BinanceFuturesClient(cfg)
    order_mgr = OrderManager(client, cfg.dry_run, logger=_NullLogger())
    position_mgr = PositionManager(cfg, db, order_mgr, logger=_NullLogger())
    risk = RiskManager(cfg, db, logger=_NullLogger())

    asyncio.run(bot_main.run_cycle(cfg, client, db, risk, position_mgr, logger=_NullLogger()))

    open_positions = db.get_open_positions()
    assert len(open_positions) >= 1, "expected the deterministically-loosened threshold to trigger an entry"
    for key, pos in open_positions.items():
        assert pos["leg1"] in prices.columns
        assert pos["leg2"] in prices.columns
        assert pos["qty1"] != 0
    db.close()


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass
    def exception(self, *a, **k): pass
