#!/usr/bin/env python3
"""
Entrypoint for the live/paper trading bot. Mirrors main.py in
algofactory_bot: wires config, db, exchange client, risk, strategy and
execution together and runs the cycle. Async end to end on the I/O side
(exchange calls); the cointegration screen itself is CPU-bound numpy/
statsmodels work, so it runs in a thread pool executor rather than
blocking the event loop while it churns through ~1770 pairs.

Defaults to DRY_RUN=true and USE_TESTNET=true, so `python bot/main.py`
with zero configuration fetches real market data from Binance and logs
what it *would* trade, without ever sending an order or needing API keys.

IMPORTANT: this strategy backtested at a loss under its original default
parameters (see results/, README). A parameter sweep found that being
far more selective (z_entry=4.0, z_stop=5-6) came out close to breakeven
over the same period, but that was found by searching after the fact on
the one period tested, not validated out of sample. Do not set
DRY_RUN=false with real API keys without reading the README first.

Usage:
    python bot/main.py --once          # one cycle, then exit (good for cron)
    python bot/main.py --loop          # run forever, sleeping between bars
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from bot.config import BotConfig
from bot.db import Db
from bot.exchange import BinanceFuturesClient
from bot.risk import RiskManager
from bot.strategy import rescan_pairs, current_zscore, size_pair
from bot.execution.order_manager import OrderManager
from bot.execution.position_manager import PositionManager, pair_key
from bot.utils import setup_logging
from coint_pairs.data import select_universe


TIMEFRAME_TO_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


async def get_universe(client: BinanceFuturesClient, cfg: BotConfig, logger) -> list[str]:
    """Picks the tradable universe by 24h quote volume from the exchange's
    own ticker data (no historical CSV needed for live trading - the
    backtest's CSV-based universe selection is research-only).
    """
    markets = await client.load_markets()
    perp_usdt = [
        m["id"].replace("/", "").replace(":USDT", "")
        for m in markets.values()
        if m.get("swap") and m.get("quote") == "USDT" and m.get("active")
    ]
    tickers = await client.exchange.fetch_tickers([client._to_ccxt_symbol(s) for s in perp_usdt])
    vols = {}
    for sym in perp_usdt:
        t = tickers.get(client._to_ccxt_symbol(sym))
        if t and t.get("quoteVolume"):
            vols[sym] = t["quoteVolume"]
    ranked = sorted(vols, key=vols.get, reverse=True)
    universe = ranked[: cfg.universe_size]
    if "BTCUSDT" not in universe:
        universe.append("BTCUSDT")
    logger.info(f"Universe: {len(universe)} symbols, top 10 by volume: {ranked[:10]}")
    return universe


async def run_cycle(cfg: BotConfig, client: BinanceFuturesClient, db: Db,
                     risk: RiskManager, position_mgr: PositionManager, logger) -> None:
    universe = await get_universe(client, cfg, logger)
    limit = cfg.window + 5
    prices = await client.fetch_price_matrix(universe, cfg.timeframe, limit)
    if len(prices) < cfg.window + 1:
        logger.warning(f"Not enough bars ({len(prices)} < {cfg.window + 1}), skipping cycle")
        return
    bar_time = prices.index[-1]
    logger.info(f"Cycle at {bar_time}, {len(prices)} bars, {prices.shape[1]} symbols with full data")

    # the cointegration screen is CPU-bound (numpy/statsmodels over ~n^2/2
    # pairs) - run it off the event loop so a slow screen doesn't stall
    # anything else the bot might be doing (e.g. a concurrent close).
    loop = asyncio.get_running_loop()
    eligible = await loop.run_in_executor(None, rescan_pairs, prices, cfg)
    logger.info(f"{len(eligible)} pairs pass the cointegration/half-life/beta screen")

    open_positions = db.get_open_positions()

    # ---- manage exits ----
    for key, pos in list(open_positions.items()):
        a, b = pos["leg1"], pos["leg2"]
        if a not in prices.columns or b not in prices.columns:
            continue
        price1 = float(prices[a].iloc[-1])
        price2 = float(prices[b].iloc[-1])
        z = current_zscore(prices, a, b, pos["hedge"], cfg.window)
        held_bars = (bar_time - pd.Timestamp(pos["entry_bar_time"])) / pd.Timedelta(
            minutes=TIMEFRAME_TO_MINUTES.get(cfg.timeframe, 240)
        )
        unrealized = pos["qty1"] * (price1 - pos["entry_price1"]) + pos["qty2"] * (price2 - pos["entry_price2"])

        exit_reason = ""
        # same priority as backtest.py: price-driven outcomes before the
        # relationship-based screen_failed check, see the comment there
        if not pd.isna(z) and abs(z) <= cfg.z_exit:
            exit_reason = "target"
        elif not pd.isna(z) and abs(z) >= cfg.z_stop:
            exit_reason = "stop_divergence"
        elif unrealized <= -cfg.max_loss_per_pair_pct * cfg.capital_usd:
            exit_reason = "stop_loss"
        elif held_bars >= cfg.max_holding_bars:
            exit_reason = "max_holding"
        elif (a, b) not in eligible and (b, a) not in eligible:
            exit_reason = "screen_failed"

        if exit_reason:
            net_pnl = await position_mgr.close_position(pos, price1, price2, z, exit_reason, bar_time)
            risk.record_realized_pnl(net_pnl)

    # ---- manage entries ----
    if not risk.trading_allowed():
        return

    open_positions = db.get_open_positions()
    gross_exposure = sum(abs(p["qty1"]) * float(prices[p["leg1"]].iloc[-1])
                          + abs(p["qty2"]) * float(prices[p["leg2"]].iloc[-1])
                          for p in open_positions.values() if p["leg1"] in prices.columns)

    for (a, b), info in eligible.items():
        if not risk.has_capacity(open_positions, gross_exposure):
            break
        if pair_key(a, b) in open_positions:
            continue
        z = current_zscore(prices, a, b, info["hedge"], cfg.window)
        if pd.isna(z) or abs(z) < cfg.z_entry:
            continue
        direction = -1 if z >= cfg.z_entry else 1
        qty1, qty2 = size_pair(prices, a, b, info["hedge"], cfg)
        if qty1 == 0.0 and qty2 == 0.0:
            continue
        price1 = float(prices[a].iloc[-1])
        price2 = float(prices[b].iloc[-1])
        await position_mgr.open_position(a, b, direction, info["hedge"], qty1, qty2, price1, price2, z, bar_time)
        open_positions = db.get_open_positions()
        gross_exposure += abs(qty1) * price1 + abs(qty2) * price2


async def main_async(args) -> None:
    cfg = BotConfig()
    cfg.validate_for_live_trading()
    logger = setup_logging(cfg.log_level)
    logger.info(f"Starting bot: dry_run={cfg.dry_run} testnet={cfg.use_testnet} "
                f"capital=${cfg.capital_usd:,.0f}")
    if not cfg.dry_run:
        logger.warning(
            "DRY_RUN=false: this bot will send REAL orders. The strategy backtest "
            "in results/ showed a net loss under its default parameters. Make sure "
            "you've read the README and know what you're doing."
        )

    db = Db(cfg.db_path)
    client = BinanceFuturesClient(cfg)
    order_mgr = OrderManager(client, cfg.dry_run, logger)
    position_mgr = PositionManager(cfg, db, order_mgr, logger)
    risk = RiskManager(cfg, db, logger)

    try:
        if args.loop:
            interval_sec = TIMEFRAME_TO_MINUTES.get(cfg.timeframe, 240) * 60
            while True:
                try:
                    await run_cycle(cfg, client, db, risk, position_mgr, logger)
                except Exception:
                    logger.exception("Cycle failed, will retry next interval")
                await asyncio.sleep(interval_sec)
        else:
            await run_cycle(cfg, client, db, risk, position_mgr, logger)
    finally:
        await client.close()
        db.close()


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run a single cycle and exit")
    mode.add_argument("--loop", action="store_true", help="run forever, sleeping between bars")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
