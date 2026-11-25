"""
Candlestick Pattern Strategy — Live Trading Bot for Bybit Futures.

WebSocket for real-time candle data + pattern detection + market orders.
"""

import os
import sys
import time
import hmac
import hashlib
import json
import threading
import requests
import numpy as np
import pandas as pd
from datetime import datetime

import websocket

from strategy import detect_patterns, apply_filters


# ── Config ─────────────────────────────────────────────────────────────

TESTNET_BASE = "https://api-testnet.bybit.com"
MAINNET_BASE = "https://api.bybit.com"
WS_PUBLIC = "wss://stream.bybit.com/v5/public/linear"

API_KEY = os.environ.get("BYBIT_TESTNET_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_TESTNET_API_SECRET", "")

BYBIT_INTERVAL_MAP = {
    '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
    '1h': '60', '2h': '120', '4h': '240',
}


# ── API helpers ────────────────────────────────────────────────────────

def bybit_request(method, endpoint, params, base_url=TESTNET_BASE):
    timestamp = int(time.time() * 1000)
    headers = {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': str(timestamp),
        'X-BAPI-RECV-WINDOW': '5000',
        'Content-Type': 'application/json',
    }
    if method == 'GET':
        query = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        sign = hmac.new(API_SECRET.encode(), f"{timestamp}{API_KEY}5000{query}".encode(), hashlib.sha256).hexdigest()
        headers['X-BAPI-SIGN'] = sign
        resp = requests.get(f"{base_url}{endpoint}", params=params, headers=headers, timeout=10)
    else:
        body = json.dumps(params)
        sign = hmac.new(API_SECRET.encode(), f"{timestamp}{API_KEY}5000{body}".encode(), hashlib.sha256).hexdigest()
        headers['X-BAPI-SIGN'] = sign
        resp = requests.post(f"{base_url}{endpoint}", data=body, headers=headers, timeout=10)

    result = resp.json()
    if result.get('retCode') != 0:
        raise Exception(f"Bybit error: {result.get('retMsg')} (code={result.get('retCode')})")
    return result.get('result', {})


class ExchangeStateUnknown(RuntimeError):
    """Exchange state (position, balance) could not be read. Never treat it as 'flat'."""


def get_balance(base_url=TESTNET_BASE):
    try:
        result = bybit_request('GET', '/v5/account/wallet-balance', {'accountType': 'UNIFIED'}, base_url)
        for c in result.get('list', [{}])[0].get('coin', []):
            if c.get('coin') == 'USDT':
                return float(c.get('equity', 0))
    except Exception as e:
        raise ExchangeStateUnknown(f"balance unavailable: {e}") from e
    raise ExchangeStateUnknown("no USDT balance in wallet response")


def get_position(symbol, base_url=TESTNET_BASE):
    try:
        result = bybit_request('GET', '/v5/position/list',
                               {'category': 'linear', 'symbol': symbol}, base_url)
        for p in result.get('list', []):
            if p.get('symbol') == symbol:
                return {
                    'side': p.get('side', ''),
                    'size': float(p.get('size', 0)),
                    'entry_price': float(p.get('avgPrice', 0)),
                }
    except Exception as e:
        raise ExchangeStateUnknown(f"position unavailable: {e}") from e
    return {'side': '', 'size': 0, 'entry_price': 0}


def place_market_order(symbol, side, qty, base_url=TESTNET_BASE, reduce_only=False):
    params = {
        'category': 'linear', 'symbol': symbol, 'side': side,
        'orderType': 'Market', 'qty': str(qty),
    }
    if reduce_only:
        params['reduceOnly'] = True
    return bybit_request('POST', '/v5/order/create', params, base_url)


def cancel_all_orders(symbol, base_url=TESTNET_BASE):
    try:
        bybit_request('POST', '/v5/order/cancel-all', {
            'category': 'linear', 'symbol': symbol,
        }, base_url)
    except Exception:
        pass


def set_leverage(symbol, leverage, base_url=TESTNET_BASE):
    """Actually apply the --leverage setting on Bybit. Previously this value
    was only printed to the console and never sent to the exchange, so the
    account kept whatever leverage was last set manually/by default."""
    try:
        bybit_request('POST', '/v5/position/set-leverage', {
            'category': 'linear', 'symbol': symbol,
            'buyLeverage': str(leverage), 'sellLeverage': str(leverage),
        }, base_url)
    except Exception as e:
        # retCode 110043 = "leverage not modified" (already set) — harmless
        if '110043' not in str(e):
            print(f"  WARNING: could not set leverage: {e}")


def set_trading_stop(symbol, stop_loss, take_profit, base_url=TESTNET_BASE):
    """
    Set exchange-side stop-loss / take-profit on the current position via
    Bybit's /v5/position/trading-stop endpoint. This makes SL/TP a resting
    order on Bybit's side — it fires even if this process crashes, loses its
    WebSocket connection, or the REST poll is delayed. This is the piece that
    was previously completely missing: the bot used to only close on the next
    opposite pattern signal, with no protective stop at all.

    positionIdx=0 assumes one-way mode (not hedge mode). If the account uses
    hedge mode, positionIdx must be 1 (long) / 2 (short) instead.
    """
    return bybit_request('POST', '/v5/position/trading-stop', {
        'category': 'linear', 'symbol': symbol,
        'stopLoss': str(stop_loss), 'takeProfit': str(take_profit),
        'tpslMode': 'Full', 'positionIdx': 0,
    }, base_url)


def enter_with_protection(symbol, side_entry, qty, stop_loss, take_profit, base_url=TESTNET_BASE,
                          retries=3):
    """Market entry, then confirm the fill and attach exchange-side SL/TP.

    If the stop cannot be attached after several tries the position is closed
    immediately: a position without a stop is not acceptable.
    """
    place_market_order(symbol, side_entry, qty, base_url)
    filled = False
    for _ in range(retries):
        time.sleep(0.5)
        try:
            pos = get_position(symbol, base_url)
        except ExchangeStateUnknown:
            continue
        if pos['size'] != 0:
            filled = True
            break
    if not filled:
        print("  Entry not confirmed on the exchange; no SL/TP placed. Check the account.")
        return False
    for attempt in range(retries):
        try:
            set_trading_stop(symbol, stop_loss, take_profit, base_url)
            return True
        except Exception as e:
            print(f"  SL/TP attempt {attempt + 1} failed: {e}")
            time.sleep(1)
    side_close = 'Sell' if side_entry == 'Buy' else 'Buy'
    print("  Could not attach SL/TP: closing the position.")
    try:
        place_market_order(symbol, side_close, qty, base_url, reduce_only=True)
    except Exception as e:
        print(f"  EMERGENCY CLOSE FAILED: {e}. Close the position manually.")
    return False


_instrument_cache = {}


def get_instrument_info(symbol, base_url=MAINNET_BASE):
    """Fetch and cache qty/price precision so orders and SL/TP aren't rejected
    for violating the symbol's step size (previously qty was blindly
    round()-ed to 3 decimals regardless of the actual instrument rules)."""
    if symbol in _instrument_cache:
        return _instrument_cache[symbol]
    try:
        result = bybit_request('GET', '/v5/market/instruments-info',
                               {'category': 'linear', 'symbol': symbol}, base_url)
        info = result.get('list', [{}])[0]
        lot = info.get('lotSizeFilter', {})
        price_filter = info.get('priceFilter', {})
        data = {
            'qty_step': float(lot.get('qtyStep', 0.001)),
            'min_qty': float(lot.get('minOrderQty', 0.001)),
            'tick_size': float(price_filter.get('tickSize', 0.1)),
        }
    except Exception as e:
        print(f"  WARNING: could not fetch instrument info for {symbol}, using defaults: {e}")
        data = {'qty_step': 0.001, 'min_qty': 0.001, 'tick_size': 0.1}
    _instrument_cache[symbol] = data
    return data


def round_step(value, step):
    if step <= 0:
        return value
    return round(round(value / step) * step, 10)


def fetch_klines(symbol, interval, limit=200, base_url=MAINNET_BASE):
    bybit_interval = BYBIT_INTERVAL_MAP.get(interval, interval)
    result = bybit_request('GET', '/v5/market/kline', {
        'category': 'linear', 'symbol': symbol,
        'interval': bybit_interval, 'limit': str(limit),
    }, base_url)
    klines = result.get('list', [])
    if not klines:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
    klines.reverse()
    df = pd.DataFrame(klines, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'Turnover'])
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = df[col].astype(float)
    df['Date'] = pd.to_datetime(df['Timestamp'].astype(int), unit='ms', utc=True)
    df.set_index('Date', inplace=True)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']]


# ── WebSocket ──────────────────────────────────────────────────────────

class MarketDataWS:
    def __init__(self, symbol, interval, on_tick_cb, on_candle_close_cb):
        self.symbol = symbol
        self.interval = interval
        self.on_tick = on_tick_cb
        self.on_candle_close = on_candle_close_cb
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
                bybit_interval = BYBIT_INTERVAL_MAP.get(self.interval, self.interval)
                topic = f"kline.{bybit_interval}.{self.symbol}"

                def on_open(ws):
                    print(f"  WS connected: {topic}")
                    ws.send(json.dumps({"op": "subscribe", "args": [topic]}))

                def on_message(ws, message):
                    msg = json.loads(message)
                    if 'topic' not in msg:
                        return
                    data = msg.get('data', [])
                    if not data:
                        return
                    k = data[0]
                    close = float(k.get('close', 0))
                    if close > 0:
                        self.on_tick(close)
                    if k.get('confirm', False):
                        ts = k.get('start', 0)
                        if ts != self._last_close_time:
                            self._last_close_time = ts
                            self.on_candle_close(int(ts))

                def on_error(ws, error):
                    print(f"  WS error: {str(error)[:200]}")

                def on_close(ws, status, msg):
                    print("  WS closed")

                self._ws = websocket.WebSocketApp(
                    WS_PUBLIC, on_message=on_message,
                    on_error=on_error, on_close=on_close, on_open=on_open,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
                backoff = 5
            except Exception as e:
                print(f"  WS error: {e}")

            if self._running:
                print(f"  WS reconnecting in {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


# ── Main loop ──────────────────────────────────────────────────────────

def run_live(symbol='BTCUSDT', interval='1h', sl_atr=2.0, tp_atr=4.0,
             risk_pct=0.01, leverage=10, dry_run=True, debug=False, testnet=False,
             use_trend_filter=True, min_strength=1.3):

    base_url = TESTNET_BASE if testnet else MAINNET_BASE

    print(f"\n{'='*70}")
    print(f"  Candle Pattern Bot — {symbol} {interval}")
    print(f"{'='*70}")
    print(f"  Mode:     {'DRY RUN' if dry_run else 'LIVE'} ({'testnet' if testnet else 'mainnet'})")
    print(f"  SL/TP:    {sl_atr}x / {tp_atr}x ATR")
    print(f"  Leverage: {leverage}x")
    print(f"  Risk:     {risk_pct*100}%")
    print(f"  Trend:    {'ON' if use_trend_filter else 'OFF'} (EMA-50/200)")
    print(f"  Strength: {min_strength}")
    if debug:
        print(f"  DEBUG:    ON (force trade every candle)")
    print(f"{'='*70}")

    if not API_KEY and not dry_run:
        print("\n  ERROR: Set BYBIT_TESTNET_API_KEY env var")
        sys.exit(1)

    if not dry_run:
        set_leverage(symbol, leverage, base_url)

    # Warmup
    warmup_limit = 250 if use_trend_filter else 100
    df = fetch_klines(symbol, interval, warmup_limit, base_url)
    if len(df) < 50:
        print(f"  ERROR: Not enough data ({len(df)} bars)")
        sys.exit(1)

    df = detect_patterns(df, atr_period=14, min_body_atr=0.15)
    df = apply_filters(df, ema_fast=50, ema_slow=200, min_strength=min_strength,
                       min_atr_pct=0.3, use_trend_filter=use_trend_filter)
    last_atr = df['ATR'].iloc[-1] if 'ATR' in df.columns else 0
    print(f"  Warmup: {len(df)} bars, ATR={last_atr:.4f}")

    last_atr_val = [last_atr]

    def on_tick(close):
        pass

    def on_candle_close(closed_start_ms=None):
        nonlocal last_atr_val
        try:
            df_new = fetch_klines(symbol, interval, warmup_limit, base_url)
            # REST may already include the next, still-forming candle: keep only
            # candles up to the one the WebSocket confirmed as closed.
            if closed_start_ms is not None:
                df_new = df_new[df_new.index <= pd.to_datetime(closed_start_ms, unit='ms', utc=True)]
            if len(df_new) >= 50:
                df_new = detect_patterns(df_new, atr_period=14, min_body_atr=0.15)
                df_new = apply_filters(df_new, ema_fast=50, ema_slow=200,
                                       min_strength=min_strength, min_atr_pct=0.3,
                                       use_trend_filter=use_trend_filter)
                last_atr_val[0] = df_new['ATR'].iloc[-1]
                signal = df_new['Signal'].iloc[-1]
                strength = df_new['Signal_strength'].iloc[-1]
                pattern = df_new['Pattern'].iloc[-1]
                ts = datetime.now().strftime('%H:%M:%S')

                # Debug mode: force trade every candle
                if debug:
                    # Close existing position first
                    try:
                        pos = get_position(symbol, base_url)
                    except ExchangeStateUnknown as e:
                        print(f"  [{ts}] [DEBUG] {e}; skipping")
                        return
                    has_position = pos['size'] != 0
                    if has_position:
                        side_close = 'Sell' if pos['side'] == 'Buy' else 'Buy'
                        if not dry_run:
                            cancel_all_orders(symbol, base_url)
                            place_market_order(symbol, side_close, abs(pos['size']), base_url)
                        print(f"  [{ts}] [DEBUG] Closed position")

                    # Alternate Buy/Sell
                    debug_side = 'Buy' if int(ts.split(':')[1]) % 2 == 0 else 'Sell'
                    atr = last_atr_val[0]
                    inst = get_instrument_info(symbol, base_url)
                    entry_price = df_new['Close'].iloc[-1]
                    if debug_side == 'Buy':
                        stop_loss = entry_price - sl_atr * atr
                        take_profit = entry_price + tp_atr * atr
                    else:
                        stop_loss = entry_price + sl_atr * atr
                        take_profit = entry_price - tp_atr * atr
                    stop_loss = round_step(stop_loss, inst['tick_size'])
                    take_profit = round_step(take_profit, inst['tick_size'])
                    balance = get_balance(base_url) if not dry_run else 100000
                    sl_distance = sl_atr * atr
                    if sl_distance > 0:
                        risk_amount = balance * risk_pct
                        qty = risk_amount / sl_distance
                        qty = round_step(qty, inst['qty_step'])
                        if qty >= inst['min_qty']:
                            print(f"  [{ts}] [DEBUG] {debug_side} @ {entry_price:.2f} qty={qty} "
                                  f"SL={stop_loss:.2f} TP={take_profit:.2f} ATR={atr:.2f}")
                            if not dry_run:
                                try:
                                    place_market_order(symbol, debug_side, qty, base_url)
                                    set_trading_stop(symbol, stop_loss, take_profit, base_url)
                                except Exception as e:
                                    print(f"  [{ts}] Order/SL-TP error: {e}")
                    return

                # Normal mode: only trade on signal
                if signal != 0:
                    print(f"  [{ts}] SIGNAL: {pattern} ({'BUY' if signal == 1 else 'SELL'}) strength={strength:.1f}")

                    try:
                        pos = get_position(symbol, base_url)
                    except ExchangeStateUnknown as e:
                        print(f"  [{ts}] {e}; skipping this candle (unknown state is not 'flat')")
                        return
                    has_position = pos['size'] != 0

                    if has_position:
                        pos_is_long = pos['side'] == 'Buy'
                        if (signal == 1) == pos_is_long:
                            # Same-direction signal: the backtest keeps the position; so do we.
                            print(f"  [{ts}] Signal in the direction of the open position: no action")
                            return
                        # Opposite signal: close and reverse (as the backtest does).
                        side_close = 'Sell' if pos_is_long else 'Buy'
                        if not dry_run:
                            try:
                                cancel_all_orders(symbol, base_url)
                                place_market_order(symbol, side_close, abs(pos['size']), base_url,
                                                   reduce_only=True)
                            except Exception as e:
                                print(f"  [{ts}] Close error: {e}; not opening a new position")
                                return
                        print(f"  [{ts}] Closed position")

                    atr = last_atr_val[0]
                    inst = get_instrument_info(symbol, base_url)
                    if signal == 1:
                        side_entry = 'Buy'
                        entry_price = df_new['Close'].iloc[-1] * 1.0002
                        stop_loss = entry_price - sl_atr * atr
                        take_profit = entry_price + tp_atr * atr
                    else:
                        side_entry = 'Sell'
                        entry_price = df_new['Close'].iloc[-1] * 0.9998
                        stop_loss = entry_price + sl_atr * atr
                        take_profit = entry_price - tp_atr * atr
                    stop_loss = round_step(stop_loss, inst['tick_size'])
                    take_profit = round_step(take_profit, inst['tick_size'])

                    try:
                        balance = get_balance(base_url) if not dry_run else 100000
                    except ExchangeStateUnknown as e:
                        print(f"  [{ts}] {e}; skipping entry")
                        return
                    sl_distance = sl_atr * atr
                    if sl_distance > 0:
                        risk_amount = balance * risk_pct
                        qty = risk_amount / sl_distance
                        # Same cap as the backtest: notional <= balance * leverage.
                        qty = min(qty, balance * leverage / entry_price)
                        qty = round_step(qty, inst['qty_step'])
                        if qty >= inst['min_qty']:
                            print(f"  [{ts}] {side_entry} @ {entry_price:.4f} qty={qty} "
                                  f"SL={stop_loss:.4f} TP={take_profit:.4f} ATR={atr:.4f}")
                            if not dry_run:
                                enter_with_protection(symbol, side_entry, qty, stop_loss, take_profit, base_url)
                        else:
                            print(f"  [{ts}] Skipped: qty {qty} below min_qty {inst['min_qty']}")
        except Exception as e:
            print(f"  Candle close error: {e}")

    market_ws = MarketDataWS(symbol, interval, on_tick, on_candle_close)
    market_ws.start()
    print(f"\n  Waiting for signals... (Ctrl+C to stop)\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Stopping...")
        market_ws.stop()
        print("  Stopped.")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Candle Pattern Bot — Bybit Futures')
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--interval', default='1h')
    parser.add_argument('--sl-atr', type=float, default=2.0)
    parser.add_argument('--tp-atr', type=float, default=4.0)
    parser.add_argument('--leverage', type=int, default=10)
    parser.add_argument('--risk-pct', type=float, default=0.01)
    parser.add_argument('--live', action='store_true', help='Enable real orders')
    parser.add_argument('--testnet', action='store_true', help='Use testnet')
    parser.add_argument('--debug', action='store_true', help='Force trade every candle')
    parser.add_argument('--no-trend-filter', action='store_true', help='Disable EMA trend filter')
    parser.add_argument('--min-strength', type=float, default=1.3)

    args = parser.parse_args()

    run_live(
        symbol=args.symbol,
        interval=args.interval,
        sl_atr=args.sl_atr,
        tp_atr=args.tp_atr,
        leverage=args.leverage,
        risk_pct=args.risk_pct,
        dry_run=not args.live,
        testnet=args.testnet,
        debug=args.debug,
        use_trend_filter=not args.no_trend_filter,
        min_strength=args.min_strength,
    )
