"""
config.py - typed, validated configuration.

Strategy parameters live in config.toml (checked into git, no
secrets, safe to share). API keys live in environment variables via
.env (never checked into git - see .gitignore). The original bot had
neither: no config file, and it imported `api_key, api_secret` from a
`config` module that doesn't exist anywhere in the zip it shipped in,
so it couldn't even start.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; plain env vars still work


class ConfigError(ValueError):
    """Raised when config.toml or the environment is missing something
    the bot needs to run safely."""


@dataclass(frozen=True)
class StrategyConfig:
    quote_asset: str
    lookback_minutes: int
    poll_interval_sec: int
    take_profit_pct: float
    stop_loss_pct: float
    alloc_quote: float
    max_positions: int


@dataclass(frozen=True)
class ExchangeConfig:
    api_key: str
    api_secret: str
    testnet: bool
    dry_run: bool


def load_strategy_config(path: str | Path = "config.toml") -> StrategyConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("rb") as f:
        raw = tomllib.load(f)

    strat = raw.get("strategy", {})
    risk = raw.get("risk", {})

    try:
        return StrategyConfig(
            quote_asset=strat["quote_asset"],
            lookback_minutes=int(strat["lookback_minutes"]),
            poll_interval_sec=int(strat["poll_interval_sec"]),
            take_profit_pct=float(risk["take_profit_pct"]),
            stop_loss_pct=float(risk["stop_loss_pct"]),
            alloc_quote=float(risk["alloc_quote"]),
            max_positions=int(risk.get("max_positions", 1)),
        )
    except KeyError as exc:
        raise ConfigError(f"Missing config key in {path}: {exc}") from exc


def load_exchange_config() -> ExchangeConfig:
    dry_run = os.getenv("DRY_RUN", "true").strip().lower() in ("1", "true", "yes")
    testnet = os.getenv("BINANCE_TESTNET", "false").strip().lower() in ("1", "true", "yes")
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")

    if not dry_run and (not api_key or not api_secret):
        raise ConfigError(
            "BINANCE_API_KEY and BINANCE_API_SECRET must be set in .env "
            "unless DRY_RUN=true"
        )
    return ExchangeConfig(api_key=api_key, api_secret=api_secret, testnet=testnet, dry_run=dry_run)
