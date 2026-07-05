"""IV inversion, Greeks, and all 15 feature computations."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
from py_vollib.black_scholes.implied_volatility import implied_volatility
from py_vollib.black_scholes.greeks.analytical import delta as bs_delta
from py_vollib.black_scholes.greeks.analytical import gamma as bs_gamma
from scipy.stats import norm

import settings
from src.pull import get_db

log = logging.getLogger(__name__)


def bsm_delta(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return float("nan")
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return float(norm.cdf(d1)) if flag in ("c", "call") else float(norm.cdf(d1) - 1)


def compute_iv(
    mid_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    flag: str,
) -> Optional[float]:
    """Compute implied volatility via BSM inversion."""
    flag_char = "c" if flag in ("call", "c") else "p"
    if mid_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    if flag_char == "c" and mid_price >= S:
        return None
    if flag_char == "p" and mid_price >= K:
        return None
    try:
        iv = float(implied_volatility(mid_price, S, K, T, r, flag_char))
    except Exception:
        log.debug("BSM IV failed S=%.2f K=%.2f T=%.4f mid=%.4f", S, K, T, mid_price)
        return None
    if iv < settings.IV_CLAMP_MIN or iv > settings.IV_CLAMP_MAX:
        return None
    return iv


def _years_to_expiry(as_of: datetime, expiry: datetime) -> float:
    return max((expiry - as_of).days / 365.25, 1 / 365.25)


def compute_contract_ivs(con: duckdb.DuckDBPyConnection, as_of: str) -> None:
    """Compute mid_price, IV, delta, gamma for each contract on as_of."""
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    rows = con.execute(
        """
        SELECT o.date, o.ticker, o.underlying, o.strike, o.expiry,
               o.contract_type, o.open, o.close, o.transactions
        FROM options_daily o
        WHERE o.date = ?
        """,
        [as_of],
    ).fetchdf()

    if rows.empty:
        return

    stocks = con.execute(
        "SELECT ticker, close FROM stock_daily WHERE date = ?", [as_of]
    ).fetchdf()
    stock_map = dict(zip(stocks["ticker"], stocks["close"]))

    mkt = con.execute(
        "SELECT t_bill_3m FROM market_daily WHERE date = ?", [as_of]
    ).fetchone()
    r = float(mkt[0]) if mkt and mkt[0] is not None else settings.DEFAULT_RISK_FREE_RATE

    updates: list[tuple] = []
    for _, row in rows.iterrows():
        S = stock_map.get(row["underlying"])
        if S is None or pd.isna(S):
            continue
        o, c = row["open"], row["close"]
        if pd.isna(o) or pd.isna(c):
            continue
        mid = (float(o) + float(c)) / 2.0
        expiry = pd.to_datetime(row["expiry"])
        T = _years_to_expiry(as_of_dt, expiry.to_pydatetime())
        flag = row["contract_type"]
        iv = compute_iv(mid, float(S), float(row["strike"]), T, r, flag)
        if iv is None:
            updates.append((mid, None, None, None, row["ticker"], as_of))
            continue
        fc = "c" if flag == "call" else "p"
        d = bs_delta(flag_char=fc, S=float(S), K=float(row["strike"]), t=T, r=r, sigma=iv)
        g = bs_gamma(flag_char=fc, S=float(S), K=float(row["strike"]), t=T, r=r, sigma=iv)
        updates.append((mid, iv, float(d), float(g), row["ticker"], as_of))

    if updates:
        upd = pd.DataFrame(
            updates,
            columns=["mid_price", "iv_computed", "delta", "gamma", "ticker", "date"],
        )
        con.register("_iv_upd", upd)
        con.execute(
            """
            UPDATE options_daily o SET
                mid_price = _iv_upd.mid_price,
                iv_computed = _iv_upd.iv_computed,
                delta = _iv_upd.delta,
                gamma = _iv_upd.gamma
            FROM _iv_upd
            WHERE o.date = _iv_upd.date AND o.ticker = _iv_upd.ticker
            """
        )


def _rolling_z(series: pd.Series, window: int = 252) -> pd.Series:
    mu = series.rolling(window, min_periods=60).mean()
    sd = series.rolling(window, min_periods=60).std()
    return (series - mu) / sd.replace(0, np.nan)


def _pct_rank(series: pd.Series, window: int = 252) -> pd.Series:
    def rank_pct(x):
        if len(x) < 2:
            return np.nan
        return x.rank(pct=True).iloc[-1]

    return series.rolling(window, min_periods=60).apply(rank_pct, raw=False)


def _realized_vol_and_ewma(con: duckdb.DuckDBPyConnection) -> None:
    """Compute realized_vol_20d and ewma_var for all stock_daily rows."""
    df = con.execute(
        "SELECT date, ticker, close FROM stock_daily ORDER BY ticker, date"
    ).fetchdf()
    if df.empty:
        return
    out_rows = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("date")
        rets = np.log(grp["close"] / grp["close"].shift(1))
        rv = rets.rolling(20).std() * np.sqrt(252)
        ewma_var = rets.ewm(alpha=1 - settings.EWMA_LAMBDA).var()
        for i, row in grp.iterrows():
            idx = grp.index.get_loc(i)
            out_rows.append(
                {
                    "date": row["date"],
                    "ticker": ticker,
                    "realized_vol_20d": float(rv.iloc[idx]) if pd.notna(rv.iloc[idx]) else None,
                    "ewma_var": float(ewma_var.iloc[idx]) if pd.notna(ewma_var.iloc[idx]) else None,
                }
            )
    upd = pd.DataFrame(out_rows)
    con.register("_rv", upd)
    con.execute(
        """
        UPDATE stock_daily s SET
            realized_vol_20d = _rv.realized_vol_20d,
            ewma_var = _rv.ewma_var
        FROM _rv WHERE s.date = _rv.date AND s.ticker = _rv.ticker
        """
    )


def _find_iv_by_delta(contracts: pd.DataFrame, target_delta: float, flag: str) -> Optional[float]:
    subset = contracts[contracts["contract_type"] == flag].dropna(subset=["iv_computed", "delta"])
    if subset.empty:
        return None
    idx = (subset["delta"] - target_delta).abs().idxmin()
    return float(subset.loc[idx, "iv_computed"])


def _find_iv_by_dte(contracts: pd.DataFrame, target_dte: int, as_of: datetime) -> Optional[float]:
    contracts = contracts.dropna(subset=["iv_computed"]).copy()
    if contracts.empty:
        return None
    contracts["dte"] = (pd.to_datetime(contracts["expiry"]) - as_of).dt.days
    idx = (contracts["dte"] - target_dte).abs().idxmin()
    return float(contracts.loc[idx, "iv_computed"])


def compute_features_for_date(con: duckdb.DuckDBPyConnection, as_of: str) -> None:
    """Compute all 15 features for each stock on as_of."""
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")

    tickers = con.execute(
        "SELECT DISTINCT underlying FROM options_daily WHERE date = ?", [as_of]
    ).fetchall()
    tickers = [t[0] for t in tickers]

    mkt_hist = con.execute(
        "SELECT date, vix, vix3m, hy_oas, dspx, cor1m FROM market_daily ORDER BY date"
    ).fetchdf()
    mkt_hist["date"] = pd.to_datetime(mkt_hist["date"])

    feature_rows: list[dict] = []

    for ticker in tickers:
        opts = con.execute(
            """
            SELECT * FROM options_daily WHERE date = ? AND underlying = ?
            """,
            [as_of, ticker],
        ).fetchdf()

        stock_hist = con.execute(
            """
            SELECT date, close, realized_vol_20d, ewma_var
            FROM stock_daily WHERE ticker = ? ORDER BY date
            """,
            [ticker],
        ).fetchdf()

        if stock_hist.empty:
            continue

        stock_hist["date"] = pd.to_datetime(stock_hist["date"])
        stock_hist["log_ret"] = np.log(stock_hist["close"] / stock_hist["close"].shift(1))
        stock_hist["ret_5d"] = np.log(stock_hist["close"] / stock_hist["close"].shift(5))

        iv_atm = _find_iv_by_delta(opts, 0.5, "call") or _find_iv_by_dte(opts, 30, as_of_dt)
        iv_25p = _find_iv_by_delta(opts, -0.25, "put")
        iv_25c = _find_iv_by_delta(opts, 0.25, "call")
        iv_60 = _find_iv_by_dte(opts, 60, as_of_dt)
        iv_30 = _find_iv_by_dte(opts, 30, as_of_dt)

        skew = (iv_25p - iv_25c) if iv_25p and iv_25c else None
        term_structure = (iv_60 / iv_30) if iv_60 and iv_30 and iv_30 > 0 else None

        call_vol = opts.loc[opts["contract_type"] == "call", "volume"].sum()
        put_vol = opts.loc[opts["contract_type"] == "put", "volume"].sum()
        cp_ratio = call_vol / put_vol if put_vol > 0 else None

        atm_call_iv = _find_iv_by_delta(opts, 0.5, "call")
        atm_put_iv = _find_iv_by_delta(opts, -0.5, "put")
        cw_spread = (atm_call_iv - atm_put_iv) if atm_call_iv and atm_put_iv else None

        # Historical feature series for z-scoring
        hist_feats = con.execute(
            """
            SELECT date, iv_atm_30dte, call_put_volume_ratio, skew_pct_rank_252d
            FROM features_daily WHERE ticker = ? ORDER BY date
            """,
            [ticker],
        ).fetchdf()

        iv_series = pd.concat(
            [
                hist_feats.set_index("date")["iv_atm_30dte"] if not hist_feats.empty else pd.Series(dtype=float),
                pd.Series({pd.Timestamp(as_of): iv_atm}),
            ]
        ).sort_index()
        iv_pct = _pct_rank(iv_series).iloc[-1] if iv_atm else None

        rv_row = stock_hist[stock_hist["date"] == pd.Timestamp(as_of)]
        ewma_rv = (
            math.sqrt(float(rv_row["ewma_var"].iloc[0]) * 252)
            if not rv_row.empty and pd.notna(rv_row["ewma_var"].iloc[0])
            else None
        )
        iv_minus_rv = (iv_atm - ewma_rv) if iv_atm and ewma_rv else None

        skew_series = pd.concat(
            [
                hist_feats.set_index("date")["skew_pct_rank_252d"]
                if not hist_feats.empty
                else pd.Series(dtype=float),
                pd.Series({pd.Timestamp(as_of): skew}),
            ]
        ).sort_index()
        skew_pct = _pct_rank(skew_series).iloc[-1] if skew is not None else None

        cp_series = pd.concat(
            [
                hist_feats.set_index("date")["call_put_volume_ratio"]
                if not hist_feats.empty
                else pd.Series(dtype=float),
                pd.Series({pd.Timestamp(as_of): cp_ratio}),
            ]
        ).sort_index()
        z_cp = float(_rolling_z(cp_series).iloc[-1]) if cp_ratio else None

        z_5d = float(_rolling_z(stock_hist.set_index("date")["ret_5d"]).iloc[-1]) if len(stock_hist) > 5 else None
        z_rv = (
            float(_rolling_z(stock_hist.set_index("date")["realized_vol_20d"]).iloc[-1])
            if len(stock_hist) > 60
            else None
        )

        mkt_row = mkt_hist[mkt_hist["date"] == pd.Timestamp(as_of)]
        vix = float(mkt_row["vix"].iloc[0]) if not mkt_row.empty and pd.notna(mkt_row["vix"].iloc[0]) else None
        vix3m = float(mkt_row["vix3m"].iloc[0]) if not mkt_row.empty and pd.notna(mkt_row["vix3m"].iloc[0]) else None
        dspx = float(mkt_row["dspx"].iloc[0]) if not mkt_row.empty and pd.notna(mkt_row["dspx"].iloc[0]) else None
        cor1m = float(mkt_row["cor1m"].iloc[0]) if not mkt_row.empty and pd.notna(mkt_row["cor1m"].iloc[0]) else None

        vix_hist = mkt_hist.set_index("date")["vix"].dropna()
        vix_chg = vix_hist.diff(3)
        z_vix_chg = float(_rolling_z(vix_chg).iloc[-1]) if len(vix_chg) > 60 and vix else None

        oas_hist = mkt_hist.set_index("date")["hy_oas"].dropna()
        oas_chg = oas_hist.diff(5)
        z_oas = float(_rolling_z(oas_chg).iloc[-1]) if len(oas_chg) > 60 else None

        iv_minus_hist = con.execute(
            "SELECT iv_minus_ewma_rv FROM features_daily WHERE ticker = ? ORDER BY date",
            [ticker],
        ).fetchdf()
        iv_minus_series = pd.concat(
            [
                iv_minus_hist["iv_minus_ewma_rv"]
                if not iv_minus_hist.empty
                else pd.Series(dtype=float),
                pd.Series([iv_minus_rv]),
            ]
        )
        z_iv_minus_ewma_rv = (
            float(_rolling_z(iv_minus_series).iloc[-1]) if iv_minus_rv is not None else None
        )

        feature_rows.append(
            {
                "date": as_of,
                "ticker": ticker,
                "iv_atm_30dte": iv_atm,
                "iv_pct_rank_252d": iv_pct,
                "iv_minus_ewma_rv": iv_minus_rv,
                "z_iv_minus_ewma_rv": z_iv_minus_ewma_rv,
                "skew_pct_rank_252d": skew_pct,
                "term_structure_60_30": term_structure,
                "z_call_put_volume_ratio": z_cp,
                "z_5d_return": z_5d,
                "vix_level": vix,
                "z_vix_3d_change": z_vix_chg,
                "vix3m_level": vix3m,
                "z_hy_oas_5d_change": z_oas,
                "dspx_level": dspx,
                "cor1m_level": cor1m,
                "z_realized_vol_20d": z_rv,
                "cw_iv_spread": cw_spread,
                "call_put_volume_ratio": cp_ratio,
            }
        )

    if feature_rows:
        df = pd.DataFrame(feature_rows)
        con.register("_feat", df)
        con.execute(
            """
            INSERT OR REPLACE INTO features_daily
            SELECT * FROM _feat
            """
        )


def run(as_of: str) -> None:
    con = get_db()
    _realized_vol_and_ewma(con)
    compute_contract_ivs(con, as_of)
    compute_features_for_date(con, as_of)
    con.close()
    log.info("Compute complete for %s", as_of)


def run_range(start: str | None = None, end: str | None = None) -> None:
    """Compute IV and features for every options date in range (for backfill)."""
    con = get_db()
    _realized_vol_and_ewma(con)

    if start and end:
        dates = con.execute(
            """
            SELECT DISTINCT date FROM options_daily
            WHERE date >= ? AND date <= ?
            ORDER BY date
            """,
            [start, end],
        ).fetchall()
    else:
        dates = con.execute(
            "SELECT DISTINCT date FROM options_daily ORDER BY date"
        ).fetchall()

    for i, (d,) in enumerate(dates):
        as_of = d.isoformat() if hasattr(d, "isoformat") else str(d)
        log.info("Computing features %d/%d: %s", i + 1, len(dates), as_of)
        compute_contract_ivs(con, as_of)
        compute_features_for_date(con, as_of)

    con.close()
    log.info("Compute history complete (%d dates)", len(dates))
