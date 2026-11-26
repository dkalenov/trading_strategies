# Candle Pattern Strategy - Bybit Futures

Candlestick pattern recognition bot with ATR-based risk management for Bybit Futures.

**Disclaimer:** Educational purposes only. Crypto trading carries high risk of total loss. Read the "Known Limitations" section before considering real capital.

## How It Works

- Detects 11 candlestick patterns: Engulfing, Hammer, Morning/Evening Star, Three White Soldiers/Black Crows, Piercing Line, Dark Cloud Cover, Harami Cross (Harami Cross is detected/labeled but is intentionally non-directional in the current code - see Pattern Weights below - so it never triggers a trade)
- Patterns are scored by weighted strength (0.9–1.4) with optional volume boost
- **BUY signal:** Bullish pattern detected (engulfing, hammer, morning star, etc.)
- **SELL signal:** Bearish pattern detected (bearish engulfing, evening star, etc.)
- ATR-based stop-loss and take-profit, enforced on the exchange via Bybit's trading-stop endpoint (see Architecture)
- **Trend filter:** EMA-50/EMA-200, only trade with the trend. See the ablation table below: without it every tested combination loses most of the account.

## Files

| File | Purpose |
|------|---------|
| `strategy.py` | Pattern detection + signal generation (11 patterns) |
| `backtest.py` | Backtester with Bybit data download + caching |
| `main.py` | Live trading bot (WebSocket + REST, Bybit Futures) |
| `validate.py` | Out-of-sample / cross-timeframe robustness checks |
| `Pattern_strategy.ipynb` | Research notebook (Colab) |
| `ML_Trading_Bot.ipynb` | ML-based pattern recognition (exploratory - not used by main.py/backtest.py) |
| `requirements.txt` | Python dependencies |

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Backtest (optimized config with trend filter)
python backtest.py --symbol BTCUSDT --interval 1h --start 2024-01 --end 2025-06

# Backtest baseline (no filters)
python backtest.py --symbol BTCUSDT --interval 1h --start 2024-01 --end 2025-06 --baseline

# Robustness / out-of-sample check
python validate.py --symbol BTCUSDT

# Live bot (dry run)
python main.py --symbol BTCUSDT --interval 1h

# Live bot (testnet)
export BYBIT_TESTNET_API_KEY=your_key
export BYBIT_TESTNET_API_SECRET=your_secret
python main.py --symbol BTCUSDT --interval 1h --testnet --live --debug
```

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `sl_atr` | 2.0 | Stop loss = N x ATR |
| `tp_atr` | 4.0 | Take profit = N x ATR |
| `atr_period` | 14 | ATR calculation period |
| `min_body_atr` | 0.15 | Min candle body size (ATR fraction) |
| `risk_pct` | 1% | Risk per trade |
| `leverage` | 10x | Max leverage cap. Position size is risk-based (from `risk_pct`), so this cap rarely binds in practice - it doesn't meaningfully change backtest results whether set to 10x or 20x |
| `commission` | 0.055% | Bybit standard (non-VIP) USDT-perpetual taker fee. All orders here are market orders (taker) |
| `min_strength` | 1.3 | Min pattern strength to trade - see the ablation table for its effect |
| `ema_fast` | 50 | Fast EMA for trend filter (calculated with the standard recursive formula, `adjust=False`) |
| `ema_slow` | 200 | Slow EMA for trend filter |
| `min_atr_pct` | 0.3% | Min ATR % of price (volatility filter) |

## Architecture

```
WebSocket (kline)  ->  Pattern Detection (11 patterns)
                              v
                     Signal + Strength scoring
                              v
                     Market Order  ->  Bybit trading-stop endpoint
                                       (SL + TP registered on the exchange
                                        immediately after entry fills)
```

Both the SL and TP levels are computed the same way in `backtest.py` and `main.py`
(`entry_price ± sl_atr/tp_atr * ATR`), and in live trading they are pushed to Bybit
via `/v5/position/trading-stop` right after the entry order - the exchange enforces
them even if this process disconnects. Previously this endpoint was never called;
the bot only closed positions on the next opposite pattern signal, with no
protective stop of any kind. Since ~99.7% of trades in the backtest close via SL/TP
(see below), that gap meant the live bot's actual behavior had essentially nothing
in common with what was backtested. If you forked this repo before this fix, update
`main.py`.

`--leverage` is now also actually sent to Bybit via `/v5/position/set-leverage` at
startup - previously it was only printed to the console and never applied, so the
account silently kept whatever leverage was last configured manually.

## Pattern Weights

| Pattern | Weight | Direction |
|---------|--------|-----------|
| Three White Soldiers | 1.4 | Bull |
| Three Black Crows | 1.4 | Bear |
| Morning Star | 1.3 | Bull |
| Evening Star | 1.3 | Bear |
| Bullish Engulfing | 1.2 | Bull |
| Bearish Engulfing | 1.2 | Bear |
| Piercing Line | 1.0 | Bull |
| Dark Cloud Cover | 1.0 | Bear |
| Hammer | 0.9 | Bull |
| Inverted Hammer | 0.9 | Bear |
| Harami Cross | 0.8 | *(none: never trades, see note above)* |

## Backtest Results

BTCUSDT 1h (2024-01 to 2025-06, 13,000 candles). Costs: Bybit's standard 0.055% taker fee, 0.02% slippage
per fill, and an approximate funding cost (0.01% per 8h, charged for each real funding settlement at
00:00, 08:00 and 16:00 UTC that a position is open; this is a rough constant, not historical funding data).
Entries are filled at the next bar's open. Reproduce with the commands in Quick Start.

### Without filters (baseline)

| Metric | Value |
|--------|-------|
| Return | -99.0% |
| Trades | 1,331 |
| Win Rate | 40.9% |
| Profit Factor | 0.48 |

Pure candlestick patterns with no filters are not profitable: the ATR stop and target are close to the
noise level, so fees and slippage dominate.

### With filters (current defaults)

| Metric | Value |
|--------|-------|
| Return | **+17.84%** |
| Trades | 329 |
| Win Rate | 39.2% |
| Max Drawdown | -17.9% |
| Sharpe Ratio | 0.62 |
| Profit Factor | 1.07 |
| Est. funding cost | -$655 (already included) |

Config: SL 2.0x ATR, TP 4.0x ATR, EMA-50/200 trend filter, `min_strength=1.3`, `min_atr_pct=0.3`.
Buy and hold BTC over the same period is not shown here; this strategy is in the market only part of
the time, so compare it with holding before drawing conclusions.

### Filter ablation

Each filter is an independent switch (`--no-trend-filter` turns off only the EMA trend filter,
`--min-strength 0` only the strength filter, `--min-atr-pct 0` only the ATR% filter). SL/TP fixed at 2.0/4.0 ATR:

| Filters active | Return | Trades | Win Rate | Profit Factor |
|---|---|---|---|---|
| None (baseline) | -99.0% | 1,331 | 40.9% | 0.48 |
| Trend only | -6.5% | 362 | 36.7% | 0.98 |
| Strength only | -84.2% | 1,408 | 31.9% | 0.74 |
| ATR% only | -95.9% | 2,756 | 30.1% | 0.70 |
| Strength + ATR%, no trend | -79.0% | 1,359 | 32.4% | 0.78 |
| Trend + strength | +11.8% | 338 | 38.8% | 1.05 |
| Trend + ATR% | -3.7% | 355 | 36.9% | 0.99 |
| All three (default) | +17.8% | 329 | 39.2% | 1.07 |

**The EMA trend filter is essential in this test.** Without it every combination loses most of the
account; with it the results are around break-even, and the strength and ATR% filters then add roughly
10 to 20 percentage points on top. An earlier version of this README concluded the opposite ("the trend
filter is not the main driver"). That table was wrong: `--no-trend-filter` did not actually turn the trend
filter off whenever the strength or ATR% filter was active, so the "no trend" rows still had it on.

### Robustness check (`validate.py`)

All parameters were chosen and evaluated on the same single 18-month dataset: no train/test split, no
walk-forward, no second symbol.

| Test | Return | Win Rate | PF |
|---|---|---|---|
| 1h, first half of the period | +11.7% | 39.5% | 1.10 |
| 1h, second half of the period (about 2024-10 to 2025-06) | +5.8% | 39.0% | 1.04 |
| 30m, 2024-09 to 2025-06 (same asset, a timeframe the parameters were not fit to) | **-38.7%** | 33.4% | 0.81 |

The 1h second half covers roughly the same months as the 30m run, so the comparison is fair in time:
the same parameters that are slightly positive on 1h lose heavily on 30m. That is evidence the
parameters are tied to this dataset and timeframe rather than a robust edge. Before risking real capital,
test on a period and a symbol that were not used to choose `sl_atr`, `tp_atr` and `min_strength`.

### Key findings

1. The EMA trend filter is what separates a break-even system from one that loses almost everything here.
2. Strength and ATR% filters improve a filtered system modestly; on their own they do not rescue it.
3. Engulfing patterns dominate the raw signal counts and have the weakest edge.
4. Realistic costs matter a lot: earlier versions quoted +34.2% with a 0.04% fee, no funding and same-bar
   entries.
5. The same parameters lose money on 30m bars, so treat the strategy as unvalidated out of sample.

## Live bot behavior

`main.py` acts on closed candles only (the candle the WebSocket confirmed). An opposite signal closes the
position and reverses it, as in the backtest; a signal in the direction of the open position is ignored.
Position size is risk-based and capped at balance x leverage. After entry the bot confirms the position on
the exchange and sets the exchange-side SL/TP, retrying, and closes the position if it cannot attach them.
An unreadable position or balance skips the candle instead of being treated as "no position". The live
path has not been exercised against a real exchange in this repository.

## Known Limitations

- **Entry timing:** the backtest fills at the *next* bar's open following a signal bar's close (`next_bar_entry=True`, default), not the signal bar's own close - see the note under "With filters" above. `main.py`'s real execution (WebSocket confirm → REST poll → order) happens shortly after a candle closes, so it lines up with this convention; there is still some real network/processing latency backtest can't capture exactly.
- **Funding rate is a rough constant estimate** (0.01%/8h), not real historical funding data. Actual BTCUSDT funding varies over time and tends to run positive during strong uptrends - which is exactly when this trend-following strategy is more likely to be long, a correlation that could make real funding costs worse than the flat estimate here.
- **No liquidation modeling.** Position sizing is risk-based (`risk_pct` of capital per trade via SL distance), which in practice keeps notional well under the leverage cap, so liquidation before SL is unlikely at the tested parameters - but it isn't explicitly checked.
- **Single asset, single timeframe, single period were used to choose parameters and report results.** See the out-of-sample section above.
- **No slippage differentiation for stop-outs.** Real stop-loss fills during fast moves/flash-crashes often slip more than the flat 0.02% assumed here for every exit.

## Contacts

Telegram: @KDR_98
