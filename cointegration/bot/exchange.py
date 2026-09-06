"""
Binance USDT-M futures access via ccxt's async_support. Mirrors the role
of exchange/binance/client.py + exchange/gateway.py in algofactory_bot,
collapsed into one file since this prototype only talks to one venue.

Market data (fetch_ohlcv) works with no API key at all, which is what
lets the bot run in dry-run/paper mode with zero setup. Placing real
orders requires BINANCE_API_KEY/SECRET and DRY_RUN=false.

Async, not sync: every exchange call is I/O (an HTTP round trip), and the
bot opens/monitors/closes several pairs per cycle, so there's real time
to win by not blocking on one request while another could be in flight.
Rounding uses ccxt's own amount_to_precision/price_to_precision against
each market's real step size and tick size, rather than a hand-rolled
Decimal helper, since that's what ccxt is already built to do correctly
per-exchange.
"""

from __future__ import annotations

import asyncio

import ccxt.async_support as ccxt_async
import pandas as pd

from .config import BotConfig

# Binance futures error codes that will not succeed on retry - no point
# burning time/rate-limit budget on them.
PERMANENT_ERROR_CODES = {
    "-1013",  # LOT_SIZE / precision filter failure
    "-1102",  # mandatory param missing/malformed
    "-2010",  # account has insufficient balance / would trigger liquidation
    "-2019",  # margin is insufficient
    "-4003",  # quantity less than or equal to zero
    "-4164",  # notional below minimum
}


class PermanentOrderError(Exception):
    """An order failed in a way that retrying will not fix."""


class BinanceFuturesClient:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        params = {"enableRateLimit": True, "options": {"defaultType": "future"}}
        if cfg.binance_api_key and cfg.binance_api_secret:
            params["apiKey"] = cfg.binance_api_key
            params["secret"] = cfg.binance_api_secret
        self.exchange = ccxt_async.binanceusdm(params)
        if cfg.use_testnet:
            self.exchange.set_sandbox_mode(True)
        self._markets = None

    async def close(self):
        await self.exchange.close()

    async def load_markets(self):
        if self._markets is None:
            self._markets = await self.exchange.load_markets()
        return self._markets

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """symbol like 'BTCUSDT' -> ccxt wants 'BTC/USDT:USDT' for USDT-M futures."""
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        raw = await self.exchange.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("date")[["open", "high", "low", "close", "volume"]]

    async def fetch_price_matrix(self, symbols: list[str], timeframe: str, limit: int) -> pd.DataFrame:
        async def _fetch_one(sym: str):
            for attempt in range(3):
                try:
                    df = await self.fetch_klines(sym, timeframe, limit)
                    return sym, df["close"]
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5 * (attempt + 1))

        results = await asyncio.gather(*(_fetch_one(s) for s in symbols))
        closes = {sym: series for sym, series in results}
        wide = pd.DataFrame(closes).dropna(axis=0, how="any")
        return wide.sort_index()

    async def get_last_price(self, symbol: str) -> float:
        ticker = await self.exchange.fetch_ticker(self._to_ccxt_symbol(symbol))
        return float(ticker["last"])

    async def get_balance_usdt(self) -> float:
        bal = await self.exchange.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0.0))

    async def round_amount(self, symbol: str, qty: float) -> float:
        await self.load_markets()
        return float(self.exchange.amount_to_precision(self._to_ccxt_symbol(symbol), qty))

    async def round_price(self, symbol: str, price: float) -> float:
        await self.load_markets()
        return float(self.exchange.price_to_precision(self._to_ccxt_symbol(symbol), price))

    async def place_market_order(self, symbol: str, side: str, qty: float, dry_run: bool) -> dict:
        """side: 'buy' or 'sell'. Returns an order-like dict either way, so
        callers don't need to branch on dry_run.
        """
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        qty = await self.round_amount(symbol, qty)
        if dry_run:
            price = await self.get_last_price(symbol)
            return {
                "id": f"dry-{id(self)}-{symbol}-{side}", "symbol": symbol, "side": side,
                "amount": qty, "price": price, "status": "dry_run_filled",
            }
        try:
            return await self.exchange.create_order(ccxt_symbol, "market", side, qty)
        except Exception as e:
            if any(code in str(e) for code in PERMANENT_ERROR_CODES):
                raise PermanentOrderError(str(e)) from e
            raise

    async def place_protective_stop(self, symbol: str, side: str, qty: float,
                                     stop_price: float, dry_run: bool) -> dict | None:
        """A reduce-only STOP_MARKET order on a single leg, meant as a blunt
        circuit breaker (not the strategy's real exit logic).

        The strategy's actual exit condition is a joint z-score across both
        legs of a pair, which cannot be expressed as a single-leg stop order
        on the exchange. This order exists only so that if the bot process
        dies or loses connectivity between cycles, one leg cannot run away
        unbounded before the bot comes back and closes the pair properly.
        Treat it as a tail hedge, not the primary risk control - the
        primary one is the z-score/stop-loss check in run_cycle(), which
        runs every cycle regardless of whether this order exists.
        """
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        qty = await self.round_amount(symbol, qty)
        stop_price = await self.round_price(symbol, stop_price)
        if dry_run:
            return {
                "id": f"dry-stop-{id(self)}-{symbol}", "symbol": symbol, "side": side,
                "amount": qty, "stopPrice": stop_price, "status": "dry_run_placed",
            }
        try:
            # ccxt's own pattern for a futures stop order is to pass the base
            # order type ('market') and let it detect the stop from
            # stopPrice/triggerPrice in params, upgrading to STOP_MARKET
            # internally for a contract market - rather than passing the
            # exchange-specific string 'STOP_MARKET' as the type directly.
            # Checked this against ccxt's own create_order_request source
            # (binance.py) since I had no network access to test it against
            # the real API - I could not verify this end to end against
            # Binance itself, so treat this one call as the least-tested
            # part of the bot and check it yourself on testnet first.
            return await self.exchange.create_order(
                ccxt_symbol, "market", side, qty, None,
                {"stopPrice": stop_price, "reduceOnly": True},
            )
        except Exception as e:
            if any(code in str(e) for code in PERMANENT_ERROR_CODES):
                raise PermanentOrderError(str(e)) from e
            raise

    async def cancel_all_open_orders(self, symbol: str, dry_run: bool) -> None:
        if dry_run:
            return
        try:
            await self.exchange.cancel_all_orders(self._to_ccxt_symbol(symbol))
        except Exception:
            pass  # best-effort: nothing to cancel is not an error worth surfacing

    @staticmethod
    def _to_ccxt_symbol(symbol: str) -> str:
        # 'BTCUSDT' -> 'BTC/USDT:USDT'  (USDT-M perpetual notation in ccxt)
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        return symbol
