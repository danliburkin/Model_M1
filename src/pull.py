"""
All external data fetching for the NDX watchlist engine.

PRE-BUILD RATE LIMIT TEST (2026-05-24):
- Bulk grouped endpoint (/v2/aggs/grouped/...): HTTP 403 on free tier — not available.
- AAPL ATM window (strike 200-230, 14-60 DTE): 100+ contracts returned (limit hit).
- Total AAPL contracts: 1000+ in one paginated call.
- Conclusion: per-contract OHLCV pulls required. Scope capped at 42 contracts/stock
  (7 strikes × 3 expiries × 2 types). Nightly estimate: ~43 Polygon calls × 100 stocks
  ≈ 4300 calls ÷ 5/min ≈ 14 hours. Full 2-year backfill ≈ 4200 contract-range calls
  ≈ 14 hours one-time (resumable via backfill_progress table).
- Snapshot endpoints (Greeks/IV/OI): HTTP 403 on free tier — IV computed via BSM only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import duckdb
import exchange_calendars as xcals
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from fredapi import Fred

import settings

log = logging.getLogger(__name__)

NYSE = xcals.get_calendar("XNYS")
_SCHEMA_APPLIED = False


def last_trading_day(d: date | None = None) -> date:
    """Most recent NYSE session on or before d."""
    d = d or date.today()
    ts = pd.Timestamp(d)
    if NYSE.is_session(ts):
        return d
    return NYSE.date_to_session(ts, direction="previous").date()


def _yf_end_exclusive(as_of: date) -> str:
    """yfinance end date (exclusive) covering as_of session."""
    ts = pd.Timestamp(as_of)
    if NYSE.is_session(ts):
        return NYSE.next_session(ts).date().isoformat()
    return (as_of + timedelta(days=1)).isoformat()


class PolygonRateLimiter:
    """Token bucket: RATE_LIMIT_CALLS_PER_MIN calls per 60 seconds."""

    def __init__(self, max_calls: int = settings.RATE_LIMIT_CALLS_PER_MIN, window: float = 60.0):
        self.max_calls = max_calls
        self.window = window
        self._timestamps: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] >= self.window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_calls:
            sleep_for = self.window - (now - self._timestamps[0]) + 0.05
            if sleep_for > 0:
                log.debug("Rate limiter sleeping %.2fs", sleep_for)
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())


_limiter = PolygonRateLimiter()


def _require_keys() -> None:
    if not settings.POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY not set in .env")
    if not settings.FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY not set in .env")


def _redact_secrets(text: str) -> str:
    """Replace Polygon API key values with [REDACTED] for safe logging."""
    if not text:
        return text
    key = os.getenv("POLYGON_API_KEY") or settings.POLYGON_API_KEY or ""
    redacted = text.replace(key, "[REDACTED]") if key else text
    return re.sub(r"([?&]apiKey=)[^&\s\"']+", r"\1[REDACTED]", redacted, flags=re.IGNORECASE)


def get_db() -> duckdb.DuckDBPyConnection:
    global _SCHEMA_APPLIED
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(settings.DB_PATH))
    if not _SCHEMA_APPLIED and settings.SCHEMA_PATH.exists():
        con.execute(settings.SCHEMA_PATH.read_text())
        con.execute(
            "ALTER TABLE features_daily ADD COLUMN IF NOT EXISTS iv_minus_ewma_rv DOUBLE"
        )
        _SCHEMA_APPLIED = True
    return con


def polygon_get(
    url: str,
    params: Optional[dict] = None,
    max_retries: int = 4,
    log_con: Optional[duckdb.DuckDBPyConnection] = None,
) -> requests.Response:
    """GET with token-bucket rate limit, logging, and exponential backoff on 429."""
    _require_keys()
    params = dict(params or {})
    params["apiKey"] = settings.POLYGON_API_KEY
    full_url = f"{url}?{urlencode(params)}" if params else url
    safe_url = _redact_secrets(full_url)

    backoff = 15.0
    for attempt in range(max_retries):
        _limiter.wait()
        t0 = time.monotonic()
        try:
            r = requests.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log.error(
                "Polygon request failed url=%s error=%s elapsed_ms=%.0f",
                safe_url,
                _redact_secrets(str(exc)),
                elapsed_ms,
            )
            if attempt == max_retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
            continue

        elapsed_ms = (time.monotonic() - t0) * 1000
        log.info("Polygon GET url=%s status=%s elapsed_ms=%.0f", safe_url, r.status_code, elapsed_ms)

        if log_con is not None:
            try:
                log_con.execute(
                    "INSERT INTO pull_log VALUES (?, ?, ?, ?)",
                    [datetime.now(), safe_url[:2000], r.status_code, elapsed_ms],
                )
            except Exception as exc:
                log.debug("pull_log insert failed: %s", exc)

        if r.status_code == 429 and attempt < max_retries - 1:
            log.warning("Polygon 429 — backing off %.0fs (attempt %d)", backoff, attempt + 1)
            time.sleep(backoff)
            backoff *= 2
            continue
        return r
    return r  # type: ignore[possibly-undefined]


def fetch_ndx_members(force: bool = False) -> list[str]:
    """Fetch NDX-100 tickers from Wikipedia; weekly cache."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if (
        not force
        and settings.NDX_CACHE_PATH.exists()
        and (datetime.now().timestamp() - settings.NDX_CACHE_PATH.stat().st_mtime)
        < settings.NDX_CACHE_DAYS * 86400
    ):
        return json.loads(settings.NDX_CACHE_PATH.read_text())

    log.info("Fetching NDX-100 membership from Wikipedia")
    r = requests.get(settings.NDX_MEMBERSHIP_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tickers: list[str] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "ticker" in headers or "symbol" in headers:
            col_idx = headers.index("ticker") if "ticker" in headers else headers.index("symbol")
            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) > col_idx:
                    t = cells[col_idx].get_text(strip=True).replace(".", "-")
                    if t and (t.replace("-", "").isalnum()):
                        tickers.append(t)
            if tickers:
                break
    if not tickers:
        raise RuntimeError("Could not parse NDX-100 tickers from Wikipedia")

    settings.NDX_CACHE_PATH.write_text(json.dumps(sorted(set(tickers))))
    log.info("Cached %d NDX tickers", len(tickers))
    return sorted(set(tickers))


def fetch_stock_ohlcv(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Bulk download stock OHLCV via yfinance."""
    log.info("Fetching stock OHLCV for %d tickers %s to %s", len(tickers), start, end)
    data = yf.download(
        tickers,
        start=start,
        end=(datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    rows: list[dict] = []
    if len(tickers) == 1:
        t = tickers[0]
        df = data.copy()
        df.columns = [c if isinstance(c, str) else c[0] for c in df.columns]
        for idx, row in df.iterrows():
            rows.append(
                {
                    "date": idx.date(),
                    "ticker": t,
                    "open": float(row.get("Open", float("nan"))),
                    "high": float(row.get("High", float("nan"))),
                    "low": float(row.get("Low", float("nan"))),
                    "close": float(row.get("Close", float("nan"))),
                    "volume": float(row.get("Volume", float("nan"))),
                }
            )
    else:
        for t in tickers:
            if t not in data.columns.get_level_values(0):
                continue
            df = data[t].dropna(how="all")
            for idx, row in df.iterrows():
                rows.append(
                    {
                        "date": idx.date(),
                        "ticker": t,
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": float(row["Volume"]),
                    }
                )
    return pd.DataFrame(rows)


def _nearest_strikes(stock_price: float, available: list[float], n_each_side: int) -> list[float]:
    unique = sorted(set(available))
    if not unique:
        return []
    atm_idx = min(range(len(unique)), key=lambda i: abs(unique[i] - stock_price))
    lo = max(0, atm_idx - n_each_side)
    hi = min(len(unique), atm_idx + n_each_side + 1)
    return unique[lo:hi]


def _select_expiries(expiry_dates: list[date], as_of: date) -> list[date]:
    dtes = [(e, (e - as_of).days) for e in sorted(set(expiry_dates))]
    in_window = [e for e, d in dtes if settings.MIN_DTE <= d <= settings.MAX_DTE]
    return in_window[: settings.NUM_EXPIRIES]


def fetch_options_chain(ticker: str, as_of: str) -> list[dict]:
    """
    Fetch daily OHLCV for options contracts in the ATM window.
    Returns list of raw contract dicts ready for storage.
    """
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()

    stock_row = yf.download(
        ticker,
        start=as_of,
        end=_yf_end_exclusive(as_of_date),
        progress=False,
    )
    if stock_row.empty:
        log.warning("No stock price for %s on %s", ticker, as_of)
        return []
    close = stock_row["Close"]
    stock_price = float(close.iloc[-1]) if hasattr(close.iloc[-1], "__float__") else float(close.iloc[-1].item())

    lo = stock_price * 0.85
    hi = stock_price * 1.15
    r = polygon_get(
        f"{settings.POLYGON_BASE}/v3/reference/options/contracts",
        {
            "underlying_ticker": ticker,
            "as_of": as_of,
            "strike_price.gte": lo,
            "strike_price.lte": hi,
            "expiration_date.gte": as_of,
            "limit": 1000,
        },
    )
    if not r.ok:
        log.error("Contract reference failed for %s: %s", ticker, r.text[:200])
        return []

    contracts = r.json().get("results", [])
    if not contracts:
        return []

    strikes = _nearest_strikes(stock_price, [c["strike_price"] for c in contracts], settings.STRIKES_EACH_SIDE)
    expiries = _select_expiries(
        [datetime.strptime(c["expiration_date"], "%Y-%m-%d").date() for c in contracts],
        as_of_date,
    )
    expiry_strs = {e.isoformat() for e in expiries}

    filtered = [
        c
        for c in contracts
        if c["strike_price"] in strikes and c["expiration_date"] in expiry_strs
    ][: settings.STRIKES_EACH_SIDE * 2 * settings.NUM_EXPIRIES + 10]

    results: list[dict] = []
    for c in filtered:
        cticker = c["ticker"]
        url = f"{settings.POLYGON_BASE}/v2/aggs/ticker/{cticker}/range/1/day/{as_of}/{as_of}"
        ar = polygon_get(url, {"adjusted": "true", "sort": "asc", "limit": 1})
        if not ar.ok:
            log.debug("No agg for %s on %s: %s", cticker, as_of, ar.status_code)
            continue
        bars = ar.json().get("results", [])
        if not bars:
            continue
        bar = bars[0]
        n_txn = int(bar.get("n", 0))
        if n_txn < settings.MIN_TRANSACTIONS_FOR_IV:
            log.debug("Stale contract %s n=%d", cticker, n_txn)
            continue
        results.append(
            {
                "date": as_of_date,
                "ticker": cticker,
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
                "transactions": n_txn,
            }
        )
    log.info("%s %s: %d option rows from %d contracts", ticker, as_of, len(results), len(filtered))
    return results


def list_contracts_for_backfill(ticker: str, as_of: str) -> list[dict]:
    """List contract metadata for backfill scope."""
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    stock_row = yf.download(
        ticker,
        start=as_of,
        end=_yf_end_exclusive(as_of_date),
        progress=False,
    )
    if stock_row.empty:
        return []
    close = stock_row["Close"]
    stock_price = float(close.iloc[-1]) if hasattr(close.iloc[-1], "__float__") else float(close.iloc[-1].item())
    r = polygon_get(
        f"{settings.POLYGON_BASE}/v3/reference/options/contracts",
        {
            "underlying_ticker": ticker,
            "as_of": as_of,
            "strike_price.gte": stock_price * 0.85,
            "strike_price.lte": stock_price * 1.15,
            "expiration_date.gte": as_of,
            "limit": 1000,
        },
    )
    if not r.ok:
        return []
    contracts = r.json().get("results", [])
    strikes = _nearest_strikes(stock_price, [c["strike_price"] for c in contracts], settings.STRIKES_EACH_SIDE)
    expiries = _select_expiries(
        [datetime.strptime(c["expiration_date"], "%Y-%m-%d").date() for c in contracts],
        as_of_date,
    )
    expiry_strs = {e.isoformat() for e in expiries}
    return [
        c
        for c in contracts
        if c["strike_price"] in strikes and c["expiration_date"] in expiry_strs
    ]


def fetch_market_indices(start: str, end: str) -> pd.DataFrame:
    """VIX and VIX3M from yfinance."""
    end_date = datetime.strptime(end, "%Y-%m-%d").date()
    yf_end = _yf_end_exclusive(end_date)
    rows: list[dict] = []
    for sym, col in [("^VIX", "vix"), ("^VIX3M", "vix3m")]:
        df = yf.download(sym, start=start, end=yf_end, progress=False)
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            existing = next((r for r in rows if r["date"] == d), None)
            if existing is None:
                existing = {"date": d}
                rows.append(existing)
            existing[col] = float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"])
    return pd.DataFrame(rows)


def fetch_fred_series(start: str, end: str) -> pd.DataFrame:
    """HY OAS and 3-month T-bill from FRED."""
    _require_keys()
    fred = Fred(api_key=settings.FRED_API_KEY)
    rows: dict[date, dict] = {}
    for col, series_id in settings.FRED_SERIES.items():
        s = fred.get_series(series_id, observation_start=start, observation_end=end)
        for idx, val in s.items():
            d = idx.date() if hasattr(idx, "date") else idx
            rows.setdefault(d, {"date": d})
            if col == "t_bill_3m":
                rows[d][col] = float(val) / 100.0 if pd.notna(val) else None
            else:
                rows[d][col] = float(val) if pd.notna(val) else None
    return pd.DataFrame(rows.values())


def fetch_cboe_metrics(as_of: str) -> dict[str, Optional[float]]:
    """Scrape DSPX and COR1M from CBOE daily statistics page."""
    result: dict[str, Optional[float]] = {"dspx": None, "cor1m": None}
    try:
        r = requests.get(settings.CBOE_DSPX_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok:
            log.warning("CBOE fetch failed: %s", r.status_code)
            return result
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ")
        for label, key in [("DSPX", "dspx"), ("COR1M", "cor1m"), ("Implied Correlation", "cor1m")]:
            m = re.search(rf"{label}[:\s]+([0-9]+\.[0-9]+)", text, re.I)
            if m:
                result[key] = float(m.group(1))
    except Exception as exc:
        log.warning("CBOE scrape error: %s", exc)
    return result


def store_options(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    con.register("_opts", df)
    con.execute(
        """
        INSERT OR REPLACE INTO options_daily
        (date, ticker, underlying, strike, expiry, contract_type,
         open, high, low, close, volume, vwap, transactions)
        SELECT date, ticker, underlying, strike, expiry, contract_type,
               open, high, low, close, volume, vwap, transactions
        FROM _opts
        """
    )


def store_stocks(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    if df.empty:
        return
    con.register("_stocks", df)
    con.execute(
        """
        INSERT OR REPLACE INTO stock_daily (date, ticker, open, high, low, close, volume)
        SELECT date, ticker, open, high, low, close, volume FROM _stocks
        """
    )


def store_market(con: duckdb.DuckDBPyConnection, row: dict) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO market_daily
        (date, vix, vix3m, hy_oas, t_bill_3m, dspx, cor1m)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row.get("date"),
            row.get("vix"),
            row.get("vix3m"),
            row.get("hy_oas"),
            row.get("t_bill_3m"),
            row.get("dspx"),
            row.get("cor1m"),
        ],
    )


def run(as_of: str, tickers: Optional[list[str]] = None) -> None:
    """Pull all external data for one date."""
    _require_keys()
    tickers = tickers or fetch_ndx_members()
    con = get_db()

    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
    stocks = fetch_stock_ohlcv(tickers + [settings.UNIVERSE_TICKER], start, as_of)
    store_stocks(con, stocks)

    all_opts: list[dict] = []
    for t in tickers:
        try:
            all_opts.extend(fetch_options_chain(t, as_of))
        except Exception as exc:
            log.error("Options pull failed for %s: %s", t, exc)

    store_options(con, all_opts)

    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    mkt = fetch_market_indices(start, as_of)
    fred = fetch_fred_series(start, as_of)
    cboe = fetch_cboe_metrics(as_of)

    row: dict[str, Any] = {"date": as_of_date}
    if not mkt.empty:
        m = mkt[mkt["date"] == as_of_date]
        if not m.empty:
            row.update(m.iloc[0].to_dict())
    if not fred.empty:
        f = fred[fred["date"] == as_of_date]
        if not f.empty:
            for c in ("hy_oas", "t_bill_3m"):
                if c in f.columns:
                    row[c] = f.iloc[0][c]
    row.update(cboe)
    store_market(con, row)
    con.close()
    log.info("Pull complete for %s", as_of)
