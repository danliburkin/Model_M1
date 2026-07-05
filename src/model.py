"""Stage B and C: training, inference, calibration."""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.calibration import CalibratedClassifierCV

import settings
from src.pull import get_db

log = logging.getLogger(__name__)


@dataclass
class Signal:
    ticker: str
    activity_score: float
    p_magnitude: float
    p_up: float
    signal: str
    confidence: str
    threshold_pct: float
    iv_baseline_p: float = 0.0
    marginal_lift: float = 0.0
    long_threshold: float = 0.5
    short_threshold: float = 0.5
    top_features: list[str] | None = None
    leakage_caveats: list[str] | None = None


class PurgedKFold:
    """K-fold CV with purge and embargo for overlapping forward returns."""

    def __init__(
        self,
        n_splits: int = settings.CV_N_FOLDS,
        horizon: int = settings.HORIZON_DAYS,
        embargo: int = settings.CV_EMBARGO_DAYS,
    ):
        self.n_splits = n_splits
        self.horizon = horizon
        self.embargo = embargo

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return self.n_splits

    def split(self, X, y=None, groups=None):
        dates = np.asarray(groups if groups is not None else X)
        unique_dates = np.sort(np.unique(dates))
        n = len(unique_dates)
        fold_size = max(n // self.n_splits, 1)
        for i in range(self.n_splits):
            test_start = i * fold_size
            test_end = n if i == self.n_splits - 1 else (i + 1) * fold_size
            if test_start >= n:
                break
            test_dates = set(unique_dates[test_start:test_end])
            test_min, test_max = unique_dates[test_start], unique_dates[test_end - 1]

            train_mask = np.ones(len(dates), dtype=bool)
            for j, d in enumerate(dates):
                if d in test_dates:
                    train_mask[j] = False
                    continue
                if abs((pd.Timestamp(d) - pd.Timestamp(test_min)).days) < self.horizon:
                    train_mask[j] = False
                if abs((pd.Timestamp(d) - pd.Timestamp(test_max)).days) < self.horizon:
                    train_mask[j] = False
                if pd.Timestamp(test_min) - pd.Timedelta(days=self.embargo) <= pd.Timestamp(d) < pd.Timestamp(
                    test_min
                ):
                    train_mask[j] = False
                if pd.Timestamp(test_max) < pd.Timestamp(d) <= pd.Timestamp(test_max) + pd.Timedelta(
                    days=self.embargo
                ):
                    train_mask[j] = False
            test_mask = np.array([d in test_dates for d in dates])
            yield np.where(train_mask)[0], np.where(test_mask)[0]


class PurgedKFoldWithDates(PurgedKFold):
    """PurgedKFold bound to a fixed date array for sklearn CalibratedClassifierCV."""

    def __init__(self, dates: np.ndarray, **kwargs):
        super().__init__(**kwargs)
        self._dates = np.asarray(dates)

    def split(self, X, y=None, groups=None):
        yield from super().split(self._dates, y, groups=self._dates)


def _build_training_frame(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, dict[str, float]]:
    """Build feature matrix with forward return targets."""
    end = datetime.now().date()
    start = end - timedelta(days=365 * settings.TRAINING_WINDOW_YEARS + 30)

    feats = con.execute(
        """
        SELECT f.*, s.close AS stock_close, s.realized_vol_20d
        FROM features_daily f
        JOIN stock_daily s ON f.date = s.date AND f.ticker = s.ticker
        WHERE f.date >= ?
        ORDER BY f.ticker, f.date
        """,
        [start.isoformat()],
    ).fetchdf()

    if feats.empty:
        return feats, {}

    feats["date"] = pd.to_datetime(feats["date"])
    rows = []
    k_by_ticker: dict[str, float] = {}

    for ticker, grp in feats.groupby("ticker"):
        grp = grp.sort_values("date").reset_index(drop=True)
        closes = grp["stock_close"].tolist()
        rvol = grp["realized_vol_20d"].tolist()

        cal_rets: list[float] = []
        cal_sigmas: list[float] = []
        for i in range(len(grp) - settings.HORIZON_DAYS - 1):
            open_t1 = closes[i + 1]
            close_t5 = closes[i + settings.HORIZON_DAYS]
            if open_t1 is None or close_t5 is None or open_t1 <= 0:
                continue
            rv = rvol[i]
            sigma = (
                (rv * np.sqrt(settings.HORIZON_DAYS / 252))
                if rv and rv > 0
                else settings.DEFAULT_SIGMA_WINDOW
            )
            cal_rets.append(float(np.log(close_t5 / open_t1)))
            cal_sigmas.append(sigma)

        k_by_ticker[ticker] = calibrate_k(
            ticker,
            np.array(cal_rets),
            np.array(cal_sigmas),
        )

        for i in range(len(grp) - settings.HORIZON_DAYS - 1):
            if i + 1 >= len(closes) or i + settings.HORIZON_DAYS >= len(closes):
                continue
            open_t1 = closes[i + 1]
            close_t5 = closes[i + settings.HORIZON_DAYS]
            if open_t1 is None or close_t5 is None or open_t1 <= 0:
                continue
            fwd_ret = np.log(close_t5 / open_t1)
            rv = rvol[i]
            sigma = (
                (rv * np.sqrt(settings.HORIZON_DAYS / 252))
                if rv and rv > 0
                else settings.DEFAULT_SIGMA_WINDOW
            )
            k = k_by_ticker[ticker]
            threshold = k * sigma
            row = grp.iloc[i].to_dict()
            row["y_magnitude"] = int(abs(fwd_ret) > threshold)
            row["y_direction"] = int(fwd_ret > 0)
            row["fwd_ret"] = fwd_ret
            row["k_threshold"] = k
            rows.append(row)

    return pd.DataFrame(rows), k_by_ticker


def calibrate_k(
    ticker: str,
    returns: np.ndarray,
    sigmas: np.ndarray,
    target: float = settings.BASE_RATE_TARGET,
) -> float:
    """Find k such that fraction of |ret| > k*sigma_window ≈ target."""
    if len(returns) < 50 or len(sigmas) != len(returns):
        return settings.DEFAULT_K_THRESHOLD
    valid = (sigmas > 0) & np.isfinite(returns) & np.isfinite(sigmas)
    if valid.sum() < 50:
        return settings.DEFAULT_K_THRESHOLD
    rets = returns[valid]
    sigs = sigmas[valid]
    for k in np.linspace(0.5, 3.0, 50):
        rate = np.mean(np.abs(rets) > k * sigs)
        if abs(rate - target) < 0.05:
            return float(k)
    return settings.DEFAULT_K_THRESHOLD


def train_iv_baseline(X: pd.DataFrame, y: np.ndarray, dates: np.ndarray) -> tuple[float, LogisticRegression]:
    """Fit logistic regression on iv_atm_30dte only; return purged CV AUC and fitted model."""
    pkf = PurgedKFold()
    aucs = []
    x_iv = X[["iv_atm_30dte"]].fillna(X["iv_atm_30dte"].median()).values
    for train_idx, test_idx in pkf.split(x_iv, y, groups=dates):
        if len(test_idx) == 0 or len(np.unique(y[test_idx])) < 2:
            continue
        lr = LogisticRegression(max_iter=500)
        lr.fit(x_iv[train_idx], y[train_idx])
        proba = lr.predict_proba(x_iv[test_idx])[:, 1]
        aucs.append(roc_auc_score(y[test_idx], proba))
    iv_lr = LogisticRegression(max_iter=500)
    iv_lr.fit(x_iv, y)
    return (float(np.mean(aucs)) if aucs else 0.5, iv_lr)


def prune_features(model: lgb.LGBMClassifier, X: pd.DataFrame, vix_series: pd.Series) -> list[str]:
    """Regime-conditional SHAP pruning."""
    explainer = shap.TreeExplainer(model)
    vix_aligned = vix_series.reindex(X.index).ffill()
    terciles = vix_aligned.quantile([0.33, 0.67])

    kept = []
    for col in X.columns:
        shap_calm = shap_stress = 0.0
        calm_mask = vix_aligned <= terciles.iloc[0]
        stress_mask = vix_aligned >= terciles.iloc[1]
        if calm_mask.sum() > 10:
            sv = explainer.shap_values(X.loc[calm_mask])
            if isinstance(sv, list):
                sv = sv[1]
            idx = list(X.columns).index(col)
            shap_calm = float(np.mean(np.abs(sv[:, idx])))
        if stress_mask.sum() > 10:
            sv = explainer.shap_values(X.loc[stress_mask])
            if isinstance(sv, list):
                sv = sv[1]
            idx = list(X.columns).index(col)
            shap_stress = float(np.mean(np.abs(sv[:, idx])))
        if shap_calm > 0.005 or shap_stress > 0.005:
            kept.append(col)
        else:
            log.info("Dropped feature %s (calm=%.4f stress=%.4f)", col, shap_calm, shap_stress)
    return kept or list(X.columns)


def _latest_model_path(stage: str) -> Optional[Path]:
    settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(settings.MODELS_DIR.glob(f"{stage}_*.pkl"), reverse=True)
    return paths[0] if paths else None


def train(date: Optional[str] = None) -> dict[str, Any]:
    """Train Stage B and C models; save artifacts."""
    con = get_db()
    df, k_by_ticker = _build_training_frame(con)
    if df.empty or len(df) < 200:
        raise RuntimeError("Insufficient training data — run backfill first")

    dates = df["date"].values
    y_mag = df["y_magnitude"].values.astype(int)
    y_dir = df["y_direction"].values.astype(int)

    X_b = df[settings.STAGE_B_FEATURES].fillna(0)
    iv_auc, iv_lr = train_iv_baseline(df, y_mag, dates)
    log.info("IV baseline AUC: %.4f", iv_auc)

    pkf = PurgedKFold()
    model_b = lgb.LGBMClassifier(n_estimators=100, max_depth=4, verbose=-1)
    model_b.fit(X_b, y_mag)
    cal_b = CalibratedClassifierCV(
        lgb.LGBMClassifier(n_estimators=100, max_depth=4, verbose=-1),
        cv=PurgedKFoldWithDates(dates),
        method="sigmoid",
    )
    cal_b.fit(X_b, y_mag)

    aucs_b: list[float] = []
    for train_idx, test_idx in pkf.split(X_b, y_mag, groups=dates):
        if len(test_idx) < 2 or len(np.unique(y_mag[test_idx])) < 2:
            continue
        mb = lgb.LGBMClassifier(n_estimators=100, max_depth=4, verbose=-1)
        mb.fit(X_b.iloc[train_idx], y_mag[train_idx])
        proba = mb.predict_proba(X_b.iloc[test_idx])[:, 1]
        aucs_b.append(roc_auc_score(y_mag[test_idx], proba))
    auc_b = float(np.mean(aucs_b)) if aucs_b else iv_auc

    if auc_b - iv_auc < settings.IV_BASELINE_MIN_AUC_LIFT:
        log.warning("Model is not adding value beyond raw IV.")

    vix = con.execute("SELECT date, vix FROM market_daily ORDER BY date").fetchdf()
    vix.index = pd.to_datetime(vix["date"])
    kept = prune_features(model_b, X_b, vix["vix"])
    importances = dict(zip(settings.STAGE_B_FEATURES, model_b.feature_importances_))
    top_features = sorted(importances, key=importances.get, reverse=True)[:5]

    X_c = df[settings.STAGE_C_FEATURES].fillna(0)
    cal_c = CalibratedClassifierCV(
        lgb.LGBMClassifier(n_estimators=100, max_depth=4, verbose=-1),
        cv=PurgedKFoldWithDates(dates),
        method="sigmoid",
    )
    cal_c.fit(X_c, y_dir)

    # Empirical direction thresholds
    proba_dir = cal_c.predict_proba(X_c)[:, 1]
    long_th, short_th = 0.55, 0.45
    for th in np.linspace(0.35, 0.65, 30):
        hits = y_dir[proba_dir >= th]
        if len(hits) > 20 and hits.mean() >= settings.DIRECTION_HIT_RATE_TARGET:
            long_th = float(th)
            break
    for th in np.linspace(0.65, 0.35, 30):
        hits = y_dir[proba_dir <= th]
        if len(hits) > 20 and (1 - hits.mean()) >= settings.DIRECTION_HIT_RATE_TARGET:
            short_th = float(th)
            break

    stamp = (date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    artifact = {
        "stage_b": cal_b,
        "stage_c": cal_c,
        "iv_baseline": iv_lr,
        "features_b": kept,
        "features_c": settings.STAGE_C_FEATURES,
        "top_features": top_features,
        "k_by_ticker": k_by_ticker,
        "long_threshold": long_th,
        "short_threshold": short_th,
        "iv_baseline_auc": iv_auc,
        "stage_b_auc": auc_b,
        "trained_at": datetime.now().isoformat(),
    }
    path = settings.MODELS_DIR / f"stage_b_{stamp}.pkl"
    settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(artifact, f)
    con.close()
    log.info("Models saved to %s", path)
    return artifact


def _load_artifact() -> dict[str, Any]:
    path = _latest_model_path("stage_b")
    if not path:
        raise RuntimeError("No trained model found — run scripts/train.py after backfill and compute")
    with open(path, "rb") as f:
        return pickle.load(f)


def store_signals(con: duckdb.DuckDBPyConnection, as_of: str, signals: list[Signal]) -> None:
    """Persist signals for monitoring."""
    if not signals:
        return
    rows = [
        {
            "date": as_of,
            "ticker": s.ticker,
            "activity_score": s.activity_score,
            "p_magnitude": s.p_magnitude,
            "p_up": s.p_up,
            "signal": s.signal,
            "confidence": s.confidence,
            "threshold_pct": s.threshold_pct,
            "top_features": ",".join(s.top_features or []),
            "leakage_caveats": ",".join(s.leakage_caveats or []),
        }
        for s in signals
    ]
    df = pd.DataFrame(rows)
    con.register("_signals", df)
    con.execute(
        """
        INSERT OR REPLACE INTO signals
        (date, ticker, activity_score, p_magnitude, p_up, signal,
         confidence, threshold_pct, top_features, leakage_caveats)
        SELECT date, ticker, activity_score, p_magnitude, p_up, signal,
               confidence, threshold_pct, top_features, leakage_caveats
        FROM _signals
        """
    )


def predict(candidates: list[str], as_of: str) -> list[Signal]:
    con = get_db()
    artifact = _load_artifact()
    cal_b = artifact["stage_b"]
    cal_c = artifact["stage_c"]
    iv_lr = artifact.get("iv_baseline")
    top_features_default = artifact.get("top_features", settings.STAGE_B_FEATURES[:3])
    k_by_ticker = artifact.get("k_by_ticker", {})
    long_th = artifact.get("long_threshold", 0.55)
    short_th = artifact.get("short_threshold", 0.45)

    signals: list[Signal] = []
    for ticker in candidates:
        row = con.execute(
            "SELECT * FROM features_daily WHERE ticker = ? AND date = ?",
            [ticker, as_of],
        ).fetchdf()
        if row.empty:
            continue

        Xb = row[settings.STAGE_B_FEATURES].fillna(0)
        Xc = row[settings.STAGE_C_FEATURES].fillna(0)
        p_mag = float(cal_b.predict_proba(Xb)[0, 1])
        p_up = float(cal_c.predict_proba(Xc)[0, 1])

        iv_val = float(row["iv_atm_30dte"].iloc[0]) if pd.notna(row["iv_atm_30dte"].iloc[0]) else None
        if iv_lr is not None and iv_val is not None:
            iv_baseline_p = float(iv_lr.predict_proba([[iv_val]])[0, 1])
        else:
            iv_baseline_p = p_mag

        rv = con.execute(
            "SELECT realized_vol_20d FROM stock_daily WHERE ticker = ? AND date = ?",
            [ticker, as_of],
        ).fetchone()
        rv_val = float(rv[0]) if rv and rv[0] else settings.DEFAULT_REALIZED_VOL
        k = k_by_ticker.get(ticker, settings.DEFAULT_K_THRESHOLD)
        threshold_pct = rv_val * np.sqrt(settings.HORIZON_DAYS / 252) * 100 * k

        if p_mag < settings.STAGE_B_THRESHOLD:
            sig = "WATCH"
            conf = "low"
        elif p_up >= long_th:
            sig = "LONG"
            conf = "high" if p_mag > 0.65 else "medium"
        elif p_up <= short_th:
            sig = "SHORT"
            conf = "high" if p_mag > 0.65 else "medium"
        else:
            sig = "NO_DIRECTION"
            conf = "medium"

        act = con.execute(
            """
            SELECT call_put_volume_ratio FROM features_daily
            WHERE ticker = ? AND date = ?
            """,
            [ticker, as_of],
        ).fetchone()
        activity = float(act[0]) if act and act[0] else 0.0

        signals.append(
            Signal(
                ticker=ticker,
                activity_score=activity,
                p_magnitude=p_mag,
                p_up=p_up,
                signal=sig,
                confidence=conf,
                threshold_pct=threshold_pct,
                iv_baseline_p=iv_baseline_p,
                marginal_lift=p_mag - iv_baseline_p,
                long_threshold=long_th,
                short_threshold=short_th,
                top_features=top_features_default[:3],
                leakage_caveats=[],
            )
        )

    store_signals(con, as_of, signals)

    con.close()
    return signals
