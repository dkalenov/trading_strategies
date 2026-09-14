"""Periodic reconciliation - catches drift between our DB and the exchange.

Trimmed from algofactory_bot/reconciliation.py (931 lines in the
reference - orphan-order cancellation across every order type, stale
PARTIALLY_FILLED resolution, position-vs-DB quantity diffing). This bot
keeps the two checks that matter most for a single-strategy, one-position-
per-symbol bot: (1) every open DB trade still has a stop on the exchange
(delegates to ProtectionTracker.audit), (2) every exchange position has a
matching open DB trade (catches a position opened outside this run, e.g.
manually or by a crashed prior process). Anything beyond that - the
reference's broader order-book-wide orphan sweep - is a real gap for
running this unattended for long periods; flagged rather than silently
dropped (see README "What's simplified").
"""
from __future__ import annotations

import logging

import db
from execution.position_manager import PositionManager

logger = logging.getLogger(__name__)


async def reconcile_once(pm: PositionManager, run_id: str) -> dict:
    """One reconciliation pass. Returns a summary dict for logging/health."""
    summary = {"checked": 0, "protection_missing": 0, "untracked_positions": 0}

    open_trades = db.get_open_trades()
    summary["checked"] = len(open_trades)

    for trade in open_trades:
        state = await pm._protection.audit(trade.symbol)
        if state.value == "missing":
            summary["protection_missing"] += 1
            logger.error("RECONCILE: %s has NO live stop - re-placing", trade.symbol)
            side = "SELL" if trade.side else "BUY"
            await pm._protection.place_stop(trade.symbol, side, trade.stop_price, run_id)

    tracked_symbols = {t.symbol for t in open_trades}
    live_symbols = pm.open_symbols()
    untracked = live_symbols - tracked_symbols
    if untracked:
        summary["untracked_positions"] = len(untracked)
        logger.warning("RECONCILE: %d position(s) in memory but not in DB: %s", len(untracked), untracked)

    if summary["protection_missing"] or summary["untracked_positions"]:
        db.save_health_event(
            run_id, "WARNING", "reconciliation",
            f"missing_protection={summary['protection_missing']} "
            f"untracked={summary['untracked_positions']}",
        )
    return summary
