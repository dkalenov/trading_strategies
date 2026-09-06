"""
Bot configuration, loaded from environment variables (.env file or real
env). Mirrors the role of config.py in algofactory_bot: one place that
reads settings and hands back a typed object, nothing else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


@dataclass
class BotConfig:
    # exchange / mode
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    use_testnet: bool = field(default_factory=lambda: _bool("USE_TESTNET", True))
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))
    disable_trading: bool = field(default_factory=lambda: _bool("DISABLE_TRADING", False))

    # universe / strategy params - defaults match the backtest in results/metrics.json
    universe_size: int = field(default_factory=lambda: _int("UNIVERSE_SIZE", 60))
    window: int = field(default_factory=lambda: _int("WINDOW", 200))
    rescan_step_bars: int = field(default_factory=lambda: _int("RESCAN_STEP_BARS", 30))
    max_half_life: float = field(default_factory=lambda: _float("MAX_HALF_LIFE", 200.0))
    beta_threshold: float = field(default_factory=lambda: _float("BETA_THRESHOLD", 0.1))
    z_entry: float = field(default_factory=lambda: _float("Z_ENTRY", 2.0))
    z_exit: float = field(default_factory=lambda: _float("Z_EXIT", 0.5))
    z_stop: float = field(default_factory=lambda: _float("Z_STOP", 4.0))
    max_holding_bars: int = field(default_factory=lambda: _int("MAX_HOLDING_BARS", 60))

    # sizing / risk
    capital_usd: float = field(default_factory=lambda: _float("CAPITAL_USD", 10_000.0))
    max_notional_per_pair: float = field(default_factory=lambda: _float("MAX_NOTIONAL_PER_PAIR", 0.05))
    max_loss_per_pair_pct: float = field(default_factory=lambda: _float("MAX_LOSS_PER_PAIR_PCT", 0.01))
    max_concurrent_pairs: int = field(default_factory=lambda: _int("MAX_CONCURRENT_PAIRS", 15))
    max_gross_exposure_pct: float = field(default_factory=lambda: _float("MAX_GROSS_EXPOSURE_PCT", 0.75))
    max_daily_loss_pct: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_PCT", 0.03))
    vol_lookback: int = field(default_factory=lambda: _int("VOL_LOOKBACK", 60))
    leg_stop_loss_pct: float = field(default_factory=lambda: _float("LEG_STOP_LOSS_PCT", 0.15))

    # execution
    fee_bps: float = field(default_factory=lambda: _float("FEE_BPS", 5.0))
    slippage_bps: float = field(default_factory=lambda: _float("SLIPPAGE_BPS", 5.0))
    min_pair_gap_bars: int = field(default_factory=lambda: _int("MIN_PAIR_GAP_BARS", 6))

    # misc
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "4h"))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "bot_state.sqlite3"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def validate_for_live_trading(self) -> None:
        if self.dry_run:
            return
        if not self.binance_api_key or not self.binance_api_secret:
            raise ValueError(
                "DRY_RUN is false but BINANCE_API_KEY / BINANCE_API_SECRET are not set. "
                "Refusing to start in live order-placing mode without credentials."
            )
