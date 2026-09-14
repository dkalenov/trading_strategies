# bollinger_bot

Bollinger Bands mean reversion for Binance USD-M Futures.
Shared signal core between backtest and live — no divergence on entry/exit timing.

## Strategy

- Bands: `SMA(20) +/- 2 * std`
- Long entry: close crosses at/below lower band
- Short entry: close crosses at/above upper band
- Exit: close crosses back through middle band (recalculated each bar), or stop-loss
- Stop-loss: `entry +/- 1.5 * ATR(14)`, sized to ~1% risk per trade
- Regime filter (EMA20/EMA50) available but off by default

Signal core is in `strategies/bollinger_reversion.py` (`BollingerReversionCore`).
Both the live adapter and backtest engine call the same code.

## Backtest results

4h bars, 328 symbols, 2024-05-23 to 2025-10-23.

BTCUSDT (147 trades):

```
Win rate:      45.6%
Total return:  -9.67%
Sharpe:        -0.556
Max drawdown:  15.32%
Profit factor: 0.88
```

Full universe, no cherry-picking:

```
Mean return:   -11.36%
Median return: -11.39%
Profitable:    44 / 328 (13.4%)
Best:          +21.25%
Worst:         -38.85%
Total trades:  42349
```

Mean reversion buys weakness and sells strength — wrong side of a trend.
Most symbols lose. The top symbol is noise from scanning 328 at once.

## Quick start

```bash
pip install -r requirements.txt
python main.py backtest --csv <path.csv> --symbol BTCUSDT
python main.py backtest --csv <path.csv>
python -m pytest tests/ -v
```

## Layout

```
__main__.py      python -m bollinger_bot entry point
main.py          CLI: backtest + run subcommands
config.py        BotConfig frozen dataclass, DB-backed
models.py        enums + dataclasses (OrderRole, OrderIntentDraft, etc.)
risk.py          ATR position sizing with adaptive multiplier
db.py            SQLite config + run storage (453 lines)
gateway.py       Binance USD-M Futures gateway
universe.py      symbol universe from TOML + exchange validation
health.py        heartbeat file + DB health events
utils.py         floor_to_step, klines_to_dataframe, wait_for_next_candle,
                 run_parallel, fmt_price/qty
errors.py        exception classes
filters.py       exchange filter parsing (tick_size, step_size, notional)
strategy_adapter.py  IntentGenerator protocol, registry dispatch
config_loader.py     TOML config loader + DB seed
reconciliation.py    stop coverage + untracked position checks
binance/         vendored REST client (futures)
marketdata/      bar_builder, history (warmup fetch), websocket (kline stream)
execution/       adapters (DryRun/Live), position_manager, protection,
                 fills, intents, order_manager
strategies/      base.py, registry.py, bollinger_reversion.py
backtest/        data.py (CSV loader), engine.py (bar-by-bar replay)
deploy/          bot_config.toml
tests/           8 test files
```

## Differences from algofactory_bot reference

- SQLite instead of PostgreSQL
- `requests` instead of `aiohttp`
- Position manager: open/protect/close, no TP1 partial-close or breakeven move
- Dry_run enforces stops each cycle (no exchange behind it)
- Reconciliation: stop coverage + untracked positions (not full orphan sweep)
- Live/testnet exchange calls untested here, but signal → risk → position_manager
  path was run end to end with historical data + simulated fills, both directions verified

## Config

`deploy/bot_config.toml` — universe, taxonomy, strategy/risk/exit/run params.
Seeds DB on first run, then hot-reloadable from DB.
Quick experiments via CLI: `--bb-period`, `--bb-std`, `--stop-atr`.

## Running live

```bash
cp .env.example .env
python main.py run --mode testnet --once
python main.py run --mode testnet
```
