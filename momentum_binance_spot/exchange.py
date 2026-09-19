"""
exchange.py - the only module in this project that talks to Binance.

strategy.py and risk.py never import python-binance directly, which
is what lets them be unit tested and reused by the backtester without
a network connection or API keys. Everything that actually calls the
exchange - fetching tickers, fetching candles, reading a symbol's
LOT_SIZE/MIN_NOTIONAL filters, sending orders - lives here.
"""
from __future__ import annotations

import pandas as pd
from binance.client import Client

from risk import SymbolInfo
from utils import retry, setup_logger

logger = setup_logger(__name__)


class BinanceGateway:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.client = Client(api_key, api_secret, testnet=testnet)
        self._symbol_info_cache: dict[str, SymbolInfo] = {}

    @retry(attempts=3, delay_seconds=5)
    def get_tickers(self) -> list[dict]:
        """24h ticker stats for every symbol - this is where
        priceChangePercent, used to find the "top gainer", comes from."""
        return self.client.get_ticker()

    @retry(attempts=3, delay_seconds=5)
    def get_klines_df(self, symbol: str, interval: str, lookback_minutes: str) -> pd.DataFrame:
        raw = self.client.get_historical_klines(
            symbol, interval, f"{lookback_minutes} min ago UTC"
        )
        if not raw:
            raise ValueError(
                f"No klines returned for {symbol} {interval} "
                f"({lookback_minutes} min ago UTC) - symbol may have no "
                f"{interval} data or be inactive."
            )
        frame = pd.DataFrame(raw).iloc[:, :6]
        frame.columns = ["Time", "Open", "High", "Low", "Close", "Volume"]
        frame = frame.set_index("Time")
        frame.index = pd.to_datetime(frame.index, unit="ms")
        return frame.astype(float)

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """LOT_SIZE / MIN_NOTIONAL filters for a symbol, cached for the
        life of the process. The original bot never looked at these at
        all - see AUDIT.md item 4."""
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]

        info = self.client.get_symbol_info(symbol)
        if info is None:
            raise ValueError(f"Unknown symbol: {symbol}")

        step_size = tick_size = min_qty = min_notional = 0.0
        market_step_size = market_min_qty = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                min_qty = float(f["minQty"])
            elif f["filterType"] == "MARKET_LOT_SIZE":
                # Binance validates MARKET orders against this filter, not
                # LOT_SIZE, when both are present - it's often the same
                # values but not always, so prefer it when it exists.
                market_step_size = float(f["stepSize"])
                market_min_qty = float(f["minQty"])
            elif f["filterType"] == "PRICE_FILTER":
                tick_size = float(f["tickSize"])
            elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = float(f.get("minNotional") or f.get("notional") or 0)

        if market_step_size:
            step_size = market_step_size
        if market_min_qty:
            min_qty = market_min_qty

        sym_info = SymbolInfo(
            symbol=symbol, step_size=step_size, tick_size=tick_size,
            min_qty=min_qty, min_notional=min_notional,
        )
        self._symbol_info_cache[symbol] = sym_info
        return sym_info

    @retry(attempts=2, delay_seconds=3)
    def market_buy(self, symbol: str, quantity: float) -> dict:
        return self.client.create_order(
            symbol=symbol, side="BUY", type="MARKET", quantity=quantity,
        )

    @retry(attempts=2, delay_seconds=3)
    def market_sell(self, symbol: str, quantity: float) -> dict:
        return self.client.create_order(
            symbol=symbol, side="SELL", type="MARKET", quantity=quantity,
        )

    def place_protective_oco_sell(
        self,
        symbol: str,
        quantity: float,
        take_profit_price: float,
        stop_trigger_price: float,
        stop_limit_price: float,
    ) -> dict:
        """
        Places the actual exit on Binance itself: an OCO (one-cancels-
        the-other) sell order combining the take profit (a limit order
        above the market) and the stop loss (a stop-limit order below
        it). Whichever leg fills first, Binance cancels the other one
        automatically - the exchange enforces this, not our process.

        This is deliberately not wrapped in @retry: an OCO create call
        that times out client-side may or may not have gone through on
        Binance's end, and blindly retrying could place a second OCO on
        top of one that already exists. The caller (bot.py) checks
        get_open_orders for the symbol before deciding whether to retry
        or fall back to client-side monitoring.
        """
        return self.client.create_oco_order(
            symbol=symbol,
            side="SELL",
            quantity=quantity,
            aboveType="TAKE_PROFIT_LIMIT",
            abovePrice=f"{take_profit_price:.10f}".rstrip("0").rstrip("."),
            aboveStopPrice=f"{take_profit_price:.10f}".rstrip("0").rstrip("."),
            aboveTimeInForce="GTC",
            belowType="STOP_LOSS_LIMIT",
            belowPrice=f"{stop_limit_price:.10f}".rstrip("0").rstrip("."),
            belowStopPrice=f"{stop_trigger_price:.10f}".rstrip("0").rstrip("."),
            belowTimeInForce="GTC",
        )

    @retry(attempts=3, delay_seconds=2)
    def get_order_status(self, symbol: str, order_id: int) -> dict:
        return self.client.get_order(symbol=symbol, orderId=order_id)

    @retry(attempts=2, delay_seconds=3)
    def get_open_orders(self, symbol: str) -> list[dict]:
        return self.client.get_open_orders(symbol=symbol)

    @retry(attempts=2, delay_seconds=3)
    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self.client.cancel_order(symbol=symbol, orderId=order_id)

    def get_quote_balance(self, quote_asset: str = "USDT") -> float:
        balance = self.client.get_asset_balance(asset=quote_asset)
        return float(balance["free"]) if balance else 0.0
