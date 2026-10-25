# Bollinger Bands Strategy - Bybit USDT Perpetual Futures

A backtesting toolkit + testnet trading bot for a Bollinger Bands strategy on
Bybit USDT perpetual futures: 4 strategy variants, a fee/slippage/funding-aware
backtester, walk-forward optimization, Monte Carlo robustness checks, and a
WebSocket bot for Bybit's testnet.

> **Disclaimer - read this first.** This is an educational project, not
> financial advice, and not a proven profitable strategy. On the one pair
> tested end-to-end in this repo (BTCUSDT, 2024-01 → 2025-06, 1h candles),
> **every variant loses money** after realistic fees. See
> [Results](#backtest-results-btcusdt-1h-2024-01--2025-06) and
> [Before you risk real money](#before-you-risk-real-money) before doing
> anything with a funded account. Crypto derivatives trading, especially
> leveraged, can lose you all of your capital quickly.

## How it works

Bollinger Bands use a moving average ± a multiple of standard deviation as
dynamic support/resistance:

- Upper band = SMA + `nbdevup`×σ, Lower band = SMA − `nbdevdn`×σ
- **BUY** when price crosses above the upper band (breakout continuation)
- **SELL** when price crosses below the lower band (breakdown continuation)
- 4 variants ship with different entry/exit logic - see
  [Strategy variants](#strategy-variants)

## Quick start

```bash
pip install -r requirements.txt

# Backtest one variant on the bundled BTCUSDT sample data
python main.py --symbol BTCUSDT --interval 1h --variant basic

# All 4 variants side by side
python main.py --symbol BTCUSDT --interval 1h --all-variants

# Independent correctness checks (no look-ahead, PnL math, edge cases)
python verify_backtest.py
python test_edge_cases.py

# Walk-forward (out-of-sample) validation
python walk_forward.py --symbol BTCUSDT --interval 1h --strategy basic

# Monte Carlo robustness check (trade-order permutation + bootstrap)
python monte_carlo.py --symbol BTCUSDT --interval 1h --strategy basic
```

Only `klines/BTCUSDT-1h-2024-01-2025-06.csv` is bundled to keep the repo
small. Any other `--symbol`/`--interval`/date range downloads and caches
automatically from Bybit's public REST API on first use (no API key needed
for historical klines).

### Live testnet bot

The bot only ever runs against Bybit's **testnet** unless you explicitly pass
`--mainnet` - and even then, `--live` is required to place real orders.
Without `--live` it's a dry run that prints signals but sends nothing.

```bash
# Free - no funded account needed to create testnet API keys:
# https://testnet.bybit.com/app/user/api-management
export BYBIT_TESTNET_API_KEY=your_key
export BYBIT_TESTNET_API_SECRET=your_secret

# Dry run - prints signals only, places nothing
python live_testnet.py --symbol BTCUSDT --interval 1h --variant basic

# Real orders on TESTNET (fake money) - trades the actual strategy signal
python live_testnet.py --symbol BTCUSDT --interval 1h --variant basic --live

# Connectivity/order-placement smoke test only - ignores the strategy signal
# entirely and force-opens alternating positions every candle. Dry-run only;
# the bot refuses to combine --debug with --live.
python live_testnet.py --symbol BTCUSDT --interval 1h --debug
```

`--mainnet-data` uses mainnet for price data while still trading on testnet
(some symbols have thin/fake data on testnet) - data source and order
destination are independent flags on purpose.

## Files

| File | Purpose |
|------|---------|
| `strategy.py` | 4 BB variants + RSI/BB indicator math |
| `backtester.py` | Fee/slippage/funding-aware bar-by-bar backtester |
| `live_testnet.py` | WebSocket bot for Bybit Futures Testnet |
| `main.py` | CLI: single backtest, all-variants, in-sample optimize |
| `batch_fast.py` | Batch backtest across many Bybit perpetuals (parallel) |
| `walk_forward.py` | Rolling train/test out-of-sample validation |
| `monte_carlo.py` | Trade-order permutation + bootstrap robustness checks |
| `filter_optimization.py` | Parameter sweep for the RSI/squeeze filters |
| `position_sizing.py` | Risk-based position sizing helpers |
| `dashboard.py` | Matplotlib equity-curve / stats report |
| `db.py` | SQLite storage for batch results |
| `utils.py` | Bybit V5 kline download + on-disk cache |
| `config.py` / `config.ini` | Central config (fees, leverage, dates, params) |
| `verify_backtest.py` | Independent script: no-lookahead + PnL sanity checks |
| `test_edge_cases.py` | Flat/spike/trend/SL-TP edge-case smoke tests |

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `bb_timeperiod` | 20 | BB SMA lookback (bars) |
| `bb_nbdevup` / `bb_nbdevdn` | 2.0 | Band width in standard deviations |
| `tp_multiplier` | 3.0 | Take profit = entry ± (BB width × 3.0 / 2) |
| `sl_multiplier` | 1.5 | Stop loss = entry ± (BB width × 1.5 / 2) |
| `leverage` | 20x | Max leverage cap for position sizing |
| `risk_pct` | 1% | Capital risked per trade (at the stop-loss distance) |
| `commission` | 0.055% | Bybit standard (non-VIP) taker fee - lower this if you have a fee discount tier |
| `slippage` | 0.02% | Estimated slippage per fill |
| `funding_rate` | 0.01% | Per 8h funding period, applied to full position notional |

## Architecture

```
REST API (klines, on startup)  ──→  warm up BB/RSI state from history
                                          │
WebSocket: kline stream  ──→  on candle CLOSE (once per bar)  ──→  recompute
                               BB/RSI  ──→  evaluate ONE signal  ──→  order
                                          │
                               on every tick (in between closes)
                               ──→  price display only, no signal logic
                                          │
REST API  ──→  market order (entry) + SL algo order + TP algo order
```

Signals are evaluated **once per closed candle**, using that candle's final
close - exactly matching how the backtester evaluates each bar. Live ticks
between candle closes are shown for visibility but never trigger a trade
decision; earlier drafts of this bot recomputed signals on every tick, which
let live behaviour drift from what was actually backtested. If you're
reviewing this code, `on_candle_close()` in `live_testnet.py` is the only
place order decisions are made.

## Strategy variants

| Variant | Logic |
|---------|-------|
| `basic` | BB crossover: BUY above upper band, SELL below lower band |
| `rsi_filter` | BB crossover + RSI confirmation (RSI>55 for BUY, <45 for SELL) |
| `squeeze` | Only trades breakouts following a period of below-average BB width |
| `mean_reversion` | Enter on band touch, exit at the middle band (fades the move) |

## Backtest results: BTCUSDT 1h (2024-01 to 2025-06)

Reproduce with `python main.py --symbol BTCUSDT --interval 1h --all-variants`
or see `sample_output/btcusdt_all_variants.txt`.

| Variant | Return | Sharpe | Win Rate | Trades | Max DD | Profit Factor |
|---------|--------|--------|----------|--------|--------|----------------|
| basic | -15.48% | -0.38 | 36.1% | 460 | -28.87% | 0.94 |
| rsi_filter | -10.99% | -0.24 | 36.1% | 441 | -26.49% | 0.95 |
| squeeze | -19.26% | -1.04 | 32.3% | 127 | -20.30% | 0.78 |
| mean_reversion | -73.75% | -6.42 | 33.1% | 856 | -73.86% | 0.49 |

Costs modeled: 0.055% taker fee per side, 0.02% slippage per fill (applied to
the fill price, counted once), and funding of 0.01% per 8h on the full notional,
always charged as a cost (a conservative simplification: with a positive rate
shorts actually receive funding). All variants lose money on this pair and
period. `mean_reversion` exits at the middle band, so its average win is small
relative to the stop distance and fees eat most of the edge.

Sharpe and Sortino use per-bar equity returns annualized with the bar size of
the chosen interval. "Calmar" in the output is total return divided by max
drawdown over the whole test period, not the annualized Calmar ratio.

`sample_output/` also has a `walk_forward.py` run (2 windows, 12 month train /
3 month test; 1 of 2 test windows profitable, compound test return -12.19%) and
a `monte_carlo.py` run (bootstrap: about 26% of resampled trade sets are
profitable). Both use a fixed seed (`--seed`, default 42) so they reproduce.
Two windows and one symbol are a tiny sample; they are a smoke test of the
tooling, not evidence for or against the strategy.

## What is checked, and how

- `python -m pytest test_regressions.py` covers: slippage counted once, final
  equity equal to initial capital plus the sum of trade PnL (including a
  position still open at the end of data), funding period derived from the
  bar size, the mean reversion exit signal not reopening a trade on the same
  bar, walk-forward train/test windows never overlapping, prefix invariance
  (results do not change when future bars are removed), Bybit trigger
  directions, the dry-run write block, and that an unreadable exchange state is
  never treated as "no position".
- `python verify_backtest.py` re-derives per-trade PnL, checks SL/TP fills
  (allowing for slippage), runs the prefix invariance check, and checks the
  accounting identity for every variant.
- `python test_edge_cases.py` runs synthetic-market smoke tests with assertions.
- Parameter search in `walk_forward.py` is seeded, so its output is reproducible.
- `db.py` uses parameterized queries. No API keys are stored in the repo; the
  live bot reads them from environment variables.

Limits of these checks: they show the code is internally consistent, not that
the strategy has an edge, and the live/testnet path has not been exercised
against a real exchange in this repo.

## Before you risk real money

Things this repo does **not** protect you from - read before connecting a
funded account:

- **Selection bias across many symbols.** Backtesting a strategy across
  hundreds of pairs and then picking the best performers (via `batch_fast.py`)
  will always surface a handful of "winners" even for a strategy with zero
  real edge, purely from testing that many hypotheses at once. A good
  Sharpe ratio on the single best symbol out of 200+ is not, by itself,
  evidence of a real edge - you'd need to validate that symbol's parameters
  on data the selection process never saw.
- **Walk-forward windows are limited by how much history you have.** With
  ~18 months of data and a 6–12 month training window, you only get a
  handful of out-of-sample test windows. Treat a "2 out of 2 profitable
  windows" result as a weak signal, not proof of robustness - the sample
  size is tiny either way.
- **Monte Carlo here tests path-dependency of the trades you already got,
  not whether the strategy has genuine predictive edge.** It answers "is
  this specific set of trades sensitive to their order/composition?", not
  "will this work going forward on data it hasn't seen." Useful, but it's
  one piece of evidence, not a verdict - and it will happily give you a
  polished, precise-looking "ROBUST" verdict for a strategy that isn't.
  The permutation test only reorders the same trades, so it cannot change the
  final capital; it is useful for drawdown, not for profitability.
- **No exchange liquidation modeling.** The backtester simulates your own
  stop-loss order, not Bybit's maintenance-margin liquidation engine. At
  high leverage, a fast enough move against you can liquidate the position
  before your stop-loss order fills, especially on lower-liquidity
  altcoins - that outcome is worse than anything in this backtest.
  Position sizing here is risk-based (caps risk at `risk_pct` of capital
  given the stop distance), which keeps typical implied leverage well below
  the `leverage` cap - but check the actual notional/leverage a signal
  produces before trusting it blind, especially in tight-range (squeeze)
  conditions.
- **Fees, slippage, and funding are estimates, not guarantees.** Confirm
  your account's actual fee tier, and remember slippage gets worse in low
  liquidity or during volatility spikes, not just on average.
- **This backtests one pair's specific 18-month window.** Markets change
  regimes; nothing here validates forward performance.

None of this means "the code is broken" - the mechanics check out (see
above). It means: treat any backtest, walk-forward, or Monte Carlo output
as one data point to interrogate, not a green light. If you do decide to
run this live, start on testnet, then with real money only at a size you
can afford to lose entirely, and monitor it closely.

## License

MIT - see `LICENSE`. Use at your own risk; see disclaimer above.
