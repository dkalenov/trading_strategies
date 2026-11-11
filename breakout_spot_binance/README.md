# Breakout Spot Strategy

Automated trading bot using volume-confirmed price breakout strategy for Binance Spot.

**DISCLAIMER:** This is for educational and research purposes only. Trading cryptocurrencies involves high risk of total loss. The author is not responsible for any financial losses. Do not use with real money without fully understanding the risks.

## What is a Breakout?

A breakout occurs when price moves above a defined resistance level with increased volume, suggesting the start of a new trend. The strategy waits for price to break above the highest high of the last N bars, confirmed by a volume spike.

## How the Strategy Works

The bot combines two conditions for entry:

1. **Price breakout**: `close > max(highs[-lookback:])` - price closes above the lookback-period high
2. **Volume confirmation**: `volume >= avg_volume × volume_multiplier` - volume is significantly above average

Exit is managed by two mechanisms:

- **Trailing stop**: ratchets up with price, exits when `close < peak × (1 - trailing_stop_pct%)`
- **ATR stop loss**: initial stop placed at `entry_price - ATR × sl_mult`

### Signal Logic

```
BUY条件: close > resistance AND volume >= avg_volume × volume_mult AND volume >= min_volume_usdt
SELL条件: close < peak_price × (1 - trailing_stop_pct / 100)
```

### Breakout vs Other Strategies

| Feature | Breakout | EMA Crossover | Grid Trading |
|---------|----------|---------------|--------------|
| Entry trigger | Price + Volume | Moving average cross | Pre-set price levels |
| Best market | Trending (breakout) | Trending | Sideways/ranging |
| Noise sensitivity | Low (volume filter) | High (EMA lag) | None (systematic) |
| Win rate | Low (~29%) | Low (~25-40%) | High (~60-70%) |
| Avg win vs loss | Large wins, small losses | Variable | Small, frequent |
| Drawdown | Moderate (29%) | High (40%+) | Low-Moderate |

### Breakout Periods

| Lookback | Use Case | Characteristics |
|----------|----------|-----------------|
| 10-15 | Aggressive | More signals, higher false breakout rate |
| 20 | Balanced | Default — good signal quality |
| 25-30 | Conservative | Fewer signals, higher conviction |
| 40+ | Swing trading | Very few signals, major breakouts only |

### Volume Multiplier

| Multiplier | Effect |
|------------|--------|
| 1.5 | More signals, includes moderate volume spikes |
| 2.0 | Default — balances sensitivity and noise |
| 2.5-3.0 | Only strong volume confirmations |

## Files

| File | Description |
|------|-------------|
| `strategy.py` | BreakoutCore class - signal logic + trailing stop |
| `live_spot.py` | Live bot - WebSocket (mainnet) / REST polling (testnet) |
| `backtester.py` | Event-driven backtester with commission + slippage |
| `main.py` | CLI: backtest, optimize, monthly analysis |
| `config.py` | Config loader (config.ini + env vars) |
| `utils.py` | Binance kline download, ATR calculation, CSV export |
| `db.py` | SQLite storage for backtest/optimization results |
| `config.ini` | Default parameters |
| `run_testnet.ps1` | Testnet launcher script (PowerShell) |

## What is Implemented

### strategy.py — Core Logic:

- `BreakoutCore` class — stateful signal generation for live and backtest
- `breakout_signal()` — array-based function for batch backtesting
- Lookback resistance calculation (highest high over N bars)
- Volume spike detection (current vs average × multiplier)
- ATR trailing stop with configurable percentage
- Signal enum: BUY / SELL / HOLD

### live_spot.py — Live Bot:

- WebSocket real-time data (mainnet) with auto-reconnect
- REST polling fallback for testnet (WebSocket not supported)
- Market buy order with proper lot sizing and tick formatting
- STOP_LOSS_LIMIT order placement
- Position state persistence (`position_state.json`) — survives restarts
- Graceful shutdown — cancels orders, closes position
- Debug mode — forces trade every candle for testing
- Trade logging to CSV

### backtester.py — Backtester:

- Event-driven simulation with realistic costs
- Commission: 0.1% taker (Binance spot)
- Slippage: 0.05% on entry and exit
- Position sizing: risk-based (2% risk per trade)
- Trailing stop checked intra-bar (low vs trailing level)
- ATR-based stop loss
- Full stats: Sharpe, Sortino, Calmar, drawdown, win rate, profit factor

### main.py:

- Backtest with any parameter combination
- Monthly performance breakdown
- Hyperparameter optimization (grid search)
- Results saved to SQLite database
- Trade export to CSV

## What is NOT Implemented

- No multi-symbol support (single pair only)
- No take-profit (relies on trailing stop for exits)
- No short selling (spot market only)
- No Telegram notifications
- No regime or trend filter
- No time-of-day filter
- No position sizing beyond fixed risk fraction

## Backtest Results

BTCUSDT 4h, 2023-01 to 2025-06, $10,000 initial capital:

| Metric | Value |
|--------|-------|
| Total Return | +32.85% |
| Max Drawdown | 29.68% |
| Sharpe Ratio | 0.694 |
| Sortino Ratio | 0.442 |
| Calmar Ratio | 1.107 |
| Win Rate | 28.9% (24W / 59L) |
| Profit Factor | 1.24 |
| Total Trades | 83 |
| Avg Win | +5.91% |
| Avg Loss | -1.47% |
| Avg Duration | 44.0h |
| Max Consec Wins | 3 |
| Max Consec Losses | 12 |
| Total Commission | $2,219 |
| Total Slippage | $1,110 |

## Analysis

Стратегия показывает положительный результат (+32.85%) на 4h таймфрейме BTCUSDT за 2.5 года. Ключевые наблюдения:

**Сильные стороны:**
- Положительное математическое ожидание (+$39.69/сделка)
- Trailing stop эффективно фиксирует прибыль на трендовых участках
- Volume filter снижает количество ложных сигналов
- Низкая корреляция с buy & hold (разные точки входа)

**Слабые стороны:**
- Низкий win rate (28.9%) —requires дисциплина при серии убытков
- Максимальная серия убытков: 12 сделок подряд
- Высокие комиссии ($2,219) при частых входах/выходах
- Max drawdown 29.68% — significant

**Почему стратегия работает на 4h:**
- На 4h меньше рыночного шума compared to 1h/15m
- Volume filter более эффективен на старших таймфреймах
- Тренды на 4h более выражены и длятся дольше

## Configurable Parameters

### Strategy Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `lookback` | 20 | Bars to measure resistance — higher = fewer signals |
| `volume_multiplier` | 2.0 | Volume must be ≥ this × average — higher = stricter |
| `atr_period` | 14 | ATR lookback for stop loss calculation |
| `stop_loss_multiplier` | 1.5 | Stop loss = entry - ATR × this value |
| `trailing_stop_pct` | 3.0 | Trailing stop distance in % from peak |
| `min_volume_usdt` | 1,000,000 | Minimum quote volume to consider signal |

### Backtest Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `initial_capital` | $10,000 | Starting capital |
| `commission` | 0.001 | Commission rate (0.1% = Binance taker) |
| `slippage` | 0.0005 | Slippage rate (0.05%) |
| `risk_pct` | 0.02 | Risk per trade (2% of capital) |

### Exchange Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `symbol` | BTCUSDT | Trading pair |
| `interval` | 4h | Candle interval |
| `start_date` | 2023-01 | Backtest start |
| `end_date` | 2025-06 | Backtest end |
| `budget` | $20 | USDT per trade (live mode) |

All parameters can be set via `config.ini`, command-line arguments, or environment variables (`BREAKOUT_STRATEGY_LOOKBACK`, etc.).

## Installation

```bash
# Clone repository
git clone https://github.com/dkalenov/trading-bots-and-strategies.git
cd trading-bots-and-strategies/breakout_spot_binance

# Install dependencies
pip install -r requirements.txt
```

Requirements: `pandas`, `numpy`, `requests`, `websocket-client`, `python-dateutil`, `pytz`

## Running

```bash
# Backtest with default params
python main.py

# Backtest with custom params
python main.py --lookback 25 --volume-mult 2.5 --sl-mult 2.0

# Monthly breakdown
python main.py --monthly

# Optimize parameters
python main.py --optimize --max-evals 50

# Export trades to CSV
python main.py --export my_trades.csv

# Live bot — testnet (debug mode)
$env:BINANCE_API_KEY = "your_testnet_key"
$env:BINANCE_API_SECRET = "your_testnet_secret"
python live_spot.py --testnet --debug

# Live bot — testnet via launcher
.\run_testnet.ps1

# Live bot — mainnet (real money!)
$env:BINANCE_API_KEY = "your_mainnet_key"
$env:BINANCE_API_SECRET = "your_mainnet_secret"
python live_spot.py --live
```

## Next Steps: Building a Production Bot

If you want to build a production bot based on this strategy:

1. **Add Multi-Symbol Support**
   - Run breakout on multiple pairs simultaneously
   - Portfolio-level risk management
   - Capital allocation across symbols

2. **Add Filters**
   - Trend regime filter (EMA50/EMA200 alignment)
   - Volatility filter (ATR minimum)
   - Time-of-day filter (avoid low-liquidity hours)
   - Market cap / liquidity filter

3. **Optimize Parameters**
   - Walk-forward optimization
   - Test on multiple symbols and timeframes
   - Monte Carlo simulation for robustness
   - Out-of-sample validation

4. **Add Infrastructure**
   - Telegram notifications for trades
   - Database logging (PostgreSQL)
   - Health monitoring and alerts
   - Graceful shutdown with position recovery

5. **Consider Using Algofactory Framework**
   The parent repository includes `algofactory_bot/` — a production-ready framework with:
   - ATR-based position sizing
   - 2 take-profit levels with breakeven stop
   - Trailing stop
   - Exchange gateway (Binance)
   - Database persistence
   - Telegram integration

   See `algofactory_bot/strategies/` for adapter examples.

## Contacts

Telegram: @KDR_98
LinkedIn: dmitrii-kalenov
Email: drkalenov@gmail.com
