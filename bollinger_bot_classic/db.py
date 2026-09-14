"""Persistence layer - SQLite via SQLAlchemy 2.0 (sync).

Ported in spirit from algofactory_bot/db.py. Deviation from the reference:
the reference runs PostgreSQL 18 via asyncpg + SQLAlchemy async, with
run_id as a NOT NULL column shared across every table (RunIdentityMixin).
This bot uses SQLite + sync SQLAlchemy instead, because a Postgres server
isn't part of this deliverable and a GitHub prototype should run with
zero external services. The table shapes, column names and CRUD contract
(save_trade/update_trade/close_trade/get_config/...) are otherwise the
same, so strategies/risk/execution code written against this module reads
the same as it would against the reference.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, Integer, BigInteger, Numeric, Float, String, Text,
    create_engine, select,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# === Tables ================================================================

class Config(Base):
    """Single-row runtime config. DB is the source of truth (see config.py)."""
    __tablename__ = "config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(Text, default="")


class RunManifest(Base):
    __tablename__ = "run_manifest"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False)
    started_at = Column(BigInteger, default=0)
    execution_mode = Column(String(16), default="dry_run")
    exchange_env = Column(String(32), default="backtest")
    strategy_kind = Column(String(64), default="")
    config_hash = Column(String(32), default="")


class Trade(Base):
    """One position lifecycle from entry to close."""
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    symbol = Column(String(32), nullable=False)
    side = Column(Boolean, nullable=False)  # True=LONG
    interval = Column(String(8), default="4h")
    position_open = Column(Boolean, default=True)
    status = Column(String(32), default="NEW")

    entry_price = Column(Numeric(20, 8), default=0)
    quantity = Column(Numeric(20, 8), default=0)
    stop_price = Column(Numeric(20, 8), default=0)
    take_price = Column(Numeric(20, 8), default=0)  # dynamic middle-band target, informational

    atr = Column(Numeric(20, 8), default=0)
    leverage = Column(Integer, default=20)
    order_size = Column(Numeric(20, 8), default=0)  # notional USDT

    bars_held = Column(Integer, default=0)
    open_time = Column(BigInteger, default=0)
    close_time = Column(BigInteger, default=0)

    exit_reason = Column(String(32), default="")
    result = Column(Float, default=0)  # realized PnL, quote currency
    r_multiple = Column(Float, default=0)
    initial_risk = Column(Float, default=0)
    risk_multiplier = Column(Float, default=1.0)
    signal_score = Column(Float, default=0)


class Order(Base):
    """Individual order (entry, stop, close)."""
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    order_id = Column(BigInteger, unique=True, nullable=False, index=True)
    trade_id = Column(Integer, nullable=True)
    client_order_id = Column(String(64), nullable=True)
    symbol = Column(String(32), nullable=False)
    time = Column(BigInteger, default=0)
    side = Column(Boolean, nullable=False)  # True=BUY
    type = Column(String(16), nullable=False)  # MARKET, STOP_MARKET
    status = Column(String(16), default="NEW")
    reduce = Column(Boolean, default=False)
    price = Column(Numeric(20, 8), default=0)
    quantity = Column(Numeric(20, 8), default=0)
    realized_profit = Column(Numeric(20, 8), default=0)
    raw_payload = Column(Text, default="")


class OrderIntentRow(Base):
    """Persisted BEFORE submission - audit trail, deterministic client_order_id."""
    __tablename__ = "order_intents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    intent_id = Column(String(64), unique=True, nullable=False, index=True)
    client_order_id = Column(String(64), nullable=False)
    trade_id = Column(Integer, nullable=True)
    symbol = Column(String(32), nullable=False)
    side = Column(Boolean, nullable=False)
    type = Column(String(16), nullable=False)
    reduce = Column(Boolean, default=False)
    price = Column(Numeric(20, 8), default=0)
    quantity = Column(Numeric(20, 8), default=0)
    stop_price = Column(Numeric(20, 8), nullable=True)
    reason = Column(String(256), default="")
    status = Column(String(16), default="pending")


class Fill(Base):
    __tablename__ = "fills"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    fill_id = Column(String(64), unique=True, nullable=False)
    order_id = Column(BigInteger, nullable=False, index=True)
    trade_id = Column(Integer, nullable=True)
    symbol = Column(String(32), nullable=False)
    time = Column(BigInteger, default=0)
    side = Column(Boolean, nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    realized_profit = Column(Numeric(20, 8), default=0)
    commission = Column(Numeric(20, 8), default=0)


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    symbol = Column(String(32), nullable=False, index=True)
    side = Column(Boolean, nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    unrealized_pnl = Column(Numeric(20, 8), default=0)
    leverage = Column(Integer, default=20)
    status = Column(String(16), default="open")


class ProtectionOrder(Base):
    __tablename__ = "protection_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    order_id = Column(BigInteger, nullable=True)
    client_order_id = Column(String(64), nullable=True)
    trade_id = Column(Integer, nullable=True)
    symbol = Column(String(32), nullable=False)
    type = Column(String(16), nullable=False)
    side = Column(Boolean, nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    status = Column(String(16), default="pending")
    role = Column(String(16), default="")


class SignalRow(Base):
    """Signal audit trail (every draft, approved or not)."""
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    time = Column(BigInteger, default=0)
    symbol = Column(String(32), nullable=False)
    direction = Column(Integer, default=0)
    score = Column(Float, default=0)
    approved = Column(Boolean, default=False)
    reason = Column(String(256), default="")


class EquityLog(Base):
    __tablename__ = "equity_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    time = Column(BigInteger, default=0)
    equity = Column(Numeric(20, 8), default=0)


class HealthEvent(Base):
    __tablename__ = "health_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    time = Column(BigInteger, default=0)
    severity = Column(String(16), default="INFO")
    component = Column(String(32), default="")
    message = Column(Text, default="")


class BacktestRun(Base):
    """Summary row per backtest invocation - engine.py writes one of these."""
    __tablename__ = "backtest_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), default="")
    symbol = Column(String(32), default="")
    strategy_kind = Column(String(64), default="")
    params_json = Column(Text, default="{}")
    total_trades = Column(Integer, default=0)
    total_return_pct = Column(Float, default=0)
    sharpe_ratio = Column(Float, default=0)
    win_rate = Column(Float, default=0)
    max_drawdown_pct = Column(Float, default=0)
    profit_factor = Column(Float, default=0)
    created_at = Column(BigInteger, default=0)


# === Engine / session ======================================================

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "bollinger_bot.db"
_state: dict = {"engine": None, "sessionmaker": None}


def init(db_path: str | Path | None = None) -> None:
    """Create engine + tables. Call once at startup (main.py / backtest CLI)."""
    path = Path(db_path) if db_path else _DEFAULT_DB_PATH
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    _state["engine"] = engine
    _state["sessionmaker"] = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    logger.info("db.init: sqlite at %s", path)


def _ensure_init() -> None:
    if _state["sessionmaker"] is None:
        init()


def get_session() -> Session:
    _ensure_init()
    return _state["sessionmaker"]()


def close() -> None:
    if _state["engine"] is not None:
        _state["engine"].dispose()
    _state["engine"] = None
    _state["sessionmaker"] = None


# ============================================================
# ConfigInfo (typed, from DB - source of truth)
# ============================================================

_CONFIG_DEFAULTS: dict = {
    "execution_mode": "dry_run",
    "exchange_env": "backtest",
    "strategy_id": "STRAT-BB-REVERSION",
    "hypothesis_id": "HYP-0035",
    "spec_version": 1,
    "new_entries_enabled": True,
    "strategy_kind": "bollinger_reversion",
    "universe_profile": "default",
    "interval": "4h",
    "risk_per_trade_pct": 0.01,
    "leverage": 5,
    "max_positions": 5,
    "max_symbol_notional_pct": 0.20,
    "stop_atr": 1.5,
    "take1_atr": 2.0,
    "take2_atr": 3.0,
    "strategy_params": {"bb_period": 20, "bb_std": 2.0, "exit_at_middle": True},
    "manual_equity": 10000.0,
    "debug_mode": False,
}


class ConfigInfo:
    """All bot config loaded from DB. Single source of truth."""

    def __init__(self, data: dict):
        merged = dict(_CONFIG_DEFAULTS)
        merged.update(data or {})
        raw_sp = merged.get("strategy_params")
        if isinstance(raw_sp, str):
            try:
                merged["strategy_params"] = json.loads(raw_sp)
            except (json.JSONDecodeError, TypeError):
                merged["strategy_params"] = {}
        for key, value in merged.items():
            setattr(self, key, value)


def ensure_defaults() -> None:
    """Seed the config table with defaults if empty (first run)."""
    _ensure_init()
    with get_session() as s:
        existing = s.execute(select(Config)).scalars().first()
        if existing is not None:
            return
        for key, value in _CONFIG_DEFAULTS.items():
            val = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            s.add(Config(key=key, value=val))
        s.commit()
    logger.info("db.ensure_defaults: seeded %d config keys", len(_CONFIG_DEFAULTS))


def get_config() -> ConfigInfo:
    """Load current config from DB, falling back to defaults for missing keys."""
    _ensure_init()
    with get_session() as s:
        rows = s.execute(select(Config)).scalars().all()
    data: dict = {}
    for row in rows:
        default = _CONFIG_DEFAULTS.get(row.key)
        if isinstance(default, bool):
            data[row.key] = row.value.lower() in ("1", "true", "yes")
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                data[row.key] = int(row.value)
            except ValueError:
                data[row.key] = default
        elif isinstance(default, float):
            try:
                data[row.key] = float(row.value)
            except ValueError:
                data[row.key] = default
        elif isinstance(default, dict):
            try:
                data[row.key] = json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                data[row.key] = default
        else:
            data[row.key] = row.value
    return ConfigInfo(data)


def update_config(**kwargs) -> None:
    """Hot-reload write path: upsert config keys."""
    _ensure_init()
    with get_session() as s:
        for key, value in kwargs.items():
            val = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            row = s.execute(select(Config).where(Config.key == key)).scalars().first()
            if row is None:
                s.add(Config(key=key, value=val))
            else:
                row.value = val
        s.commit()


# === Trade CRUD =============================================================

def save_trade(**fields) -> int:
    _ensure_init()
    with get_session() as s:
        t = Trade(**fields)
        s.add(t)
        s.commit()
        return t.id


def update_trade(trade_id: int, **fields) -> None:
    _ensure_init()
    with get_session() as s:
        t = s.get(Trade, trade_id)
        if t is None:
            logger.warning("update_trade: trade_id=%s not found", trade_id)
            return
        for k, v in fields.items():
            setattr(t, k, v)
        s.commit()


def close_trade(trade_id: int, close_time: int, exit_reason: str, result: float,
                 r_multiple: float = 0.0) -> None:
    update_trade(
        trade_id, position_open=False, status="CLOSED", close_time=close_time,
        exit_reason=exit_reason, result=result, r_multiple=r_multiple,
    )


def get_open_trades(symbol: str | None = None) -> list[Trade]:
    _ensure_init()
    with get_session() as s:
        stmt = select(Trade).where(Trade.position_open.is_(True))
        if symbol:
            stmt = stmt.where(Trade.symbol == symbol)
        return list(s.execute(stmt).scalars().all())


def get_last_trade_close_time(symbol: str) -> int:
    _ensure_init()
    with get_session() as s:
        stmt = (
            select(Trade.close_time)
            .where(Trade.symbol == symbol, Trade.position_open.is_(False))
            .order_by(Trade.close_time.desc())
        )
        row = s.execute(stmt).scalars().first()
        return int(row) if row else 0


def save_order(**fields) -> int:
    _ensure_init()
    with get_session() as s:
        o = Order(**fields)
        s.add(o)
        s.commit()
        return o.id


def save_fill(**fields) -> int:
    _ensure_init()
    with get_session() as s:
        f = Fill(**fields)
        s.add(f)
        s.commit()
        return f.id


def save_signal(**fields) -> int:
    _ensure_init()
    with get_session() as s:
        row = SignalRow(**fields)
        s.add(row)
        s.commit()
        return row.id


def save_equity_point(run_id: str, ts: int, equity: float) -> None:
    _ensure_init()
    with get_session() as s:
        s.add(EquityLog(run_id=run_id, time=ts, equity=equity))
        s.commit()


def save_health_event(run_id: str, severity: str, component: str, message: str) -> None:
    _ensure_init()
    with get_session() as s:
        s.add(HealthEvent(run_id=run_id, time=int(time.time()), severity=severity,
                           component=component, message=message))
        s.commit()


def save_backtest_run(**fields) -> int:
    _ensure_init()
    with get_session() as s:
        row = BacktestRun(**fields)
        s.add(row)
        s.commit()
        return row.id
