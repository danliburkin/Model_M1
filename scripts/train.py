#!/usr/bin/env python3
"""
Run quarterly (first trading day of Jan, Apr, Jul, Oct).
Fits Stage B and Stage C models on trailing 2 years of data.
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

from src import model

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("train")


def main() -> None:
    artifact = model.train(date.today().isoformat())
    log.info(
        "Training complete — IV baseline AUC: %.4f, Stage B AUC: %.4f",
        artifact["iv_baseline_auc"],
        artifact["stage_b_auc"],
    )


if __name__ == "__main__":
    main()
