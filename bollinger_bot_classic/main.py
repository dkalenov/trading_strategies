"""CLI entrypoint.

Two modes, mirroring algofactory_bot/main.py's structure but split into
explicit subcommands for clarity:

    python main.py backtest --csv <path> --symbol BTCUSDT
    python main.py backtest --csv <path> --all-symbols
    python main.py run --mode dry_run [--once]

`run` drives the same strategies.bollinger_reversion.BollingerReversionAdapter
that `backtest` replays historically - see backtest/engine.py's module
docstring for why that matters.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


# === backtest subcommand ====================================================

def cmd_backtest(args: argparse.Namespace) -> None:
    from backtest.data import load_multi_symbol_csv
    from backtest.engine import BacktestConfig, run_backtest
    import db

    frames = load_multi_symbol_csv(args.csv)
    cfg = BacktestConfig(
        initial_capital=args.capital, risk_pct=args.risk_pct, stop_atr_mult=args.stop_atr,
        max_leverage=args.leverage, bb_period=args.bb_period, bb_std=args.bb_std,
    )

    db.init(args.db)
    run_id = f"bt-{uuid.uuid4().hex[:12]}"

    if args.symbol:
        if args.symbol not in frames:
            logger.error("Symbol %s not found in dataset (%d symbols available)", args.symbol, len(frames))
            sys.exit(1)
        symbols = [args.symbol]
    else:
        symbols = sorted(frames.keys())

    all_stats = []
    for symbol in symbols:
        stats, trades = run_backtest(frames[symbol], symbol, cfg)
        if stats.total_trades > 0:
            all_stats.append(stats)
            db.save_backtest_run(
                run_id=run_id, symbol=symbol, strategy_kind="bollinger_reversion",
                params_json=json.dumps({"bb_period": cfg.bb_period, "bb_std": cfg.bb_std,
                                         "stop_atr": cfg.stop_atr_mult, "leverage": cfg.max_leverage}),
                total_trades=stats.total_trades, total_return_pct=stats.total_return_pct,
                sharpe_ratio=stats.sharpe_ratio, win_rate=stats.win_rate,
                max_drawdown_pct=stats.max_drawdown_pct, profit_factor=stats.profit_factor,
                created_at=int(time.time()),
            )

    if len(symbols) == 1:
        s = all_stats[0] if all_stats else None
        if s is None:
            print(f"No trades generated for {symbols[0]} (not enough history for warmup).")
            return
        print(f"\n=== {s.symbol} ===")
        print(f"Trades:        {s.total_trades} (long={s.long_trades}, short={s.short_trades})")
        print(f"Win rate:      {s.win_rate:.1f}%")
        print(f"Total return:  {s.total_return_pct:+.2f}%")
        print(f"Sharpe:        {s.sharpe_ratio:.3f}")
        print(f"Max drawdown:  {s.max_drawdown_pct:.2f}%")
        print(f"Profit factor: {s.profit_factor:.2f}")
        print(f"Avg bars held: {s.avg_bars_held:.1f}")
        print(f"Exits:         stop_loss={s.stop_loss_exits}, middle_band={s.middle_band_exits}")
        print(f"Final capital: {s.final_capital:,.2f} (from {cfg.initial_capital:,.2f})")
    else:
        import numpy as np
        returns = np.array([s.total_return_pct for s in all_stats])
        print(f"\n=== Batch backtest: {len(all_stats)}/{len(symbols)} symbols with trades ===")
        print(f"Mean return:    {returns.mean():+.2f}%")
        print(f"Median return:  {np.median(returns):+.2f}%")
        print(f"Profitable:     {(returns > 0).sum()}/{len(returns)} ({(returns > 0).mean()*100:.1f}%)")
        print(f"Best:           {returns.max():+.2f}%")
        print(f"Worst:          {returns.min():+.2f}%")
        print(f"Total trades:   {sum(s.total_trades for s in all_stats)}")
        print(f"\nSaved per-symbol results to {args.db} (run_id={run_id})")

    if args.out:
        out_path = Path(args.out)
        with open(out_path, "w") as f:
            json.dump([s.__dict__ for s in all_stats], f, indent=2, default=str)
        logger.info("Wrote results to %s", out_path)


# === run subcommand (live / dry_run / testnet) ==============================

async def _run_loop(args: argparse.Namespace) -> None:
    import db
    import health
    import universe
    from config import BotConfig
    from config_loader import seed_from_toml_if_first_run
    from marketdata.bar_builder import BarBuilder
    from marketdata.history import fetch_warmup_history
    from risk import RiskManager
    from strategy_adapter import StrategyContext, build_adapter

    db.init(args.db)
    db.ensure_defaults()
    seed_from_toml_if_first_run()
    if args.mode:
        db.update_config(execution_mode=args.mode)

    conf = db.get_config()
    bot_config = BotConfig.from_config_info(conf)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    health.set_run_identity(run_id, bot_config.execution_mode)
    logger.info("Starting run_id=%s mode=%s strategy=%s config_hash=%s",
                run_id, bot_config.execution_mode, bot_config.strategy_kind, bot_config.config_hash)

    adapter = build_adapter(bot_config)
    warmup = adapter.warmup_bars()
    risk = RiskManager(bot_config)

    if bot_config.execution_mode == "dry_run":
        from execution.adapters import DryRunExecutor
        executor = DryRunExecutor()
        gateway = None
    else:
        from execution.adapters import LiveExecutor
        from gateway import Gateway
        gateway = Gateway.from_env(testnet=(bot_config.execution_mode == "testnet"))
        symbol_infos = await gateway.get_symbol_infos()
        valid_symbols, missing = universe.validate_against_exchange(symbol_infos)
        executor = LiveExecutor(gateway, symbol_infos)

    from execution.position_manager import PositionManager
    pm = PositionManager(executor, risk, run_id)

    symbols = list(universe.UNIVERSE_SYMBOLS)
    bars = BarBuilder(max_bars=warmup + 50)

    if gateway is not None:
        frames = await fetch_warmup_history(gateway, symbols, bot_config.interval, warmup)
        for sym, df in frames.items():
            bars.seed(sym, df)
    else:
        logger.warning("dry_run mode with no historical data source configured - "
                        "waiting for live bars to accumulate (or use `backtest` for historical replay)")

    async def _cycle() -> None:
        from execution.intents import build_sized_intent
        from filters import default_symbol_info
        from models import OrderRole
        from strategies.bollinger_reversion import calculate_atr

        frames_now = bars.frames()
        ready = {s: f for s, f in frames_now.items() if len(f) >= warmup}
        if not ready:
            logger.info("No symbols with enough warmup bars yet (%d needed)", warmup)
            return

        stopped_out = await pm.enforce_stops(ready, adapter)
        if stopped_out:
            logger.info("Stop-loss closed %d position(s) this cycle", stopped_out)

        equity = bot_config.manual_equity
        ctx = StrategyContext(
            equity=equity, current_weights={}, target_weights={}, frames=ready,
            symbol_infos={}, open_positions={s: True for s in pm.open_symbols()},
        )
        drafts = adapter.generate(ctx)
        logger.info("Signal cycle: %d intents from %d ready symbols", len(drafts), len(ready))

        for draft in drafts:
            if draft.role == OrderRole.CLOSE:
                await pm.close_position(draft.symbol, reason=draft.reason)
                continue

            df = ready.get(draft.symbol)
            if df is None:
                continue
            last_close = float(df["close"].iloc[-1])
            highs, lows, closes = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()

            atr_arr = calculate_atr(highs, lows, closes, adapter._atr_period)
            atr = float(atr_arr[-1])
            if atr <= 0 or atr != atr:
                continue

            info = None
            if gateway is not None:
                info = (await gateway.get_symbol_infos()).get(draft.symbol)
            if info is None:
                info = default_symbol_info(draft.symbol, last_close)

            sized = build_sized_intent(
                draft, bot_config, risk, equity=equity, symbol_info=info, atr=atr,
                entry_price=last_close, bar_ts=int(time.time() * 1000), run_id=run_id,
            )
            if sized is None:
                continue
            if hasattr(executor, "set_price"):
                executor.set_price(draft.symbol, last_close)
            await pm.open_position(sized, atr, bot_config.interval)

        health.touch_heartbeat()

    if args.once:
        await _cycle()
        return

    from reconciliation import reconcile_once
    from utils import wait_for_next_candle
    shutdown = asyncio.Event()
    cycle_count = 0
    while not shutdown.is_set():
        await wait_for_next_candle(bot_config.interval, shutdown_event=shutdown)
        if shutdown.is_set():
            break
        await _cycle()
        cycle_count += 1
        if cycle_count % 6 == 0:
            await reconcile_once(pm, run_id)


def cmd_run(args: argparse.Namespace) -> None:
    asyncio.run(_run_loop(args))


# === CLI ====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bollinger_bot")
    sub = p.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="Replay historical klines through the strategy")
    bt.add_argument("--csv", required=True, help="Path to multi-symbol klines CSV")
    bt.add_argument("--symbol", default=None, help="Single symbol (omit for full-universe batch)")
    bt.add_argument("--capital", type=float, default=100_000.0)
    bt.add_argument("--risk-pct", type=float, default=0.01, dest="risk_pct")
    bt.add_argument("--stop-atr", type=float, default=1.5, dest="stop_atr")
    bt.add_argument("--leverage", type=int, default=5)
    bt.add_argument("--bb-period", type=int, default=20, dest="bb_period")
    bt.add_argument("--bb-std", type=float, default=2.0, dest="bb_std")
    bt.add_argument("--db", default="bollinger_bot.db")
    bt.add_argument("--out", default=None, help="Optional JSON output path for per-symbol stats")
    bt.set_defaults(func=cmd_backtest)

    run = sub.add_parser("run", help="Live / dry_run / testnet signal loop")
    run.add_argument("--mode", choices=["dry_run", "testnet", "live"], default=None)
    run.add_argument("--once", action="store_true", help="Run a single signal cycle and exit")
    run.add_argument("--db", default="bollinger_bot.db")
    run.set_defaults(func=cmd_run)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
