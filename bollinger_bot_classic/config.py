"""Frozen typed config built from DB-backed ConfigInfo.

BotConfig is the single source of validation and config_hash for the bot.
ConfigInfo (db.py) is the raw DB load; BotConfig is the validated, frozen,
immutable representation used by all downstream modules.

Ported 1:1 from algofactory_bot/config.py. Deviation from the reference:
the reference stores runtime config in PostgreSQL; this bot uses SQLite
(db.py) since a Postgres server isn't part of this deliverable. The
validation contract and field set are unchanged.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from db import ConfigInfo

# === Constants ============================================================
_VALID_EXECUTION_MODES = frozenset({"dry_run", "testnet", "live"})
_VALID_EXCHANGE_ENVS = frozenset({"binance_usdm_prod", "binance_usdm_testnet", "backtest"})


def _get_valid_strategy_kinds() -> frozenset[str]:
    """Dynamic: valid kinds come from ADAPTER_REGISTRY (avoids hardcoding)."""
    from strategies.registry import ADAPTER_REGISTRY
    return frozenset(ADAPTER_REGISTRY.keys())


_VALID_INTERVALS = frozenset({"1m", "5m", "15m", "30m", "1h", "4h"})

_STRAT_RE = re.compile(r"^STRAT-[A-Z0-9][A-Z0-9-]*$")
_HYP_RE = re.compile(r"^HYP-[0-9]{4}$")

# === Config hash: only strategy-impacting fields ==========================
_HASH_FIELDS = (
    "interval",
    "strategy_kind",
    "risk_per_trade_pct",
    "leverage",
    "max_positions",
    "max_symbol_notional_pct",
    "stop_atr",
    "take1_atr",
    "take2_atr",
    "strategy_params",
    "spec_version",
    "strategy_id",
    "hypothesis_id",
)


class ConfigError(ValueError):
    """Raised on invalid config values."""


# === BotConfig ============================================================
@dataclass(frozen=True, slots=True)
class BotConfig:
    """Validated, immutable bot configuration.

    Built exclusively from ``ConfigInfo`` via ``from_config_info()``.
    """

    # Run-identity
    execution_mode: str
    exchange_env: str
    strategy_id: str
    hypothesis_id: str
    spec_version: int
    new_entries_enabled: bool

    # Dual-mode
    strategy_kind: str
    universe_profile: str
    interval: str

    # Risk / sizing (ATR-based, mirrors reference risk.py)
    risk_per_trade_pct: float
    leverage: int
    max_positions: int
    max_symbol_notional_pct: float
    stop_atr: float
    take1_atr: float
    take2_atr: float

    # Strategy-specific parameters (from [strategy] TOML section)
    # Access via bot_config.strategy_params.get("key", default) - never getattr.
    strategy_params: Mapping[str, object]

    # Dev flags (NOT included in config_hash)
    manual_equity: float
    debug_mode: bool

    # === Factory ============================================================
    @classmethod
    def from_config_info(cls, conf: ConfigInfo) -> BotConfig:
        """Build BotConfig with full validation. Raises ConfigError on failure."""
        _validate_str("execution_mode", conf.execution_mode, _VALID_EXECUTION_MODES)
        _validate_str("exchange_env", conf.exchange_env, _VALID_EXCHANGE_ENVS)
        _validate_str("strategy_kind", conf.strategy_kind, _get_valid_strategy_kinds())
        _validate_str("interval", conf.interval, _VALID_INTERVALS)

        if not _STRAT_RE.match(conf.strategy_id):
            raise ConfigError(
                f"strategy_id must match ^STRAT-[A-Z0-9][A-Z0-9-]*$, got {conf.strategy_id!r}"
            )
        if not _HYP_RE.match(conf.hypothesis_id):
            raise ConfigError(
                f"hypothesis_id must match ^HYP-[0-9]{{4}}$, got {conf.hypothesis_id!r}"
            )

        _validate_int_range("spec_version", conf.spec_version, min_val=1)
        _validate_int_range("leverage", conf.leverage, min_val=1, max_val=125)
        _validate_int_range("max_positions", conf.max_positions, min_val=1)

        _validate_float_range(
            "risk_per_trade_pct", conf.risk_per_trade_pct, min_val=0.0, exclusive_min=True,
        )
        _validate_float_range(
            "max_symbol_notional_pct", conf.max_symbol_notional_pct, min_val=0.0, max_val=1.0,
        )
        _validate_float_range("stop_atr", conf.stop_atr, min_val=0.0, exclusive_min=True)
        _validate_float_range("take1_atr", conf.take1_atr, min_val=0.0, exclusive_min=True)
        _validate_float_range("take2_atr", conf.take2_atr, min_val=0.0, exclusive_min=True)

        strategy_params = dict(conf.strategy_params) if conf.strategy_params else {}

        return cls(
            execution_mode=conf.execution_mode,
            exchange_env=conf.exchange_env,
            strategy_id=conf.strategy_id,
            hypothesis_id=conf.hypothesis_id,
            spec_version=conf.spec_version,
            new_entries_enabled=conf.new_entries_enabled,
            strategy_kind=conf.strategy_kind,
            universe_profile=conf.universe_profile,
            interval=conf.interval,
            risk_per_trade_pct=conf.risk_per_trade_pct,
            leverage=conf.leverage,
            max_positions=conf.max_positions,
            max_symbol_notional_pct=conf.max_symbol_notional_pct,
            stop_atr=conf.stop_atr,
            take1_atr=conf.take1_atr,
            take2_atr=conf.take2_atr,
            strategy_params=strategy_params,
            manual_equity=conf.manual_equity,
            debug_mode=conf.debug_mode,
        )

    # === Config hash =========================================================
    @property
    def config_hash(self) -> str:
        """Deterministic sha256 hash of strategy-impacting parameters."""
        parts = []
        for field in _HASH_FIELDS:
            val = getattr(self, field)
            if isinstance(val, dict):
                val_str = "|".join(f"{k}={v}" for k, v in sorted(val.items()))
            else:
                val_str = str(val)
            parts.append(f"{field}={val_str}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# === Validation helpers ====================================================
def _validate_str(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {sorted(allowed)}, got {value!r}")


def _validate_int_range(name: str, value: int, *, min_val: int = 0, max_val: int | None = None) -> None:
    if value < min_val:
        raise ConfigError(f"{name} must be >= {min_val}, got {value}")
    if max_val is not None and value > max_val:
        raise ConfigError(f"{name} must be <= {max_val}, got {value}")


def _validate_float_range(
    name: str, value: float, *, min_val: float = 0.0, max_val: float | None = None,
    exclusive_min: bool = False,
) -> None:
    if exclusive_min and value <= min_val:
        raise ConfigError(f"{name} must be > {min_val}, got {value}")
    if not exclusive_min and value < min_val:
        raise ConfigError(f"{name} must be >= {min_val}, got {value}")
    if max_val is not None and value > max_val:
        raise ConfigError(f"{name} must be <= {max_val}, got {value}")
