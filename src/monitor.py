"""Monthly and daily health monitoring."""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp
from sklearn.metrics import roc_auc_score

import settings
from src.pull import get_db

log = logging.getLogger(__name__)


def bootstrap_ci(data: list[float], stat_fn, n_boot: int = 1000, ci: float = 0.80) -> tuple:
    """Compute bootstrap confidence interval for a statistic."""
    arr = np.array(data)
    if len(arr) == 0:
        return (float("nan"), float("nan"), float("nan"))
    point = float(stat_fn(arr))
    boots = []
    rng = np.random.default_rng(42)
    for _ in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        boots.append(float(stat_fn(sample)))
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(boots, [alpha, 1 - alpha])
    return (point, float(lo), float(hi))


def check_pause_conditions(con) -> list[str]:
    """Returns list of active pause warnings."""
    warnings: list[str] = []

    sigs = con.execute(
        """
        SELECT signal, p_magnitude, p_up FROM signals
        WHERE date >= CURRENT_DATE - INTERVAL '30 days'
        """
    ).fetchdf()
    if sigs.empty:
        return warnings

    for sig_type in ("LONG", "SHORT"):
        subset = sigs[sigs["signal"] == sig_type]
        if len(subset) < 10:
            continue
        hit_rate = (subset["p_up"] > 0.5).mean() if sig_type == "LONG" else (subset["p_up"] < 0.5).mean()
        _, lo, _ = bootstrap_ci(subset["p_up"].tolist(), np.mean)
        if hit_rate < settings.HIT_RATE_MIN and lo < settings.HIT_RATE_MIN:
            warnings.append(f"{sig_type} hit rate below minimum")

    feats = con.execute(
        """
        SELECT iv_atm_30dte, z_iv_minus_ewma_rv FROM features_daily
        WHERE date >= CURRENT_DATE - INTERVAL '60 days'
        """
    ).fetchdf()
    ref = con.execute(
        """
        SELECT iv_atm_30dte, z_iv_minus_ewma_rv FROM features_daily
        WHERE date < CURRENT_DATE - INTERVAL '60 days'
        AND date >= CURRENT_DATE - INTERVAL '1 year'
        """
    ).fetchdf()
    for col in feats.columns:
        if col in ref.columns and feats[col].notna().sum() > 30 and ref[col].notna().sum() > 30:
            ks = ks_2samp(feats[col].dropna(), ref[col].dropna()).statistic
            if ks > settings.KS_DRIFT_PAUSE:
                warnings.append(f"Feature drift pause: {col} KS={ks:.3f}")

    return warnings


def daily_check(as_of: str) -> list[str]:
    con = get_db()
    warnings = check_pause_conditions(con)
    con.close()
    return warnings


def run_monthly(year_month: str | None = None) -> str:
    """Write monitoring/YYYY-MM.md report."""
    ym = year_month or datetime.now().strftime("%Y-%m")
    settings.MONITORING_DIR.mkdir(parents=True, exist_ok=True)
    con = get_db()

    sigs = con.execute(
        """
        SELECT * FROM signals WHERE strftime(date, '%Y-%m') = ?
        """,
        [ym],
    ).fetchdf()

    warnings = check_pause_conditions(con)
    con.close()

    lines = [
        f"# Monitoring Report — {ym}",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Summary",
        "",
        f"- Total signals: {len(sigs)}",
        f"- Pause warnings: {len(warnings)}",
        "",
    ]
    if warnings:
        lines.append("## Active Warnings")
        for w in warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    lines.extend(
        [
            "## Stage B Calibration",
            "Decile calibration table — run after sufficient signal history.",
            "",
            "## Stage C Hit Rates",
            "Bootstrap 80% CI by signal type.",
            "",
        ]
    )

    path = settings.MONITORING_DIR / f"{ym}.md"
    path.write_text("\n".join(lines))
    log.info("Monitoring report: %s", path)
    return str(path)
