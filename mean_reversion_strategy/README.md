# Mean Reversion Strategy - Bybit Futures

Simple mean reversion bot that trades price deviations from SMA for Bybit Futures.

**Disclaimer:** Educational purposes only. Crypto trading carries high risk of total loss.

## How It Works

- Calculates SMA-25 (Simple Moving Average, 25 periods)
- **BUY signal:** Price deviates below SMA by more than 0.5% — expects reversion to mean
- **SELL signal:** Price deviates above SMA by more than 0.5% — expects reversion to mean
- Position closed when price returns to SMA line

## Files

| File | Purpose |
|------|---------|
| `main.py` | Live trading bot (pybit/Bybit client) |

## Quick Start

```bash
pip install pybit numpy pandas ta-lib

export BYBIT_API_KEY=your_key
export BYBIT_API_SECRET=your_secret

python main.py
```

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `sma_period` | 25 | SMA lookback period |
| `threshold_percentage` | 0.5% | Deviation threshold for entry |
| `interval` | 60 min | Kline timeframe |
| `symbol` | NEARUSDT | Trading pair |

## Backtest Results

No backtest included. Single-file live trading implementation.

## Contacts

Telegram: @KDR_98
