# UTBot Strategy

ATR trailing-stop trend strategy for Binance USD-M Futures, with a backtester
and a WebSocket live-testnet bot. This version follows a full audit that
found the live bot generated signals a different way than the backtest
tested them, three files each had their own inconsistent position-sizing
formula, and the headline "98 pairs" result in the old README wasn't
reproducible from anything in this repo - see **What changed** below for
the full list and how each was fixed.

**Disclaimer:** Educational project. Not financial advice. Crypto
derivatives carry a high risk of total loss. Test on testnet first.

## How it works

UTBot (a well-known open-source TradingView indicator, "UT Bot Alerts")
builds an ATR-based trailing stop:

- `n_loss = ATR × key_value`
- The stop ratchets up under an uptrend and down under a downtrend, never
  reversing against the trend
- **BUY**: price crosses above the trailing stop from below
- **SELL**: price crosses below the trailing stop from above

`strategy.py`'s `UTBotCore` is a line-by-line port of the Pine Script
indicator (verified against the original logic, including the exact
bar-lag semantics of Pine's `crossover()`/`nz()`). Four ways to trade
that signal are implemented in `backtester.py` / `main.py --strategy`:

| Variant | Exit logic |
|---|---|
| `basic` | Fixed SL/TP (ATR × multiplier), or an opposite UTBot signal |
| `supertrend` | Same, but entries are additionally filtered by a SuperTrend trend confirmation |
| `inner_trend` | Same, but entries are additionally filtered by RSI |
| `take_loss` | Fixed SL/TP/signal exit, **plus** a % trailing stop once the trade is in profit |

Position size is risk-based: `risk_pct` of equity is the dollar amount
you're willing to lose if the stop is hit, from which quantity is derived
and then capped by `leverage`. This is now the **one** formula
(`utils.compute_position_size`), used identically by the backtester and
every live/testnet code path - see **What changed**.

## Project structure

| File | Purpose |
|---|---|
| `strategy.py` | `UTBotCore` (the indicator) + `SuperTrendFilter` / `RSIFilter` |
| `utils.py` | Kline download/cache, Wilder's ATR, the shared position-sizing function |
| `backtester.py` | `Backtester` - all four strategy variants, realistic costs |
| `debug_mode.py` | Verbose trade-by-trade printout — now just a display layer over `Backtester`, not a second implementation |
| `main.py` | CLI: backtest / monthly analysis / parameter optimization, SQLite result storage |
| `db.py` | SQLite schema + read/write helpers for `main.py` |
| `live_testnet.py` | WebSocket bot - real-time execution on Binance Futures Testnet |
| `testnet_once.py` | One-shot: check the current signal and place a single order if there is one |
| `scan_signals.py` | Read-only signal scan across a symbol watchlist and multiple intervals |
| `config.py` / `config.ini` | Central configuration (symbol, strategy params, costs, leverage) |
| `klines/` | Cached kline data (auto-downloaded from Binance Vision on first use) |

## Setup

```bash
pip install -r requirements.txt

export BINANCE_TESTNET_API_KEY="your-testnet-key"
export BINANCE_TESTNET_API_SECRET="your-testnet-secret"
```

Get testnet keys at https://testnet.binancefuture.com.

## Usage

**Backtest** (reads `config.ini`, overridable via flags):

```bash
python main.py --strategy take_loss --key-value 12 --atr-period 14 \
    --start 2021-01 --end 2025-06 --leverage 1 --commission 0.0005 --slippage 0.0001
```

**Trade-by-trade detail** for one run:

```bash
python debug_mode.py --symbol BTCUSDT --interval 1h --start 2024-01 --end 2024-03 --strategy take_loss
```

**Parameter optimization** (random search over `config.ini`'s
`[optimization]` section, results saved to SQLite):

```bash
python main.py --optimize --max-evals 50
```

**Watchlist scan** (read-only, no orders):

```bash
python scan_signals.py --symbols BTCUSDT ETHUSDT SOLUSDT --intervals 5m 15m 1h
```

**Live testnet bot**:

```bash
python live_testnet.py --interval 1h --leverage 1          # dry run, prints signals only
python live_testnet.py --interval 1h --leverage 1 --live   # places real testnet orders
python live_testnet.py --interval 1h --live --debug        # forces an alternating trade every candle, for smoke-testing order placement
```

**One-shot check**:

```bash
python testnet_once.py --symbol BTCUSDT --interval 1h            # dry run
python testnet_once.py --symbol BTCUSDT --interval 1h --live     # places an order if there's a signal
```

## Honest backtest results

BTCUSDT, 1h, 2021-01 → 2025-06 (39,408 candles, verified gap-free and
duplicate-free), `key_value=12, atr_period=14, tp_mult=3.0, sl_mult=1.5`,
`config.ini` defaults (`leverage=1x, commission=0.05%, slippage=0.01%,
risk_pct=1%`), $100,000 starting capital:

| Strategy | Trades | Return | Max DD | Sharpe | Sortino | Win rate | Profit factor |
|---|---|---|---|---|---|---|---|
| `basic` | 107 | **+32.73%** | 12.27% | 0.952 | 0.293 | 46.7% | 1.46 |
| `supertrend` | 75 | **+24.86%** | 10.33% | 0.879 | 0.227 | 48.0% | 1.53 |
| `inner_trend` | 106 | **+34.19%** | 12.27% | 0.988 | 0.304 | 47.2% | 1.48 |
| `take_loss` | 107 | **+14.04%** | 9.43% | 0.535 | 0.132 | 46.7% | 1.27 |

Per-year breakdown (`total_return_pct`, same params):

| Year | basic | supertrend | inner_trend | take_loss |
|---|---|---|---|---|
| 2021 | +5.41% | +1.06% | +6.57% | +4.90% |
| 2022 (bear) | +9.03% | +10.58% | +10.28% | +0.87% |
| 2023 | +7.07% | +2.86% | +6.72% | +3.13% |
| 2024 | +10.13% | +8.82% | +12.32% | +3.98% |
| 2025 H1 | +0.40% | +1.41% | +1.32% | +2.07% |

No year was a net loser for any variant over this window — a materially
different (and much less alarming) risk profile than a strategy with no
per-trade stop-loss, precisely because every trade here has a hard,
ATR-sized SL from the moment it opens.

## Parameter sensitivity - a systematic sweep, not a cherry-picked result

`sweep_results.csv` in this repo contains **all 36** combinations of
`key_value ∈ {8,10,12,15} × atr_period ∈ {10,14,20} × leverage ∈ {1,3,5}`,
`basic` strategy, same full 2021–2025 period — wins and losses alike, not
just the good ones.

- **33 / 36 (91.7%) were profitable.** Median return: **+10.79%**.
- Best: `key_value=12, atr_period=14` (leverage=3x or 5x - leverage only
  matters where the risk-based quantity would otherwise exceed it, which
  is rare here) → **+33.20%**, max DD 13.51%.
- Worst: `key_value=8, atr_period=20` (leverage=3x or 5x - tied) →
  **-13.98%**, max DD 24.13%, 35.2% win rate - a genuinely bad
  combination, not filtered out.
- `key_value=8` (a tighter/more sensitive trailing stop) combined with a
  longer `atr_period=20` (slower-reacting ATR) consistently produced the
  worst results in this sweep - more, smaller, lower-quality trades
  (193 vs ~90-110 for other settings). This is a directional signal from
  one asset/period, not a tuned recommendation.

Reproduce it yourself - the sweep is plain Python over `Backtester`, not
a separate script in this version, but the loop is three lines:
```python
for kv, ap, lev in itertools.product([8,10,12,15], [10,14,20], [1,3,5]):
    ...
```

## What changed (audit summary)

The version this was rewritten from had the backtester and the live bot
disagreeing about what strategy was actually being traded, in three
separate ways, plus a headline README claim with no supporting code:

- **The live bot generated signals from price ticks, not candle closes.**
  `live_testnet.py`'s `on_tick()` - wired to the `miniTicker` WebSocket
  stream, firing roughly once per second - called `UTBotCore.update()`
  on every tick. `UTBotCore` is a recursive, stateful translation of the
  Pine Script indicator that assumes one call per candle close (exactly
  how `backtester.py` calls it, and how TradingView evaluates it); feeding
  it ~3600 tick-noise updates per hourly candle instead of one produces a
  different, never-backtested trailing-stop trajectory. This was the
  documented design (the old README's architecture diagram explicitly
  routed "Signal check" through miniTicker), not an accidental slip.
  Fixed: `core.update()` is now called exactly once, inside
  `on_candle_close()`, using that candle's real close price;
  `on_tick()` only tracks the latest price.
- **Three different, disagreeing position-sizing formulas.**
  `backtester.py` capped notional at `capital × max_leverage` (a loose
  ceiling); `live_testnet.py`'s `compute_quantity()` capped it at a
  hardcoded `equity × 0.15` and **never used its own `leverage`
  parameter** (confirmed via AST inspection — the name was bound but
  never referenced); `testnet_once.py` had a third version capping at
  `balance × leverage`. On real BTCUSDT ATR values, the backtested
  position could be **up to 15x larger** than what the live bot would
  actually have placed for the identical signal. Fixed: all of it now
  calls `utils.compute_position_size()` - one function, leverage is the
  one governing cap, no hidden constant.
- **`--leverage` / `--commission` (and `config.ini`'s `leverage`,
  `commission`, `slippage`) did nothing.** All three places `main.py`
  constructed a `Backtester` used hardcoded `max_leverage=20,
  commission_rate=0.0004, slippage_rate=0.0002` regardless of config or
  CLI flags — confirmed empirically (identical output for `--leverage 1`
  vs `--leverage 20` before the fix). Fixed via one `_build_backtester()`
  helper that actually reads `config`; added `--slippage` / `--risk-pct`
  flags that were missing entirely.
- **`scan_signals.py` didn't run at all** - `ImportError: cannot import
  name 'get_klines'` (the function had been renamed to `fetch_klines` in
  `live_testnet.py` and the one caller never got updated). Fixed, and
  the file's own from-scratch ATR reimplementation (a third copy of the
  same Wilder's-ATR logic already in `utils.py`) was removed in favor of
  importing it.
- **`debug_mode.py` was an independent ~250-line reimplementation of the
  `take_loss` strategy loop** that silently omitted slippage on every
  exit type. Confirmed by running both on identical data: same 6 trades,
  different final capital ($95,130.64 vs $95,242.47). Fixed by deleting
  the duplicate logic entirely — `debug_mode.py` now calls
  `Backtester.run()` and only adds verbose per-trade printing, so it
  cannot diverge from the real backtester again.
- **The funding-cost model charged funding on the "borrowed" portion of
  notional** (`notional × (1 - 1/leverage)`), understating cost by
  roughly `1/leverage`. Real Binance perpetual funding is paid on the
  full notional regardless of leverage. Fixed.
- **The shipped `utbot_results.db` mixed results from two code
  versions** — reproducing each row against the current code at the time
  showed 3 of 7 matched exactly and 4 were stale (from before an earlier,
  undocumented fix). The database and the pre-computed `trades_*.csv`
  files are regenerated fresh for this version and are `.gitignore`d, as
  they were before — don't commit them.
- **The README's headline "98 USDT perpetuals, 88/98 profitable, +45.09%
  avg return, Sharpe 1.072" result had no supporting code anywhere in the
  repo** — no symbol list of that size, no aggregation logic, nothing in
  the database schema for a multi-symbol summary. `scan_signals.py` (the
  only multi-symbol code that existed) checks 9 current signals, not a
  98-symbol historical backtest. Removed; replaced with the real,
  reproducible single-symbol results above.
- `requirements.txt` was missing `pytz` (imported by `utils.py`) — a
  fresh install would fail. Fixed.

## Risk / limitations

- **Backtest cost model is an approximation**: fixed commission/slippage
  rates and a funding rate applied uniformly per `funding_interval_bars`,
  not real historical funding rates or order-book depth.
- **No liquidation modeling.** Stops are simulated as always filling
  exactly at the stop price plus slippage; a real account could be
  liquidated before a stop order fills in a fast, thin market.
  `max_leverage` bounds position size but the backtester doesn't check
  for margin calls mid-trade.
- **SL and TP are checked in a fixed order when both fall inside the same
  candle's range** (SL first, for LONG and SHORT alike) — a deliberate,
  conservative tie-break, not a resolved ambiguity: OHLC data alone
  cannot tell you which was actually touched first.
- **Live bot assumes one-way position mode** and a REST fetch of the
  just-closed candle immediately after the WebSocket `kline` close event
  — there's a small window where the REST data could theoretically lag
  the WS event; a short delay before the ATR refetch would reduce this
  further.
- **Tick/step-size rounding** (`live_testnet.py`) is derived from the
  exchange's real `tickSize`/`stepSize` filters via `log10`, which is
  only exact when those are clean powers of ten — true for BTCUSDT, not
  guaranteed for every symbol.
- Nothing here is financial advice. Test on testnet, understand the
  mechanics, and only risk money you can afford to lose.
