"""
Breakout Spot Strategy — Live Spot Bot (WebSocket-based)

Production-grade live trading for Binance Spot.
- Real-time price via WebSocket (miniTicker)
- Candle close via WebSocket (kline) for ATR recalc + signal generation
- Market orders with SL (STOP_LOSS_LIMIT)
- Trailing stop on every tick
- Position recovery on startup
- Trade logging to CSV
"""

import math
import os
import sys
import time
import hmac
import hashlib
import json
import csv
import signal
import threading
import requests
import urllib.parse
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

import websocket
import pandas as pd
import numpy as np

from strategy import BreakoutCore, Signal, StrategyParams
from utils import calculate_atr_from_df


# ── Config ────────────────────────────────────────────────────────────

SPOT_BASE = "https://api.binance.com"
SPOT_TESTNET_BASE = "https://testnet.binance.vision"
WS_BASE = "wss://stream.binance.com:9443"
WS_TESTNET_BASE = "wss://stream.testnet.binance.vision:9443"

API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

_filters_cache = {}
_server_time_offset = 0


# ── Logging ───────────────────────────────────────────────────────────

_log_file = None
_log_lock = threading.Lock()


def init_log(symbol: str):
    global _log_file
    os.makedirs('logs', exist_ok=True)
    filename = f"logs/{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    _log_file = open(filename, 'w', newline='')
    writer = csv.writer(_log_file)
    writer.writerow(['timestamp', 'event', 'side', 'price', 'quantity', 'notional',
                     'stop_loss', 'pnl', 'reason'])
    _log_file.flush()
    return filename


def log_trade(event: str, side: str = '', price: float = 0, quantity: float = 0,
              notional: float = 0, stop_loss: float = 0, pnl: float = 0, reason: str = ''):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _log_lock:
        if _log_file:
            writer = csv.writer(_log_file)
            writer.writerow([ts, event, side, f"{price:.4f}", f"{quantity:.8f}",
                           f"{notional:.2f}", f"{stop_loss:.4f}", f"{pnl:.2f}", reason])
            _log_file.flush()
    print(f"  [{ts[-8:]}] {event} {side} @ {price:.2f} qty={quantity:.6f} ${notional:.2f} {reason}")


# ── Exchange filters ──────────────────────────────────────────────────

def _decimal_places(value: float) -> int:
    if value > 0:
        return max(0, round(-math.log10(value)))
    return 0


def get_symbol_filters(symbol: str) -> dict:
    if symbol in _filters_cache:
        return _filters_cache[symbol]
    url = f"{SPOT_BASE}/api/v3/exchangeInfo"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    for s in resp.json()['symbols']:
        if s['symbol'] == symbol:
            filters = {}
            for f in s['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    filters['step_size'] = _decimal_places(float(f['stepSize']))
                    filters['min_qty'] = float(f['minQty'])
                    filters['max_qty'] = float(f['maxQty'])
                elif f['filterType'] == 'PRICE_FILTER':
                    filters['tick_size'] = _decimal_places(float(f['tickSize']))
                elif f['filterType'] == 'NOTIONAL':
                    filters['min_notional'] = float(f.get('minNotional', 10))
            _filters_cache[symbol] = filters
            return filters
    return {'step_size': 3, 'tick_size': 2, 'min_qty': 0.001, 'max_qty': 1000, 'min_notional': 10}


def floor_to_step(qty: Decimal, step_size: int) -> Decimal:
    factor = 10 ** step_size
    return Decimal(str(int(qty * factor))) / factor


def fmt_qty(qty: float, precision: int) -> str:
    return f"{qty:.{precision}f}"


def fmt_price(price: float, precision: int) -> str:
    return f"{price:.{precision}f}"


# ── Time sync ─────────────────────────────────────────────────────────

def klines_to_closed_frame(data: list) -> pd.DataFrame:
    """Klines as a DataFrame, keeping only candles that have already closed.

    Binance returns the still-forming candle as the last row; feeding it to the
    strategy would mix a partial bar into every indicator.
    """
    now_ms = time.time() * 1000
    rows = [k for k in data if k[6] <= now_ms]
    df = pd.DataFrame(rows, columns=[
        'Open time', 'Open', 'High', 'Low', 'Close', 'Volume',
        'Close time', 'Quote volume', 'Trades', 'Taker buy base',
        'Taker buy quote', 'Ignore'
    ])
    for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Quote volume']:
        df[col] = df[col].astype(float)
    return df


def replay_and_get_signal(core, df: pd.DataFrame, atr_arr) -> Signal:
    """Reset the core, replay every bar once, and return the signal of the last bar.

    The last bar must be fed to the core exactly once: a second update() would
    put that bar's own high into the resistance window, which makes
    close > resistance impossible and silently disables all entries.
    """
    core.reset()
    signal = Signal.HOLD
    last_i = len(df) - 1
    for i in range(len(df)):
        if not np.isnan(atr_arr[i]):
            s = core.update(df['High'].iloc[i], df['Low'].iloc[i],
                            df['Close'].iloc[i], df['Quote volume'].iloc[i], atr_arr[i])
            if i == last_i:
                signal = s
    return signal


def sync_server_time(base_url: str = SPOT_BASE):
    global _server_time_offset
    try:
        local_before = int(time.time() * 1000)
        resp = requests.get(f"{base_url}/api/v3/time", timeout=5)
        server_time = resp.json()['serverTime']
        local_after = int(time.time() * 1000)
        _server_time_offset = server_time - (local_before + local_after) // 2
        print(f"  Time sync: offset={_server_time_offset}ms")
    except Exception as e:
        print(f"  Warning: Time sync failed: {e}")


def get_timestamp() -> int:
    return int(time.time() * 1000) + _server_time_offset


# ── Signing ───────────────────────────────────────────────────────────

def sign_request(params: dict, secret: str) -> dict:
    p = dict(params)
    query = urllib.parse.urlencode(p)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    p['signature'] = signature
    return p


def _api_request(method, url, params=None):
    resp = requests.request(method, url, params=params,
                            headers={'X-MBX-APIKEY': API_KEY}, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json()


# ── Account ───────────────────────────────────────────────────────────

def get_account_balance(base_url: str = SPOT_BASE) -> float:
    params = sign_request({'timestamp': get_timestamp()}, API_SECRET)
    resp = requests.get(f"{base_url}/api/v3/account", params=params,
                        headers={'X-MBX-APIKEY': API_KEY}, timeout=10)
    resp.raise_for_status()
    for balance in resp.json()['balances']:
        if balance['asset'] == 'USDT':
            return float(balance['free'])
    return 0.0


def get_symbol_balance(symbol: str, base_url: str = SPOT_BASE) -> float:
    asset = symbol.replace('USDT', '').replace('BUSD', '').replace('USDC', '')
    params = sign_request({'timestamp': get_timestamp()}, API_SECRET)
    resp = requests.get(f"{base_url}/api/v3/account", params=params,
                        headers={'X-MBX-APIKEY': API_KEY}, timeout=10)
    resp.raise_for_status()
    for balance in resp.json()['balances']:
        if balance['asset'] == asset:
            return float(balance['free'])
    return 0.0


def get_open_orders(symbol: str, base_url: str = SPOT_BASE) -> list:
    params = sign_request({'symbol': symbol, 'timestamp': get_timestamp()}, API_SECRET)
    return _api_request('get', f"{base_url}/api/v3/openOrders", params)


# ── Order execution ───────────────────────────────────────────────────

def cancel_all_orders(symbol: str, base_url: str = SPOT_BASE):
    params = sign_request({'symbol': symbol, 'timestamp': get_timestamp()}, API_SECRET)
    try:
        _api_request('delete', f"{base_url}/api/v3/openOrders", params)
    except Exception as e:
        if '-2011' not in str(e):
            raise


def place_market_buy(symbol: str, quote_qty: float, base_url: str = SPOT_BASE) -> dict:
    params = sign_request({
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET',
        'quoteOrderQty': f"{quote_qty:.2f}",
        'newOrderRespType': 'FULL',
        'timestamp': get_timestamp(),
    }, API_SECRET)
    return _api_request('post', f"{base_url}/api/v3/order", params)


def place_market_sell(symbol: str, quantity: float, step_size: int,
                      base_url: str = SPOT_BASE) -> dict:
    params = sign_request({
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
        'quantity': fmt_qty(quantity, step_size),
        'newOrderRespType': 'FULL',
        'timestamp': get_timestamp(),
    }, API_SECRET)
    return _api_request('post', f"{base_url}/api/v3/order", params)


def place_stop_loss(symbol: str, quantity: float, stop_price: float,
                    tick_size: int, step_size: int,
                    base_url: str = SPOT_BASE) -> dict:
    stop_str = fmt_price(stop_price, tick_size)
    # A limit equal to the trigger may never fill in a fast drop; leave a 0.5% buffer.
    limit_str = fmt_price(stop_price * 0.995, tick_size)
    qty_str = fmt_qty(quantity, step_size)
    params = sign_request({
        'symbol': symbol, 'side': 'SELL', 'type': 'STOP_LOSS_LIMIT',
        'quantity': qty_str, 'price': limit_str, 'stopPrice': stop_str,
        'timeInForce': 'GTC',
        'timestamp': get_timestamp(),
    }, API_SECRET)
    return _api_request('post', f"{base_url}/api/v3/order", params)


# ── Market data WebSocket ─────────────────────────────────────────────

class MarketDataWS:
    def __init__(self, symbol: str, interval: str, base_url: str,
                 on_tick_callback, on_candle_close_callback):
        self.symbol = symbol
        self.interval = interval
        self.base_url = base_url
        self.on_tick = on_tick_callback
        self.on_candle_close = on_candle_close_callback
        self._ws = None
        self._running = False
        self._thread = None
        self._last_close_time = 0

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run(self):
        backoff = 5
        while self._running:
            try:
                sym = self.symbol.lower()
                streams = f"{sym}@miniTicker/{sym}@kline_{self.interval}"
                # Binance testnet supports WS streams on separate URL
                if 'testnet' in self.base_url:
                    ws_base = WS_TESTNET_BASE
                else:
                    ws_base = WS_BASE
                url = f"{ws_base}/stream?streams={streams}"
                print(f"  WS connecting: miniTicker + kline_{self.interval}")

                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
                backoff = 5
            except Exception as e:
                print(f"  WS error: {e}")

            if self._running:
                print(f"  WS reconnecting in {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _on_open(self, ws):
        print(f"  WS connected: miniTicker + kline_{self.interval}")

    def _on_message(self, ws, message):
        try:
            msg = json.loads(message)
            data = msg.get("data", msg)
            event = data.get("e", "")

            if event == "24hrMiniTicker":
                close = float(data.get("c", 0))
                if close > 0:
                    self.on_tick(close)

            elif event == "kline":
                k = data.get("k", {})
                if k.get("x", False):
                    close_time = k.get("t", 0)
                    if close_time != self._last_close_time:
                        self._last_close_time = close_time
                        self.on_candle_close()
        except Exception as e:
            print(f"  WS message error: {e}")

    def _on_error(self, ws, error):
        print(f"  WS error: {str(error)[:200]}")

    def _on_close(self, ws, close_status, close_msg):
        print(f"  WS closed")


# ── REST polling fallback (for testnet) ──────────────────────────────

INTERVAL_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
    '12h': 43200, '1d': 86400,
}


class PollingMarketData:
    """REST polling for Binance spot testnet (no WebSocket support)."""

    def __init__(self, symbol: str, interval: str, base_url: str,
                 on_tick_callback, on_candle_close_callback):
        self.symbol = symbol
        self.interval = interval
        self.base_url = base_url
        self.on_tick = on_tick_callback
        self.on_candle_close = on_candle_close_callback
        self._running = False
        self._thread = None
        self._last_close_time = 0

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        interval_sec = INTERVAL_SECONDS.get(self.interval, 3600)
        poll_price = min(3, interval_sec // 10) if interval_sec > 10 else 1
        print(f"  Polling mode: price every {poll_price}s, candle every {interval_sec}s")

        while self._running:
            try:
                # Poll price
                resp = requests.get(
                    f"{self.base_url}/api/v3/ticker/price",
                    params={'symbol': self.symbol}, timeout=5
                )
                if resp.status_code == 200:
                    price = float(resp.json().get('price', 0))
                    if price > 0:
                        self.on_tick(price)

                # Poll kline for candle close detection
                resp = requests.get(
                    f"{self.base_url}/api/v3/klines",
                    params={'symbol': self.symbol, 'interval': self.interval, 'limit': 3},
                    timeout=5
                )
                if resp.status_code == 200:
                    # The last row is normally the candle still being formed.
                    # Use the newest candle whose close time is already in the past.
                    now_ms = time.time() * 1000
                    closed = [k for k in resp.json() if k[6] <= now_ms]
                    if closed:
                        close_time = closed[-1][6]
                        if close_time != self._last_close_time:
                            if self._last_close_time:  # first poll only records the baseline
                                self.on_candle_close()
                            self._last_close_time = close_time

            except Exception as e:
                print(f"  Poll error: {e}")

            time.sleep(poll_price)


# ── Position state persistence ────────────────────────────────────────

STATE_FILE = 'position_state.json'


def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


# ── Main loop ─────────────────────────────────────────────────────────

def run_live_spot(symbol='BTCUSDT', interval='4h',
                  lookback=20, volume_mult=2.0, atr_period=14,
                  sl_mult=1.5, trailing_stop_pct=3.0,
                  min_volume_usdt=1_000_000,
                  budget_usdt=20.0,
                  dry_run=True, testnet=False, verbose=True,
                  debug=False):

    base_url = SPOT_TESTNET_BASE if testnet else SPOT_BASE

    params = StrategyParams(
        lookback=lookback,
        volume_multiplier=volume_mult,
        atr_period=atr_period,
        stop_loss_multiplier=sl_mult,
        trailing_stop_pct=trailing_stop_pct,
        min_volume_usdt=min_volume_usdt,
    )

    if not API_KEY or not API_SECRET:
        print("\n  ERROR: Set BINANCE_API_KEY and BINANCE_API_SECRET env vars")
        sys.exit(1)

    # Init logging
    log_path = init_log(symbol)

    if verbose:
        mode = 'DRY RUN' if dry_run else ('TESTNET' if testnet else 'LIVE')
        print(f"\n{'='*70}")
        print(f"  Breakout Spot Live — {symbol} {interval}")
        print(f"{'='*70}")
        print(f"  Mode:       {mode}")
        print(f"  Params:     Lookback={lookback}, VolMult={volume_mult}x, ATR={atr_period}, "
              f"SL={sl_mult}x, TS={trailing_stop_pct}%")
        print(f"  Budget:     ${budget_usdt:.0f}/trade")
        print(f"  Log:        {log_path}")
        print(f"{'='*70}")

    filters = get_symbol_filters(symbol)
    step_size = filters.get('step_size', 3)
    tick_size = filters.get('tick_size', 2)

    if verbose:
        print(f"  Filters:    step={10**-step_size}, tick={10**-tick_size}, "
              f"min_qty={filters.get('min_qty')}, min_notional={filters.get('min_notional')}")

    if not dry_run:
        sync_server_time(base_url)

    # ── Warm up strategy ──
    core = BreakoutCore(params)
    warmup_limit = max(lookback + 50, atr_period + 50)
    url = f"{base_url}/api/v3/klines"
    resp = requests.get(url, params={'symbol': symbol, 'interval': interval, 'limit': warmup_limit}, timeout=10)
    resp.raise_for_status()
    df = klines_to_closed_frame(resp.json())

    atr_arr = calculate_atr_from_df(df, atr_period)
    for i in range(len(df)):
        if not np.isnan(atr_arr[i]):
            core.update(
                df['High'].iloc[i], df['Low'].iloc[i],
                df['Close'].iloc[i], df['Quote volume'].iloc[i],
                atr_arr[i]
            )

    if verbose:
        last_close = df['Close'].iloc[-1]
        last_atr = atr_arr[-1] if not np.isnan(atr_arr[-1]) else 0
        print(f"  Warmup:     {len(df)} bars, last={last_close:.2f}, ATR={last_atr:.2f}")

    # ── Recover existing position ──
    position_open = False
    entry_price = 0.0
    peak_price = 0.0
    stop_loss_price = 0.0
    quantity = 0.0
    entry_time = ''
    last_signal_time = 0

    saved = load_state()
    if saved and saved.get('symbol') == symbol and saved.get('quantity', 0) > 0:
        position_open = True
        entry_price = saved['entry_price']
        peak_price = saved.get('peak_price', entry_price)
        stop_loss_price = saved['stop_loss_price']
        quantity = saved['quantity']
        entry_time = saved.get('entry_time', '')
        print(f"\n  Recovered position: LONG {quantity:.6f} @ {entry_price:.2f}")
        print(f"    SL: {stop_loss_price:.2f} | Peak: {peak_price:.2f}")

        # Verify SL order exists
        if not dry_run:
            try:
                orders = get_open_orders(symbol, base_url)
                sl_orders = [o for o in orders if o['type'] == 'STOP_LOSS_LIMIT']
                if not sl_orders:
                    print(f"    WARNING: No SL order found, placing new one")
                    place_stop_loss(symbol, quantity, stop_loss_price, tick_size, step_size, base_url)
            except Exception as e:
                print(f"    SL check error: {e}")

    # ── Shutdown handler ──
    _shutdown = threading.Event()

    def shutdown_handler(sig, frame):
        print("\n  Shutdown signal received...")
        _shutdown.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    def close_position_market(ref_price):
        """Sell the whole position at market.

        Returns the fill price, or None if the position is still open. State
        must only be cleared when this returns a price; a failed sell must not
        make the bot forget a position it still holds.
        """
        if dry_run:
            return ref_price
        try:
            cancel_all_orders(symbol, base_url)
            order = place_market_sell(symbol, quantity, step_size, base_url)
            executed = float(order.get('executedQty', 0) or 0)
            quote = float(order.get('cummulativeQuoteQty', 0) or 0)
            return quote / executed if executed > 0 and quote > 0 else ref_price
        except Exception as e:
            log_trade('SELL_ERROR', 'SELL', ref_price, quantity, 0, 0, 0, str(e))
            try:
                # The exchange-side stop may already have sold the position.
                if get_symbol_balance(symbol, base_url) < max(filters.get('min_qty', 0) or 0, 1e-12):
                    log_trade('EXTERNALLY_CLOSED', 'SELL', ref_price, quantity, 0, 0, 0,
                              'no balance left to sell')
                    return ref_price
            except Exception as e2:
                log_trade('BALANCE_CHECK_ERROR', '', 0, 0, 0, 0, 0, str(e2))
            try:
                place_stop_loss(symbol, quantity, stop_loss_price, tick_size, step_size, base_url)
            except Exception:
                pass
            return None

    # ── Tick callback (every price update) ──
    def on_tick(close):
        nonlocal peak_price, position_open

        if not position_open or _shutdown.is_set():
            return

        # Update peak using HIGH proxy (close from miniTicker is the best we have)
        if close > peak_price:
            peak_price = close

        # Check trailing stop
        trailing_stop = peak_price * (1 - trailing_stop_pct / 100)
        if close < trailing_stop and peak_price > entry_price:
            fill = close_position_market(close)
            if fill is None:
                return  # still holding: keep state, retry on the next tick
            pnl = (fill - entry_price) * quantity
            log_trade('TRAILING_STOP', 'SELL', fill, quantity, fill * quantity, 0, pnl,
                     f'peak={peak_price:.2f}')
            position_open = False
            peak_price = 0.0
            clear_state()

    # ── Candle close callback ──
    def on_candle_close():
        nonlocal position_open, entry_price, peak_price, stop_loss_price, quantity
        nonlocal last_signal_time, entry_time

        if _shutdown.is_set():
            return

        try:
            # Fetch fresh klines
            resp = requests.get(url, params={'symbol': symbol, 'interval': interval,
                                             'limit': warmup_limit}, timeout=10)
            resp.raise_for_status()
            df_new = klines_to_closed_frame(resp.json())

            # Recalculate ATR and run strategy
            atr_arr_new = calculate_atr_from_df(df_new, atr_period)
            signal = replay_and_get_signal(core, df_new, atr_arr_new)

            close = df_new['Close'].iloc[-1]
            high = df_new['High'].iloc[-1]
            low = df_new['Low'].iloc[-1]
            atr_val = atr_arr_new[-1]

            if np.isnan(atr_val) or atr_val <= 0:
                return

            ts = datetime.now().strftime('%H:%M:%S')

            # Debug mode: force signal every candle
            if debug and not position_open:
                signal = Signal.BUY

            # ── Check stop loss on existing position ──
            if position_open and low <= stop_loss_price:
                fill = close_position_market(stop_loss_price)
                if fill is None:
                    return  # still holding: keep state
                pnl = (fill - entry_price) * quantity
                log_trade('STOP_LOSS', 'SELL', fill, quantity, fill * quantity, stop_loss_price, pnl)
                position_open = False
                peak_price = 0.0
                clear_state()
                return

            # ── New entry on BUY signal ──
            if signal == Signal.BUY and not position_open:
                now = time.time()
                if now - last_signal_time < 30:
                    return

                entry_price = close
                sl = entry_price * (1 - sl_mult * atr_val / entry_price)
                notional = budget_usdt
                qty = notional / entry_price if entry_price > 0 else 0

                # Exchange minimum notional: skip instead of silently spending more than the budget.
                min_notional = filters.get('min_notional', 10) * 1.15
                if notional < min_notional:
                    log_trade('SKIP', 'BUY', entry_price, 0, 0, 0, 0,
                              f'budget {notional:.2f} < min notional {min_notional:.2f}')
                    return

                # Step size
                qty = float(floor_to_step(Decimal(str(qty)), step_size))
                if qty <= 0:
                    return

                peak_price = entry_price
                stop_loss_price = sl
                entry_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                log_trade('SIGNAL', 'BUY', entry_price, qty, entry_price * qty, sl, 0,
                         f'ATR={atr_val:.2f}')

                if not dry_run:
                    try:
                        cancel_all_orders(symbol, base_url)
                        order = place_market_buy(symbol, notional, base_url)
                        actual_qty = float(order.get('executedQty', qty))
                        actual_price = float(order.get('cummulativeQuoteQty', 0)) / actual_qty if actual_qty > 0 else entry_price

                        if actual_qty > 0:
                            qty = actual_qty
                        if actual_price > 0:
                            entry_price = actual_price
                            sl = entry_price * (1 - sl_mult * atr_val / entry_price)
                            stop_loss_price = sl

                        log_trade('FILLED', 'BUY', entry_price, qty, entry_price * qty, sl, 0)
                    except Exception as e:
                        log_trade('BUY_ERROR', 'BUY', entry_price, qty, 0, 0, 0, str(e))
                        return

                    try:
                        place_stop_loss(symbol, qty, sl, tick_size, step_size, base_url)
                        log_trade('SL_PLACED', 'SELL', sl, qty, 0, sl, 0)
                    except Exception as e:
                        log_trade('SL_ERROR', 'SELL', sl, qty, 0, 0, 0, str(e))

                quantity = qty
                position_open = True
                last_signal_time = time.time()

                # Persist state (real positions only)
                if not dry_run:
                    save_state({
                        'symbol': symbol,
                        'entry_price': entry_price,
                        'peak_price': peak_price,
                        'stop_loss_price': stop_loss_price,
                        'quantity': quantity,
                        'entry_time': entry_time,
                    })

        except Exception as e:
            print(f"  on_candle_close error: {e}")

    # ── Start market data feed ──
    if testnet:
        market_data = PollingMarketData(symbol, interval, base_url, on_tick, on_candle_close)
    else:
        market_data = MarketDataWS(symbol, interval, base_url, on_tick, on_candle_close)
    market_data.start()

    print(f"\n  Waiting for signals... (Ctrl+C to stop)\n")

    try:
        while not _shutdown.is_set():
            _shutdown.wait(timeout=1)
    except KeyboardInterrupt:
        pass

    # ── Graceful shutdown ──
    print("\n  Shutting down...")
    market_data.stop()

    if position_open and not dry_run:
        print(f"  Closing open position: LONG {quantity:.6f}")
        fill = close_position_market(entry_price)
        if fill is not None:
            log_trade('SHUTDOWN_SELL', 'SELL', fill, quantity, fill * quantity, 0,
                      (fill - entry_price) * quantity, 'shutdown')
            clear_state()
        else:
            print("  Could not close the position; state file kept so the next start recovers it.")
    if dry_run:
        clear_state()  # a simulated position must never be recovered as a real one
    if _log_file:
        _log_file.close()
    print("  Stopped.")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Breakout Spot Live')
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--interval', default='4h')
    parser.add_argument('--lookback', type=int, default=20)
    parser.add_argument('--volume-mult', type=float, default=2.0)
    parser.add_argument('--atr-period', type=int, default=14)
    parser.add_argument('--sl-mult', type=float, default=1.5)
    parser.add_argument('--trailing-stop', type=float, default=3.0)
    parser.add_argument('--min-volume', type=float, default=1_000_000)
    parser.add_argument('--budget', type=float, default=20.0)
    parser.add_argument('--live', action='store_true', help='Enable real orders on mainnet')
    parser.add_argument('--testnet', action='store_true', help='Use testnet')
    parser.add_argument('--debug', action='store_true', help='Force trade every candle')
    parser.add_argument('--dry-run', action='store_true',
                        help='never send orders (also with --testnet: testnet data, no orders)')

    args = parser.parse_args()
    if args.live and args.testnet:
        parser.error('--live and --testnet are mutually exclusive')
    if args.debug and args.live:
        parser.error('--debug forces trades and cannot be combined with --live')
    # Modes: default = dry run; --testnet = orders on testnet; --live = orders on mainnet.
    # --dry-run always wins.
    dry_run = args.dry_run or not (args.live or args.testnet)

    run_live_spot(
        symbol=args.symbol,
        interval=args.interval,
        lookback=args.lookback,
        volume_mult=args.volume_mult,
        atr_period=args.atr_period,
        sl_mult=args.sl_mult,
        trailing_stop_pct=args.trailing_stop,
        min_volume_usdt=args.min_volume,
        budget_usdt=args.budget,
        dry_run=dry_run,
        testnet=args.testnet,
        debug=args.debug,
    )
