"""Domain enums and data types for the execution pipeline.

Ported 1:1 in spirit from algofactory_bot/models.py. Enums define state
machines for orders/positions/protection. Dataclasses carry event payloads
between strategy -> risk -> execution. All exchange-boundary values use
Decimal; float is only used for derived indicator metrics (score, ATR,
entry_price in Signal) exactly as in the reference framework.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

# === Enums ================================================================


class ExecutionMode(StrEnum):
    DRY_RUN = "dry_run"
    TESTNET = "testnet"
    LIVE = "live"


class OrderState(StrEnum):
    INTENDED = "intended"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN_FILL = "unknown_fill"
    UNKNOWN = "unknown"


class PositionState(StrEnum):
    OPENING = "opening"
    OPEN = "open"
    PROTECTING = "protecting"
    PROTECTED = "protected"
    REDUCING = "reducing"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


class ProtectionState(StrEnum):
    UNPROTECTED = "unprotected"
    PLACING = "placing"
    PROTECTED = "protected"
    MODIFY_REQUESTED = "modify_requested"
    CANCEL_REQUESTED = "cancel_requested"
    MISSING = "missing"
    FAILED = "failed"
    EMERGENCY_CLOSING = "emergency_closing"
    CLOSED = "closed"


class OrderRole(StrEnum):
    """Bollinger reversion only needs ENTRY/STOP/CLOSE.

    TAKE1/TAKE2/PARTIAL/TRAIL kept for parity with the reference registry
    (RiskManager still computes take1/take2 as informational ATR targets)
    but the strategy's real exit is a dynamic middle-band CLOSE, not a
    resting take order - see strategies/bollinger_reversion.py docstring.
    """
    ENTRY = "entry"
    STOP = "stop"
    TAKE1 = "take1"
    TAKE2 = "take2"
    PARTIAL = "partial"
    TRAIL = "trail"
    CLOSE = "close"


# === Domain dataclasses ===================================================


@dataclass(frozen=True, slots=True)
class OrderIntentDraft:
    """Intent produced by strategy_adapter, before sizing / persistence."""
    symbol: str
    direction: int  # 1=LONG, -1=SHORT
    qty: Decimal | None
    role: OrderRole
    reason: str
    score: float = 0.5  # signal strength (0.25-0.70 range for adaptive sizing)


@dataclass(frozen=True, slots=True)
class Signal:
    """Approved signal ready for execution."""
    symbol: str
    direction: int
    score: float
    atr: float
    entry_price: float
    components: dict


@dataclass(frozen=True, slots=True)
class SignalRejection:
    """Signal that did not pass filters."""
    symbol: str
    direction: int
    reason: str


@dataclass(frozen=True, slots=True)
class OrderAccepted:
    order_id: int
    client_order_id: str
    status: str
    avg_price: float | None


@dataclass(frozen=True, slots=True)
class OrderRejected:
    client_order_id: str
    error_code: str | None
    message: str


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: int
    trade_id: int
    symbol: str
    side: str  # "BUY" / "SELL"
    price: Decimal
    quantity: Decimal
    realized_profit: Decimal
    commission: Decimal


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    symbol: str
    side: str  # "LONG" / "SHORT"
    quantity: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    leverage: int


@dataclass(frozen=True, slots=True)
class ProtectionSnapshot:
    symbol: str
    role: OrderRole
    state: ProtectionState
    price: Decimal | None
