# ML Candle Patterns Bot

> **DISCLAIMER:** This is an educational project. Trading cryptocurrencies involves
> high risk of total loss. The author is not responsible for any financial losses.
> Do not use with real money without fully understanding the risks.

## What Is This

ML strategy that predicts the next candle direction using candlestick patterns.
GradientBoosting classifier trained on 13 technical patterns + indicators.

**Current result:** models perform better than random but remain unprofitable for live trading.

## How It Works

1. Load historical klines from Binance
2. Detect 13 candlestick patterns (doji, hammer, engulfing, etc.)
3. Train GradientBoosting on pattern features
4. Generate LONG/SHORT signals based on model predictions

---

## Notebook

Full theory, candlestick pattern visualizations, EDA, correlation analysis, stationarity testing, and ML model training are in the notebook:

**[ML_Trading_Bot.ipynb](ML_Trading_Bot.ipynb)**

Open in Google Colab or run locally.

---

## ML Model

- **Algorithm:** GradientBoosting (200 trees, depth=5, lr=0.1)
- **Features:** 13 patterns + returns + volatility + volume_ratio
- **Target:** next candle > current close (1=LONG, 0=SHORT)
- **Metrics:** Accuracy, Precision, Recall, F1, ROC-AUC

### Feature Engineering

1. **Candlestick patterns** - 13 binary features (True/False for each pattern)
2. **Returns** - price change percentage
3. **Volatility** - standard deviation of recent prices
4. **Volume ratio** - current volume vs average volume

### Stationarity

All features are tested for stationarity using the **Augmented Dickey-Fuller (ADF) test**. Non-stationary features are transformed via:
- Log transformation
- Log + differencing
- First differencing

This ensures the ML model trains on statistically meaningful data.

---

## Project Structure

| File | Description |
|------|-------------|
| `main.py` | Bot entry point - loads data, runs strategy, generates signals |
| `strategy.py` | Strategy logic - patterns + ML model combined |
| `data_loader.py` | Binance klines data loader |
| `patterns.py` | Candlestick pattern detection (13 patterns) |
| `ml_models.py` | GradientBoosting model training and prediction |
| `stationarity.py` | ADF test and stationarity transformation |
| `backtest.py` | Backtester with SL/TP and metrics |
| `ML_Trading_Bot.ipynb` | [**Colab notebook**](ML_Trading_Bot.ipynb) — theory, visualizations, EDA, ML training |
| `requirements.txt` | Python dependencies |

## Backtest

```bash
pip install -r requirements.txt
python backtest.py
```

Default parameters:
- Symbol: ADAUSDT
- Interval: 15m
- Period: 2022.09-2025.09
- SL: 2% | TP: 4%
- Commission: 0.1%

## Running

```bash
pip install -r requirements.txt

# Backtest
python backtest.py

# Demo
python main.py

# Or open notebook in Colab
```

## Framework Integration

For live trading, use the `algofactory_bot/` framework.

**IMPORTANT:** `algofactory_bot/` is a black box. DO NOT modify or change it.

Strategy connects as an adapter:
- `strategy.py` - pattern detection + ML prediction
- Signals (LONG/SHORT) are passed to the framework

## Contacts

- Telegram: [@KDR_98](https://t.me/KDR_98)
- LinkedIn: [dmitrii-kalenov](https://www.linkedin.com/in/dmitrii-kalenov)
- Email: drkalenov@gmail.com
