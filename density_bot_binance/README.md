# Density Bot

I first stumbled into HFT while reading Michael Lewis's "Flash Boys." The book describes how firms build server rooms right next to stock exchanges and lay dedicated fiber optic cables just to gain a few microseconds. It got me thinking if the edge is purely about speed and infrastructure, what happens when you try the same strategy on regular hardware?

I built this bot to find out. It monitors orderbooks on Binance Futures, detects large bid-wall densities that might act as support, and places limit buys near them. The take-profit is around 1% of price, the stop-loss is tighter, and both were also tested in tick-based variants. The core idea: big buy walls hold price up, so buying near them should work. In practice, it doesn't.

## How It Works

1. Streams L2 orderbook data via WebSocket for 200+ pairs
2. Scores price levels by volume density
3. Places limit buy orders near detected walls
4. Manages exits via OCO (take-profit + stop-loss)

## Results

The bot ran in dry-run mode across multiple strategy versions over several weeks. **It is unprofitable.** Win rate stayed around 35-40%, and the best-performing version was not statistically significant. Different versions used different parameters, deposit sizes, and filters, so no single number captures the full picture - the consistent finding was that the strategy does not produce a reliable edge.

## Local Setup

```bash
git clone https://github.com/username/density-bot.git
cd density-bot
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp config.ini.example config.ini
# Edit config.ini with your Binance API keys, Telegram token, PostgreSQL credentials

# Create database
psql -U postgres -c "CREATE DATABASE density;"

python main.py
```

Set `TESTNET=true` in `config.ini` for simulated trading.

## Why It Loses vs Real HFT

The strategy itself isn't the problem - the infrastructure gap is. I measured the actual latency of this bot:

**Measured latency (from this machine):**
- REST API round-trip to Binance: **~95ms** avg (P50: 93ms, P95: 142ms)
- WebSocket message parse: **~0.024ms** (essentially free)
- Python event loop overhead: **~0.5-2ms** (GIL, GC, context switches)
- **Total signal-to-order: ~100ms**

A co-located HFT server achieves network round-trips under **50 microseconds** — that's a 2000x gap. The order of operations:

1. WebSocket message arrives (same for everyone)
2. Python parses JSON + runs scoring logic (~1-5ms)
3. Constructs HTTP request + sends to Binance REST API (~95ms)
4. Binance processes the order

By step 3, an HFT firm has already completed steps 1-4 and exited its position.

**What actually happened** — 98.4% of the densities this bot detected existed for less than 5 seconds. HFT firms place large orders to bait slower participants, then cancel them before anyone fills. This bot was the bait. The absorption ratio across all trades was 0 — none of the density was ever real.

**Latency hierarchy:**

| Layer | Latency | Technology |
|-------|---------|------------|
| Co-located FPGA | <1μs | Xilinx Alveo, custom NICs, kernel bypass |
| Co-located C++ | 10-100μs | OpenOnload, lock-free queues |
| VPS C++ | 0.5-2ms | Standard network stack |
| **This bot (measured)** | **~100ms** | **Python asyncio, aiohttp, public internet** |
| Browser trader | 300-1000ms | REST API, browser overhead |

## What Would Actually Be Needed

**Without rewriting in C++:**
- Keep HTTP connections alive with connection pooling — avoids TCP handshake overhead on each order
- Pre-compute order parameters before density is confirmed
- Reduce ENTRY_CONFIRMATION_DELAY from 5s to 1-2s (densities disappear in <5s)
- Note: Binance Futures does NOT have WebSocket API for order placement — orders go through REST API only. WebSocket is used for market data and user data streams.

**With infrastructure changes:**
- Co-locate next to Binance matching engine (Tokyo). Monthly cost: $5k-50k/rack
- Rewrite core loop in C++ with lock-free queues (moodycamel::ConcurrentQueue)
- Or go FPGA: Xilinx Alveo U55C does orderbook reconstruction in <500ns

**Spoofing defense** — Real-time absorption tracking: compare density volume against actual fill rate on the trade tape. If a $50k wall appears but 0 contracts trade at that level in 100ms, it's fake. Requires reading the aggTrade stream alongside the orderbook.

**Time-stop** — Some trades lasted minutes instead of seconds, and the longer a trade was held, the worse it performed. Short-duration trades (<60s) were profitable, while long-duration trades (>3min) generated all the losses. The 5-10 minute bucket was the worst. A hard time-stop at 60-120 seconds would have significantly improved results. This is the lowest-hanging fruit.

**Time-of-day pattern** — There was a pattern of higher win rates during late Asia session and Europe-US overlap. The Asia-Europe transition was consistently unprofitable — high spread, low liquidity, maximum manipulation on altcoins. However, the sample size per session was too small to draw confident conclusions. This could be explored further with more data.

## Project Structure

| File | Description |
|------|-------------|
| `main.py` | Trading logic, position management, filters |
| `ob.py` | Orderbook processing, WebSocket connections |
| `db.py` | PostgreSQL ORM (SQLAlchemy) |
| `tg.py` | Telegram notifications |
| `utils.py` | Utilities |
| `config.ini.example` | Config template |

## Tech Stack

Python 3.10+, SQLAlchemy + asyncpg, aiogram 3.x, aiohttp, ujson, websocket-client

## License

MIT
