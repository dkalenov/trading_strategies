"""Exchange gateway - thin abstraction over binance.Futures.

Ported in spirit from algofactory_bot/gateway.py. Everything above this
layer (execution/, marketdata/, main.py) talks to Gateway, never to
binance.Futures directly, so swapping exchanges/testnet-vs-prod is a
constructor argument, not a code change.
"""
from __future__ import annotations

import logging
import os

from binance.futures import Futures
from filters import SymbolInfo, build_symbol_info_map
from utils import klines_to_dataframe

logger = logging.getLogger(__name__)


class Gateway:
    """Binance USD-M Futures gateway - market data + trading."""

    def __init__(self, api_key: str = "", secret_key: str = "", testnet: bool = False):
        self._client = Futures(api_key=api_key, secret_key=secret_key, testnet=testnet)
        self._symbol_info_cache: dict[str, SymbolInfo] | None = None

    @classmethod
    def from_env(cls, testnet: bool = True) -> "Gateway":
        return cls(
            api_key=os.environ.get("BINANCE_API_KEY", ""),
            secret_key=os.environ.get("BINANCE_API_SECRET", ""),
            testnet=testnet,
        )

    async def ping(self) -> bool:
        await self._client.ping()
        return True

    async def get_symbol_infos(self, force_refresh: bool = False) -> dict[str, SymbolInfo]:
        if self._symbol_info_cache is None or force_refresh:
            info = await self._client.exchange_info()
            self._symbol_info_cache = build_symbol_info_map(info)
        return self._symbol_info_cache

    async def get_klines_df(self, symbol: str, interval: str, limit: int = 500):
        raw = await self._client.klines(symbol, interval, limit=limit)
        return klines_to_dataframe(raw)

    async def get_mark_price(self, symbol: str) -> float:
        data = await self._client.mark_price(symbol)
        return float(data["markPrice"])

    async def get_position(self, symbol: str) -> dict | None:
        rows = await self._client.position_risk(symbol)
        for row in rows:
            if float(row.get("positionAmt", 0)) != 0:
                return row
        return None

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._client.change_leverage(symbol, leverage)

    async def submit_market_order(self, symbol: str, side: str, quantity: str,
                                   client_order_id: str, reduce_only: bool = False) -> dict:
        return await self._client.new_order(
            symbol=symbol, side=side, order_type="MARKET", quantity=quantity,
            reduce_only=reduce_only, new_client_order_id=client_order_id,
        )

    async def submit_stop_market(self, symbol: str, side: str, stop_price: str,
                                  client_order_id: str, close_position: bool = True) -> dict:
        return await self._client.new_order(
            symbol=symbol, side=side, order_type="STOP_MARKET", stop_price=stop_price,
            close_position=close_position, new_client_order_id=client_order_id,
        )

    async def cancel_order(self, symbol: str, order_id: int | None = None,
                            client_order_id: str | None = None) -> dict:
        return await self._client.cancel_order(symbol, order_id=order_id,
                                                 orig_client_order_id=client_order_id)

    async def cancel_all_open_orders(self, symbol: str) -> dict:
        return await self._client.cancel_all_open_orders(symbol)

    async def open_orders(self, symbol: str | None = None) -> list:
        return await self._client.open_orders(symbol)

    async def close(self) -> None:
        await self._client.close()
