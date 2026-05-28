#!/usr/bin/env python3
"""
Run every evening after US market close.
Manual usage: python scripts/run_daily.py
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import settings
from src import compute, model, monitor, output, pull, screen

from src.pull import last_trading_day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("run_daily")


def main() -> None:
    if not settings.POLYGON_API_KEY or not settings.FRED_API_KEY:
        raise SystemExit("ERROR: POLYGON_API_KEY and FRED_API_KEY must be set in .env")

    as_of = last_trading_day().isoformat()
    log.info("Starting daily run for %s", as_of)

    pull.run(as_of)
    compute.run(as_of)

    candidates = screen.run(as_of)
    log.info("Stage A: %d candidates", len(candidates))

    signals = model.predict(candidates, as_of)
    actionable = [s for s in signals if s.signal not in ("WATCH", "NO_DIRECTION")]
    log.info("Stage B/C: %d signals", len(actionable))

    output.write(as_of, signals, candidates)
    log.info("Watchlist written: watchlists/%s.md", as_of)

    warnings = monitor.daily_check(as_of)
    if warnings:
        log.warning("Health warnings: %s", warnings)


if __name__ == "__main__":
    main()
