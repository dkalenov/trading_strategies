# Momentum Spot Bot - Binance Spot

Simple momentum-based bot that buys the top-performing USDT pair on Binance Spot.

**Disclaimer:** Educational purposes only. Crypto trading carries high risk of total loss.

## How It Works

- Scans all Binance USDT pairs for highest 24h price change percentage
- Fetches 120 minutes of 1-minute candles for the top performer
- If cumulative return over the window is positive → BUY
- Exits at +2% take profit or -1.5% stop loss

## Files

| File | Purpose |
|------|---------|
| `main.py` | Single-file bot: scanning, signal, execution, monitoring |

## Quick Start

```bash
pip install requests

export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret

python main.py
```

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `lookback` | 120 min | Momentum lookback window |
| `tp_pct` | 2% | Take profit percentage |
| `sl_pct` | 1.5% | Stop loss percentage |




