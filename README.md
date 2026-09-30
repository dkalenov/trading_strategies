# Trading Bots & Strategies

Automated trading bots and quantitative strategies for cryptocurrency exchanges (Binance, Bybit).

---

## DISCLAIMER

> **All strategies and bots presented here are for EDUCATIONAL and RESEARCH purposes ONLY.**
>
> **These are NOT trading recommendations. Trading cryptocurrencies involves HIGH RISK. Using any data, code, or ideas from this repository may result in COMPLETE LOSS OF FUNDS.**
>
> - Past performance does not guarantee future results
> - The author is not responsible for any financial losses
> - Do not trade with money you cannot afford to lose
> - Do not run bots in live mode without fully understanding the risks

---

## About

Trading strategies and bots covering:

- **Trend Following** - EMA Bot, SuperTrend Bot, UTBot Strategy, Sloping Bot, Sloping Bot 2.0
- **Volatility / Bands** - Bollinger Futures
- **Momentum** — Momentum Spot, Momentum Futures
- **Breakout** — Breakout Spot, Williams Fractal
- **Pattern Recognition** — Candle Patterns, ML Candle Patterns
- **Statistical Arbitrage** — Cointegration, Mean Reversion
- **Grid Trading** — Grid Trading
- **Orderbook Analysis** — Density Bot
- **Signals** — TradingView Screener, Trigger Orders
- **Alerts** — Price Alerts

---

## Strategies

| Strategy | Type | Exchange | Backtest | Status |
|----------|------|----------|----------|--------|
| [EMA Bot](EMA_bot_bybit/) | Trend Following | Bybit Futures | [Yes](EMA_bot_bybit/backtest.py) | Working |
| [Bollinger Futures](boll_bybit_futures/) | Volatility | Bybit Futures | No | Working |
| [SuperTrend Bot](supertrend_bot_binance/) | Trend Following | Binance Futures | [Yes](supertrend_bot_binance/supertrend_backtest.ipynb) | Working |
| [Grid Trading](grid_trading_binance_futures/) | Grid | Binance Futures | No | Working |
| [Density Bot](density_bot_binance/) | Orderbook | Binance Futures | No | **Unprofitable** (HFT latency gap) |
| [UTBot Strategy](UTBot_Strategy/) | Trend | Research | [Yes](UTBot_Strategy/UTBot_backtest.ipynb) | Indicator |
| [Williams Fractal](williams_fractal_strategy/) | Pattern | Research | [Yes](williams_fractal_strategy/williams_fractal_strategy.ipynb) | Research |
| [Momentum Spot](momentum_binance_spot/) | Momentum | Binance Spot | No | Working |
| [Momentum Futures](momentum_futures_bot/) | Momentum | Binance Futures | [Yes](momentum_futures_bot/momentum_backtest/) | Advanced |
| [Cointegration](cointegration/) | Statistical Arbitrage | Research | No | Research |
| [Mean Reversion](mean_reversion_strategy/) | Statistical | Bybit Futures | No | Working |
| [Candle Patterns](candle_pattern_strategy/) | Pattern | Research | [Yes](candle_pattern_strategy/Pattern_strategy.ipynb) | Educational |
| [ML Candle Patterns](ML_candle_patterns_bot/) | ML | Research | [Yes](ML_candle_patterns_bot/ML_Trading_Bot.ipynb) | **Ineffective** |
| [Price Alerts](price_alerts_binance/) | Alerts | Binance | No | **Stub** |
| [Trigger Orders](trigger_orders_bot/) | Triggers | Binance Futures | No | Working |
| [Sloping Bot 2.0](sloping_bot_2.0/) | Trend | Binance Futures | [Yes](sloping_bot_2.0/sloping_backtest/) | Advanced |
| [Sloping Bot](sloping_bot_binance/) | Trend | Binance Futures | [Yes](sloping_bot_binance/Sloping_backtest.ipynb) | Previous version |
| [Breakout Spot](breakout_spot_binance/) | Breakout | Binance Spot | No | Working |
| [TradingView Screener](tradingview_screener/) | Signals | Binance Futures | [Yes](tradingview_screener/backtest/) | Working |

### Stats

- **Total strategies:** 19
- **With backtests:** 12
- **Without backtests:** 7
- **Exchanges:** Binance (11), Bybit (3), Research (5)

---



## Contacts

- Telegram: [@KDR_98](https://t.me/KDR_98)
- LinkedIn: [dmitrii-kalenov](https://www.linkedin.com/in/dmitrii-kalenov)
- Email: drkalenov@gmail.com
