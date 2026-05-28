"""Stage A screening: exclusions and ActivityScore_relative."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pandas as pd

import settings
from src.pull import fetch_ndx_members, get_db

log = logging.getLogger(__name__)


def _third_friday(year: int, month: int) -> datetime:
    """Monthly options expiration (3rd Friday)."""
    d = datetime(year, month, 1)
    fridays = 0
    while fridays < 3:
        if d.weekday() == 4:
            fridays += 1
            if fridays == 3:
                return d
        d += timedelta(days=1)
    return d


def _in_opex_window(dt: datetime) -> bool:
    tf = _third_friday(dt.year, dt.month)
    return abs((dt - tf).days) <= settings.OPEX_BUFFER_DAYS


def _z_score_from_history(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    as_of: str,
    column: str,
    window: int = 252,
) -> float:
    df = con.execute(
        f"""
        SELECT date, {column} FROM features_daily
        WHERE ticker = ? AND date <= ? AND {column} IS NOT NULL
        ORDER BY date DESC LIMIT ?
        """,
        [ticker, as_of, window],
    ).fetchdf()
    if len(df) < 20:
        return 0.0
    vals = df[column].astype(float)
    mu, sd = vals.mean(), vals.std()
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float((vals.iloc[0] - mu) / sd)


def apply_exclusions(ticker: str, as_of: str, con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    """Returns (passes, reason_if_excluded)."""
    dt = datetime.strptime(as_of, "%Y-%m-%d")

    # 1. earnings window — placeholder: no calendar source; skip unless flagged in DB
    # 2. ex-dividend — placeholder
    # 3. OpEx window
    if _in_opex_window(dt):
        return False, "opex_window"

    # 4. insufficient history
    hist = con.execute(
        "SELECT COUNT(*) FROM stock_daily WHERE ticker = ? AND date <= ?",
        [ticker, as_of],
    ).fetchone()[0]
    if hist < settings.MIN_HISTORY_DAYS:
        return False, "insufficient_history"

    # 5. insufficient option volume
    vol = con.execute(
        """
        SELECT AVG(volume) FROM options_daily
        WHERE underlying = ? AND date <= ? AND date > ? - INTERVAL '20 days'
        """,
        [ticker, as_of, as_of],
    ).fetchone()[0]
    if vol is None or vol < settings.MIN_OPTION_VOLUME_20D:
        return False, "insufficient_option_volume"

    return True, ""


def _activity_score(con: duckdb.DuckDBPyConnection, ticker: str, as_of: str) -> float:
    z_cp = _z_score_from_history(con, ticker, as_of, "call_put_volume_ratio")
    z_iv = _z_score_from_history(con, ticker, as_of, "iv_pct_rank_252d")
    z_rv = _z_score_from_history(con, ticker, as_of, "z_iv_minus_ewma_rv")
    return 0.5 * z_cp + 0.3 * z_iv + 0.2 * z_rv


def _stage_a_n(as_of: str, vix: float) -> int:
    """Dynamic Stage A candidate count."""
    # Simple VIX-scaled cap
    vix_factor = min(max(vix / 20.0, 0.5), 2.0) if vix else 1.0
    n = int(settings.STAGE_A_BASE + settings.STAGE_A_SLOPE * vix_factor)
    return min(n, settings.STAGE_A_MAX)


def run(as_of: str) -> list[str]:
    con = get_db()
    universe = fetch_ndx_members()

    mkt = con.execute("SELECT vix FROM market_daily WHERE date = ?", [as_of]).fetchone()
    vix = float(mkt[0]) if mkt and mkt[0] else 20.0
    n_select = _stage_a_n(as_of, vix)

    scores: list[tuple[str, float]] = []
    for ticker in universe:
        ok, reason = apply_exclusions(ticker, as_of, con)
        if not ok:
            log.info("Excluded %s: %s", ticker, reason)
            continue
        score_stock = _activity_score(con, ticker, as_of)
        score_qqq = _activity_score(con, settings.UNIVERSE_TICKER, as_of)
        score_rel = score_stock - score_qqq
        scores.append((ticker, score_rel))

    scores.sort(key=lambda x: x[1], reverse=True)
    candidates = [t for t, _ in scores[:n_select]]
    con.close()
    log.info("Stage A: %d candidates from %d universe", len(candidates), len(universe))
    return candidates
