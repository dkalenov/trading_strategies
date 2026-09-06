"""
Places the two legs of a pair trade and tries to not end up naked if one
leg fails. Mirrors execution/order_manager.py in algofactory_bot in
spirit: retryable vs. permanent failures are treated differently, and a
failure partway through a multi-leg operation triggers an unwind rather
than being left as-is.
"""

from __future__ import annotations

import asyncio

from ..exchange import BinanceFuturesClient, PermanentOrderError

MAX_RETRIES = 3
RETRY_DELAY_SEC = 1.5


class OrderManager:
    def __init__(self, client: BinanceFuturesClient, dry_run: bool, logger):
        self.client = client
        self.dry_run = dry_run
        self.log = logger

    async def _place_with_retry(self, symbol: str, side: str, qty: float) -> dict:
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self.client.place_market_order(symbol, side, qty, self.dry_run)
            except PermanentOrderError:
                raise
            except Exception as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY_SEC * (attempt + 1))
        raise last_err

    async def open_pair(self, leg1: str, leg2: str, qty1_signed: float, qty2_signed: float) -> dict:
        """qty*_signed > 0 means buy/long that leg, < 0 means sell/short it."""
        side1 = "buy" if qty1_signed > 0 else "sell"
        side2 = "buy" if qty2_signed > 0 else "sell"

        order1 = await self._place_with_retry(leg1, side1, abs(qty1_signed))
        try:
            order2 = await self._place_with_retry(leg2, side2, abs(qty2_signed))
        except Exception as e:
            self.log.error(f"Leg 2 ({leg2}) failed after leg 1 ({leg1}) filled: {e}. Unwinding leg 1.")
            unwind_side = "sell" if side1 == "buy" else "buy"
            try:
                await self._place_with_retry(leg1, unwind_side, abs(qty1_signed))
            except Exception as e2:
                self.log.critical(
                    f"UNWIND FAILED for {leg1} after a failed second leg. "
                    f"Manual intervention required. Original error: {e}. Unwind error: {e2}"
                )
            raise

        return {"order1": order1, "order2": order2}

    async def close_pair(self, leg1: str, leg2: str, qty1_signed: float, qty2_signed: float) -> dict:
        """Closing orders are the opposite side of the currently held signed qty."""
        side1 = "sell" if qty1_signed > 0 else "buy"
        side2 = "sell" if qty2_signed > 0 else "buy"
        order1, order2 = await asyncio.gather(
            self._place_with_retry(leg1, side1, abs(qty1_signed)),
            self._place_with_retry(leg2, side2, abs(qty2_signed)),
        )
        return {"order1": order1, "order2": order2}

    async def place_protective_stops(self, leg1: str, leg2: str, qty1_signed: float,
                                      qty2_signed: float, price1: float, price2: float,
                                      leg_stop_loss_pct: float) -> None:
        """Best-effort circuit breaker on each leg. See exchange.py for why
        this doesn't try to replicate the pair's real (joint) exit logic.
        Failure here is logged, not raised - a missing safety net order
        should not undo an entry that already filled.
        """
        stop_side1 = "sell" if qty1_signed > 0 else "buy"
        stop_price1 = price1 * (1 - leg_stop_loss_pct) if qty1_signed > 0 else price1 * (1 + leg_stop_loss_pct)
        stop_side2 = "sell" if qty2_signed > 0 else "buy"
        stop_price2 = price2 * (1 - leg_stop_loss_pct) if qty2_signed > 0 else price2 * (1 + leg_stop_loss_pct)
        try:
            await asyncio.gather(
                self.client.place_protective_stop(leg1, stop_side1, abs(qty1_signed), stop_price1, self.dry_run),
                self.client.place_protective_stop(leg2, stop_side2, abs(qty2_signed), stop_price2, self.dry_run),
            )
        except Exception as e:
            self.log.warning(f"Could not place protective stop for {leg1}/{leg2}: {e}. "
                              f"Position is open without an exchange-side safety net until the next cycle.")
