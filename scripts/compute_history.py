#!/usr/bin/env python3
"""
Compute IV and features for all dates already in options_daily.
Run after backfill.py completes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src import compute

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("compute_history")


def main() -> None:
    compute.run_range()
    log.info("Done")


if __name__ == "__main__":
    main()
