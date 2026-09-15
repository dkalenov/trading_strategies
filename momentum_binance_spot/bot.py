"""
bot.py - the live trading loop.

Same rule as the original single-file script: buy the biggest 24h
gainer on Binance spot if its most recent price move is still
pointing up, exit at a fixed take-profit or stop-loss, repeat. What's
different is everything around that rule - see AUDIT.md for the full
list of what was actually broken in the original code. In short:

  - the original crashed on its first trade. The function argument is
    named `by_amt`, but the body uses `buy_amt`, a name that's never
    defined anywhere. That's an immediate NameError.
  - the entry check used `.loc[-1]` on a timestamp-indexed series,
    which looks for a row literally labeled -1. There isn't one. That
    line raises a KeyError every time it runs.
  - it imported api_key/api_secret from a `config` module that isn't
    in the project - there's nothing to import.
  - it never checked Binance's LOT_SIZE / MIN_NOTIONAL rules, so even
    a working version would have had orders rejected by the exchange
    on most symbols.
  - no logging, no persistence, no error handling around order calls,
    no way to know after a restart whether a position was still open.
  - the exit itself only ever existed as a price check inside the
    Python process. If the script died while holding a coin, the
    position sat there with no stop-loss at all until someone noticed
    and restarted it.

This version fixes all of that, including the last one: once a buy
fills, this bot places a real OCO (one-cancels-the-other) order on
Binance itself, combining the take profit and the stop loss into a
single exchange-side order pair. If this process crashes, the position
is still protected, because Binance is the one watching it, not this
script. See "How the exit actually works" below.

None of this changes what the strategy does - it's still "chase the
top gainer" - read README.md before pointing real money at it.

Run with DRY_RUN=true (the default - see .env.example) to watch it
make decisions without sending real orders.

## How the exit actually works

After a buy fills, the bot places a SELL OCO: a take-profit limit
order above the market and a stop-loss stop-limit order below it,
submitted together. Binance holds both. Whichever price level is
reached first, Binance fills that leg and cancels the other one
automatically - this bot doesn't have to be running for that to
happen.

The stop-limit leg's actual limit price is set slightly below the
trigger price (see STOP_LIMIT_BUFFER_PCT below), because a bare
stop-limit order with limit == trigger can fail to fill at all in a
fast-moving market: once triggered it becomes a limit order, and if
price has already gapped past that exact limit, it just sits unfilled.
A small buffer gives it room to actually execute. This is a real
tradeoff, not a free fix - too small a buffer and it still might not
fill in a violent enough move; too large and you give back some of the
1.5% stop budget as slippage. The default (0.1%) is a reasonable
middle ground, not a guarantee.

If placing the OCO fails for any reason (a Binance filter rejects the
prices, a network error, whatever), the bot logs that clearly and
falls back to watching the price itself and sending a market sell when
a threshold is crossed - the same behavior as before this feature
existed. That fallback position has no exchange-side protection until
it closes, and the log says so.
"""
from __future__ import annotations

import argparse
import time

from binance.exceptions import BinanceAPIException, BinanceOrderException

import db
from config import load_exchange_config, load_strategy_config
from exchange import BinanceGateway
from risk import compute_position_size, round_price_down
from strategy import build_signal, check_exit, momentum_confirmed, pick_top_gainer
from utils import setup_logger

logger = setup_logger("bot")

# How far below the stop trigger price to set the stop-limit leg's
# actual limit price. See "How the exit actually works" above.
STOP_LIMIT_BUFFER_PCT = 0.001

# An OCO order's two legs report status independently. If both end up
# in one of these terminal, non-filled states at the same time (for
# example: someone cancelled the order manually from the Binance app),
# the position is no longer protected by anything and the bot needs to
# fall back to watching the price itself.
_OCO_TERMINAL_UNFILLED = {"CANCELED", "EXPIRED", "REJECTED"}

# How many top-gainer candidates to try before giving up on entry for
# this poll cycle. Each candidate that lacks kline data gets skipped and
# the next one is tried, so the bot doesn't stall on illiquid symbols.
MAX_ENTRY_ATTEMPTS = 10


def _avg_fill_price(order: dict) -> float:
    executed = float(order.get("executedQty") or 0)
    cumm_quote = float(order.get("cummulativeQuoteQty") or 0)
    if executed > 0 and cumm_quote > 0:
        return cumm_quote / executed
    return float(order.get("price") or 0)


def _place_protective_oco(gateway, conn, buy_trade_id, symbol, quantity, signal, sym_info):
    """Places the real exit on Binance. Returns an oco dict on success,
    or None if it failed (caller falls back to client-side polling)."""
    take_profit_price = round_price_down(signal.take_profit, sym_info)
    stop_trigger_price = round_price_down(signal.stop_loss, sym_info)
    stop_limit_price = round_price_down(
        stop_trigger_price * (1 - STOP_LIMIT_BUFFER_PCT), sym_info
    )

    try:
        resp = gateway.place_protective_oco_sell(
            symbol, quantity, take_profit_price, stop_trigger_price, stop_limit_price,
        )
    except (BinanceAPIException, BinanceOrderException) as exc:
        logger.error(
            "Could not place protective OCO for %s: %s. Falling back to client-side "
            "price polling for this position - it has NO exchange-side stop until it closes.",
            symbol, exc,
        )
        return None

    logger.info("OCO response for %s: %s", symbol, resp)
    
    stop_order_id = limit_order_id = None
    for leg in resp.get("orderReports", resp.get("orders", [])):
        leg_type = leg.get("type", "")
        if leg_type in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
            stop_order_id = leg["orderId"]
        elif leg_type in ("TAKE_PROFIT_LIMIT", "LIMIT_MAKER", "TAKE_PROFIT"):
            limit_order_id = leg["orderId"]

    if stop_order_id is None or limit_order_id is None:
        logger.error(
            "OCO for %s placed but couldn't identify both legs from the response - "
            "falling back to client-side polling to be safe.", symbol,
        )
        return None

    order_list_id = resp.get("orderListId")
    db.attach_oco(conn, buy_trade_id, order_list_id=order_list_id,
                   stop_order_id=stop_order_id, limit_order_id=limit_order_id)
    logger.info(
        "Protective OCO live on Binance for %s: take profit %.8f, stop trigger %.8f "
        "(stop limit %.8f).",
        symbol, take_profit_price, stop_trigger_price, stop_limit_price,
    )
    return {"order_list_id": order_list_id, "stop_order_id": stop_order_id,
            "limit_order_id": limit_order_id}


def _poll_oco(gateway, symbol, oco):
    """Checks both OCO legs. Returns (fill_price, reason) once one has
    filled, 'OCO_LOST' if both ended up terminal without filling
    (needs a fallback), or None if still waiting."""
    stop_order = gateway.get_order_status(symbol, oco["stop_order_id"])
    if stop_order["status"] == "FILLED":
        return _avg_fill_price(stop_order), "stop_loss"

    limit_order = gateway.get_order_status(symbol, oco["limit_order_id"])
    if limit_order["status"] == "FILLED":
        return _avg_fill_price(limit_order), "take_profit"

    if stop_order["status"] in _OCO_TERMINAL_UNFILLED and limit_order["status"] in _OCO_TERMINAL_UNFILLED:
        return "OCO_LOST"

    return None


def run(config_path: str = "config.toml", db_path: str = "trades.db") -> None:
    strat_cfg = load_strategy_config(config_path)
    exch_cfg = load_exchange_config()
    conn = db.init_db(db_path)
    gateway = BinanceGateway(exch_cfg.api_key, exch_cfg.api_secret, exch_cfg.testnet)

    if exch_cfg.dry_run:
        logger.warning("DRY RUN mode: decisions are logged but no real orders are sent "
                        "(including no real OCO - exits are simulated from price checks).")

    signal = None
    holding_symbol = None
    buy_trade_id = None
    buy_qty = None
    oco = None  # dict once a protective OCO is live on Binance, else None

    open_pos = db.get_open_position(conn)
    if open_pos:
        logger.info("Recovered open position from a previous run: %s @ %.8f",
                     open_pos["symbol"], open_pos["price"])
        holding_symbol = open_pos["symbol"]
        buy_trade_id = open_pos["id"]
        buy_qty = open_pos["quantity"]
        signal = build_signal(open_pos["symbol"], open_pos["price"],
                               strat_cfg.take_profit_pct, strat_cfg.stop_loss_pct)
        if open_pos["oco_stop_order_id"] and open_pos["oco_limit_order_id"]:
            oco = {
                "order_list_id": open_pos["oco_order_list_id"],
                "stop_order_id": open_pos["oco_stop_order_id"],
                "limit_order_id": open_pos["oco_limit_order_id"],
            }
            logger.info("Resuming the protective OCO already live on Binance for %s.",
                         holding_symbol)
        else:
            logger.warning("No protective OCO recorded for this recovered position - "
                            "falling back to client-side price checks until it closes.")

    while True:
        try:
            if signal is None:
                tickers = gateway.get_tickers()
                changes = {t["symbol"]: float(t["priceChangePercent"]) for t in tickers}

                # Try the top gainers in order; skip any symbol whose klines
                # we can't fetch (common for low-volume alts that show big
                # pumps but have no 1m history). Up to MAX_ENTRY_ATTEMPTS
                # tries before we give up and sleep.
                top_symbol = None
                closes = None
                skipped: set[str] = set()
                for _attempt in range(MAX_ENTRY_ATTEMPTS):
                    candidate = pick_top_gainer(
                        {s: c for s, c in changes.items() if s not in skipped},
                        strat_cfg.quote_asset,
                    )
                    if candidate is None:
                        break
                    try:
                        klines = gateway.get_klines_df(
                            candidate, "1m", str(strat_cfg.lookback_minutes)
                        )
                        closes = klines["Close"].tolist()
                        top_symbol = candidate
                        break
                    except (ValueError, BinanceAPIException) as exc:
                        logger.warning(
                            "%s: cannot fetch klines (%s), skipping.",
                            candidate, exc,
                        )
                        skipped.add(candidate)

                if top_symbol is None:
                    logger.info("No eligible symbol with kline data found, sleeping.")
                    time.sleep(strat_cfg.poll_interval_sec)
                    continue

                if not momentum_confirmed(closes):
                    logger.info("%s is the top gainer but recent momentum is negative, skipping.",
                                top_symbol)
                    time.sleep(strat_cfg.poll_interval_sec)
                    continue

                entry_price = closes[-1]
                sym_info = gateway.get_symbol_info(top_symbol)
                sizing = compute_position_size(strat_cfg.alloc_quote, entry_price, sym_info)
                if sizing is None:
                    logger.warning("%s: order would be below exchange minimums, skipping.",
                                    top_symbol)
                    time.sleep(strat_cfg.poll_interval_sec)
                    continue

                logger.info("BUY %s qty=%s price=%.8f", top_symbol, sizing.quantity, entry_price)
                if exch_cfg.dry_run:
                    fill_price = entry_price
                else:
                    order = gateway.market_buy(top_symbol, sizing.quantity)
                    fill_price = float(order["fills"][0]["price"])

                buy_trade_id = db.log_trade(
                    conn, symbol=top_symbol, side="BUY",
                    quantity=sizing.quantity, price=fill_price, reason="momentum entry",
                )
                buy_qty = sizing.quantity
                holding_symbol = top_symbol
                signal = build_signal(top_symbol, fill_price,
                                       strat_cfg.take_profit_pct, strat_cfg.stop_loss_pct)
                oco = None

                if exch_cfg.dry_run:
                    logger.info(
                        "(dry run) would place protective OCO: take profit %.8f, stop %.8f",
                        signal.take_profit, signal.stop_loss,
                    )
                else:
                    oco = _place_protective_oco(
                        gateway, conn, buy_trade_id, top_symbol, sizing.quantity, signal, sym_info,
                    )

            elif oco is not None:
                result = _poll_oco(gateway, holding_symbol, oco)

                if result is None:
                    time.sleep(strat_cfg.poll_interval_sec)
                    continue

                if result == "OCO_LOST":
                    logger.error(
                        "The protective OCO for %s is gone (both legs cancelled/expired/"
                        "rejected) without filling - falling back to client-side price "
                        "checks for the rest of this position.", holding_symbol,
                    )
                    oco = None
                    continue

                last_price, exit_reason = result
                logger.info("EXIT %s reason=%s price=%.8f (filled by Binance OCO)",
                            holding_symbol, exit_reason, last_price)
                db.log_trade(
                    conn, symbol=holding_symbol, side="SELL",
                    quantity=buy_qty, price=last_price,
                    reason=exit_reason, opened_trade_id=buy_trade_id,
                )
                signal = None
                holding_symbol = None
                oco = None

            else:
                # No live OCO protecting this position - either we're in
                # dry run, or a real OCO failed/was lost and we fell back.
                klines = gateway.get_klines_df(holding_symbol, "1m", "2")
                last_price = float(klines["Close"].iloc[-1])
                exit_reason = check_exit(last_price, signal)
                logger.info("%s price=%.8f tp=%.8f sl=%.8f",
                            holding_symbol, last_price, signal.take_profit, signal.stop_loss)

                if exit_reason is None:
                    time.sleep(5)
                    continue

                logger.info("EXIT %s reason=%s price=%.8f", holding_symbol, exit_reason, last_price)
                if not exch_cfg.dry_run:
                    gateway.market_sell(holding_symbol, buy_qty)

                db.log_trade(
                    conn, symbol=holding_symbol, side="SELL",
                    quantity=buy_qty, price=last_price,
                    reason=exit_reason, opened_trade_id=buy_trade_id,
                )
                signal = None
                holding_symbol = None

            time.sleep(strat_cfg.poll_interval_sec)

        except BinanceAPIException as exc:
            logger.error("Binance API error: %s", exc)
            time.sleep(strat_cfg.poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception:
            logger.exception("Unexpected error, sleeping before retry.")
            time.sleep(strat_cfg.poll_interval_sec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Momentum spot bot - live trading loop")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--db", default="trades.db")
    args = parser.parse_args()
    run(args.config, args.db)
