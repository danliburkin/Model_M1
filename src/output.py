"""Write watchlist JSON and render markdown."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import duckdb

import settings
from src.model import Signal
from src.pull import fetch_ndx_members, get_db

log = logging.getLogger(__name__)


def _market_regime(vix: float) -> str:
    if vix < 15:
        return "calm"
    if vix < 25:
        return "normal"
    return "elevated"


def _build_payload(as_of: str, signals: list[Signal], screening: dict) -> dict[str, Any]:
    con = get_db()
    mkt = con.execute(
        "SELECT vix, vix3m, dspx, hy_oas FROM market_daily WHERE date = ?", [as_of]
    ).fetchone()
    con.close()

    vix = float(mkt[0]) if mkt and mkt[0] else 20.0
    vix3m = float(mkt[1]) if mkt and mkt[1] else None
    dspx = float(mkt[2]) if mkt and mkt[2] else None

    return {
        "date": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market": {
            "regime": _market_regime(vix),
            "vix": vix,
            "vix3m": vix3m,
            "vix_3d_change_zscore": None,
            "dspx": dspx,
            "hy_oas_5d_change_zscore": None,
        },
        "screening": screening,
        "signals": [
            {
                "ticker": s.ticker,
                "activity_score": round(s.activity_score, 2),
                "p_magnitude": round(s.p_magnitude, 3),
                "iv_baseline_p": round(s.iv_baseline_p, 3),
                "marginal_lift": round(s.marginal_lift, 3),
                "p_up": round(s.p_up, 3),
                "long_threshold": round(s.long_threshold, 3),
                "short_threshold": round(s.short_threshold, 3),
                "signal": s.signal,
                "confidence": s.confidence,
                "threshold_pct": round(s.threshold_pct, 1),
                "top_features": s.top_features or [],
                "leakage_caveats": s.leakage_caveats or [],
            }
            for s in signals
            if s.signal != "NO_DIRECTION"
        ],
        "health": {
            "iv_baseline_auc_lift_30d": None,
            "stage_b_calibration_error_30d": None,
            "last_refit": None,
            "next_refit": None,
            "pause_warnings": [],
        },
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    d = payload["date"]
    mkt = payload["market"]
    scr = payload["screening"]
    lines = [
        f"# Watchlist — {d}",
        "",
        f"**Market:** {mkt['regime'].upper()} (VIX {mkt['vix']:.1f})"
        + (f" | DSPX {mkt['dspx']:.1f}" if mkt.get("dspx") else ""),
        "",
        f"Screened: {scr.get('after_exclusions', 0)} stocks → "
        f"{scr.get('candidates_screened', 0)} candidates → "
        f"{scr.get('magnitude_passes', 0)} magnitude passes → "
        f"{len(payload['signals'])} signals",
        "",
        "---",
        "",
        "## Signals",
        "",
    ]

    if not payload["signals"]:
        lines.append("*No signals today.*")
    else:
        for s in payload["signals"]:
            conf_dot = "●"
            lines.extend(
                [
                    f"### {s['ticker']} — {s['signal']} {conf_dot} {s['confidence']} confidence",
                    f"Move probability: {s['p_magnitude']*100:.0f}% | "
                    f"Direction: {'up' if s['p_up'] > 0.5 else 'down'} (p_up={s['p_up']:.2f})",
                    f"Stock-adjusted threshold: ±{s['threshold_pct']:.1f}% over 5 days",
                    f"Key drivers: {', '.join(s['top_features'][:3])}",
                ]
            )
            if s.get("leakage_caveats"):
                lines.append(f"⚠️ Caveat: {', '.join(s['leakage_caveats'])}")
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## System health",
            "See monitoring/ for full monthly report.",
        ]
    )
    return "\n".join(lines)


def write(as_of: str, signals: list[Signal], candidates: list[str] | None = None) -> None:
    settings.WATCHLISTS_DIR.mkdir(parents=True, exist_ok=True)
    universe = fetch_ndx_members()
    magnitude_passes = len([s for s in signals if s.p_magnitude >= settings.STAGE_B_THRESHOLD])
    screening = {
        "universe_size": len(universe),
        "after_exclusions": len(candidates or []),
        "candidates_screened": len(candidates or []),
        "magnitude_passes": magnitude_passes,
    }
    payload = _build_payload(as_of, signals, screening)

    json_path = settings.WATCHLISTS_DIR / f"{as_of}.json"
    md_path = settings.WATCHLISTS_DIR / f"{as_of}.md"
    json_path.write_text(json.dumps(payload, indent=2))
    md_path.write_text(_render_markdown(payload))
    log.info("Watchlist written: %s and %s", json_path, md_path)
