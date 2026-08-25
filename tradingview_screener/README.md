# TradingView Screener Bot (Binance USD-M Futures)

A trend-following bot for Binance USD-M Futures built around TradingView's
technical rating. It watches a symbol list, waits for TradingView to say
STRONG_BUY or STRONG_SELL, filters that against what TradingView says about
BTC, and manages the resulting trade with an ATR-based stop, a partial
take-profit, and a move to breakeven.

**Disclaimer:** educational project. Not financial advice. Crypto
derivatives can lose you all of your margin quickly.

## How it works

Entry, on every candle close for a symbol on your watchlist:
- fetch TradingView's technical rating for the symbol
- fetch TradingView's rating for BTCUSDT as a regime filter
- go long if the symbol is STRONG_BUY and BTC is STRONG_BUY, BUY, or NEUTRAL
- go short if the symbol is STRONG_SELL and BTC is STRONG_SELL, SELL, or NEUTRAL
- one open position per symbol at a time; a new signal is ignored while a
  trade is already open

Exit, once a position is open (monitored via WebSocket):
- stop-loss at `entry -/+ ATR * stop_multiplier`
- take1 at `entry +/- ATR * take1_multiplier` - closes a portion of the
  position and moves the stop for the rest to breakeven
- take2 at `entry +/- ATR * take2_multiplier` - closes what's left
- if the stop is hit before take1, the whole position exits there, at a loss

## Project structure

| Path | Purpose |
|---|---|
| `trading_bot/main.py` | async entry point: signal collection, trade execution, WebSocket monitoring |
| `trading_bot/config.py` | configuration constants (DB, Telegram) |
| `trading_bot/config_loader.py` | reads config.ini |
| `trading_bot/db.py` | PostgreSQL models and queries (Trades, Orders, Config, SymbolsSettings) |
| `trading_bot/exchange.py` | Binance Futures REST client |
| `trading_bot/get_data.py` | TradingView rating fetcher, symbol loader |
| `trading_bot/models.py` | shared dataclasses |
| `trading_bot/risk.py` | ATR calculation, position sizing, exit levels |
| `trading_bot/signals.py` | signal processing logic |
| `trading_bot/state.py` | in-memory trade state |
| `trading_bot/trader.py` | trade orchestration (entry + exit) |
| `trading_bot/utils.py` | rounding helpers, candle timing |
| `trading_bot/health.py` | connectivity checks |
| `trading_bot/tg.py` | Telegram notifications |
| `trading_bot/compare_indicators.py` | indicator comparison experiments |
| `trading_bot/indicator_test.py` | indicator testing |
| `execution/order_manager.py` | order placement/cancellation |
| `execution/position_manager.py` | position lifecycle management |
| `execution/protection.py` | take1 -> breakeven transition |
| `strategies/` | strategy interface and implementations |
| `core/` | pure strategy math (entry rule, exit levels) |
| `exchange/` | low-level Binance client |
| `backtest/` | backtest engine |
| `indicator/` | TradingView indicator reproduction experiments |
| `data/` | signal logs and symbol lists |
| `results/` | backtest output |
| `tests/` | unit tests |

## Setup

```bash
pip install -r requirements.txt
```

Copy `config.ini.example` to `config.ini` and fill in:
- Binance API key/secret
- PostgreSQL connection details
- Telegram bot token and channel

```ini
[BOT]
api_key = your-binance-key
api_secret = your-binance-secret
testnet = True

[DB]
host = localhost
port = 5432
db = your_db
user = your_user
password = your_password

[TG]
token = your-telegram-token
channel = your-channel-id
```

## Usage

**Run the bot** (paper trading by default, set `trade_mode` in DB to enable):

```bash
python trading_bot/main.py
```

**Run with live trading:**

Set `trade_mode = 1` in the database config table.

**Backtest:**

```bash
python backtest/run_backtest.py --klines path/to/klines.csv --signals data/tradingview_signals.csv
```

**Tests:**

```bash
python -m pytest tests/ -v
```

## Strategy parameters

Per-symbol settings stored in the `SymbolsSettings` table:
- `atr_length` - ATR period
- `stop` - stop-loss multiplier (e.g. 0.45)
- `take1` - first take-profit multiplier (e.g. 2.5)
- `take2` - second take-profit multiplier (e.g. 5.0)
- `order_size` - fixed dollar amount per trade
- `leverage` - position leverage

## Telegram notifications

The bot sends alerts to your configured channel on:
- Position opened (entry price, stop, take levels)
- Take1 hit (partial close, stop moved to breakeven)
- Position closed (reason: stop/take2/breakeven, P&L)

## Notes

- TradingView has rate limits. With 30 threads you can process ~314 symbols.
- The bot uses WebSocket streams for real-time price monitoring (not polling).
- In flat markets the strategy may drag on or catch stops frequently.
- Optimizing stop/take coefficients per symbol can improve results.

## License

MIT. See `LICENSE`.
