"""TOML config loading - universe, taxonomy, strategy params.

Ported in spirit from algofactory_bot/config_loader.py. In the reference,
this module is marked DEPRECATED for runtime config (DB owns that) but is
still the loader for deploy-time seed data: [universe], [taxonomy] and the
one-time DB seed from [strategy]/[risk]/[exit]/[run] on first run.
"""
from __future__ import annotations

import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TOML_PATH = Path(__file__).resolve().parent / "deploy" / "bot_config.toml"


def _load_toml(path: Path | None = None) -> dict:
    p = path or _DEFAULT_TOML_PATH
    if not p.exists():
        raise FileNotFoundError(f"bot_config.toml not found at {p}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def load_universe_from_config(path: Path | None = None) -> tuple[str, ...]:
    data = _load_toml(path)
    symbols = data.get("universe", {}).get("symbols", [])
    return tuple(sorted(symbols))


def load_taxonomy_from_config(path: Path | None = None) -> dict[str, frozenset[str]]:
    try:
        data = _load_toml(path)
    except FileNotFoundError:
        return {}
    taxonomy = data.get("taxonomy", {})
    return {cat: frozenset(syms) for cat, syms in taxonomy.items()}


def load_strategy_params(path: Path | None = None) -> dict:
    data = _load_toml(path)
    strat = dict(data.get("strategy", {}))
    strat.pop("kind", None)
    strat.pop("interval", None)
    return strat


def seed_from_toml_if_first_run(path: Path | None = None) -> None:
    """One-time DB seed from bot_config.toml [strategy]/[risk]/[exit]/[run].

    Reference note (DEVELOPMENT_PLAYBOOK anti-patterns): strategy_params
    must be upserted (on_conflict_do_update), not on_conflict_do_nothing -
    a frozen spec can change between deploys and the DB must pick it up.
    db.update_config() here always upserts, so re-running this is safe.
    """
    import db

    try:
        data = _load_toml(path)
    except FileNotFoundError:
        logger.warning("No bot_config.toml found - using DB/code defaults only")
        return

    strategy = data.get("strategy", {})
    risk = data.get("risk", {})
    exit_cfg = data.get("exit", {})
    run_cfg = data.get("run", {})

    updates: dict = {}
    if "kind" in strategy:
        updates["strategy_kind"] = strategy["kind"]
    if "interval" in strategy:
        updates["interval"] = strategy["interval"]
    updates["strategy_params"] = load_strategy_params(path)

    for key in ("risk_per_trade_pct", "leverage", "max_positions", "max_symbol_notional_pct"):
        if key in risk:
            updates[key] = risk[key]
    for key in ("stop_atr", "take1_atr", "take2_atr"):
        if key in exit_cfg:
            updates[key] = exit_cfg[key]
    for key in ("execution_mode", "exchange_env", "manual_equity", "debug_mode"):
        if key in run_cfg:
            updates[key] = run_cfg[key]

    db.update_config(**updates)
    logger.info("Seeded DB config from bot_config.toml (%d keys)", len(updates))
