#!/usr/bin/env python3
"""
One-time: pull 2 years of options OHLCV history from Polygon.
Resumable via backfill_progress table.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import settings
from src.pull import (
    fetch_ndx_members,
    get_db,
    last_trading_day,
    list_contracts_for_backfill,
    polygon_get,
    store_options,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("backfill")


def main() -> None:
    end = last_trading_day()
    start = end - timedelta(days=365 * settings.TRAINING_WINDOW_YEARS)
    as_of = end.isoformat()
    tickers = fetch_ndx_members()
    con = get_db()

    log.info("Backfill %s to %s for %d tickers", start, end, len(tickers))

    for i, ticker in enumerate(tickers):
        log.info("Ticker %d/%d: %s", i + 1, len(tickers), ticker)
        try:
            contracts = list_contracts_for_backfill(ticker, as_of)
        except Exception as exc:
            log.error("Contract list failed for %s: %s", ticker, exc)
            continue

        for c in contracts:
            ct = c["ticker"]
            done = con.execute(
                "SELECT last_date FROM backfill_progress WHERE ticker = ? AND contract_ticker = ?",
                [ticker, ct],
            ).fetchone()
            if done and done[0] and done[0] >= end:
                continue

            url = (
                f"{settings.POLYGON_BASE}/v2/aggs/ticker/{ct}/range/1/day/"
                f"{start.isoformat()}/{end.isoformat()}"
            )
            r = polygon_get(url, {"adjusted": "true", "sort": "asc", "limit": 50000})
            if not r.ok:
                log.warning("Failed %s: %s", ct, r.status_code)
                continue

            rows = []
            for bar in r.json().get("results", []):
                ts = datetime.utcfromtimestamp(bar["t"] / 1000).date()
                rows.append(
                    {
                        "date": ts,
                        "ticker": ct,
                        "underlying": ticker,
                        "strike": float(c["strike_price"]),
                        "expiry": c["expiration_date"],
                        "contract_type": c["contract_type"],
                        "open": bar.get("o"),
                        "high": bar.get("h"),
                        "low": bar.get("l"),
                        "close": bar.get("c"),
                        "volume": bar.get("v"),
                        "vwap": bar.get("vw"),
                        "transactions": int(bar.get("n", 0)),
                    }
                )
            store_options(con, rows)
            con.execute(
                """
                INSERT OR REPLACE INTO backfill_progress (ticker, contract_ticker, last_date)
                VALUES (?, ?, ?)
                """,
                [ticker, ct, end],
            )
            log.info("  %s: %d bars", ct, len(rows))

    con.close()
    log.info("Backfill complete")


if __name__ == "__main__":
    main()
