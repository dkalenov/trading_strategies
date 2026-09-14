"""Health monitoring - heartbeat file, DB events, watchdog.

Ported in spirit from algofactory_bot/health.py. Docker healthcheck (or
systemd) can read heartbeat.tmp freshness to decide the process is alive.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import db

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path(__file__).resolve().parent / "heartbeat.tmp"

_run_identity: dict = {}


def set_run_identity(run_id: str, execution_mode: str = "dry_run") -> None:
    global _run_identity
    _run_identity = {"run_id": run_id, "execution_mode": execution_mode}


def touch_heartbeat() -> None:
    HEARTBEAT_PATH.write_text(str(time.time()))


async def health_watchdog(gateway=None, interval: int = 300) -> None:
    """Periodic health check - ping exchange (if live), record heartbeat."""
    while True:
        try:
            await asyncio.sleep(interval)
            touch_heartbeat()
            if gateway is not None:
                try:
                    await gateway.ping()
                    _record_health("INFO", "health", "ping OK")
                except Exception as e:
                    _record_health("ERROR", "health", f"ping failed: {e}")
            else:
                _record_health("INFO", "health", "no gateway - dry-run heartbeat")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Health watchdog error: %s", e)


def _record_health(severity: str, component: str, message: str) -> None:
    if not _run_identity:
        return
    try:
        db.save_health_event(_run_identity.get("run_id", ""), severity, component, message)
    except Exception as e:
        logger.debug("Health DB write skipped: %s", e)
