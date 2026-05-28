"""Tests for output module."""

from __future__ import annotations

import json
from pathlib import Path

import settings
from src.model import Signal
from src.output import write


def test_write_json_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WATCHLISTS_DIR", tmp_path)
    monkeypatch.setattr("src.output.fetch_ndx_members", lambda: ["NVDA", "AAPL"])

    class FakeCon:
        def execute(self, *args, **kwargs):
            class R:
                def fetchone(self):
                    return (20.0, None, None, None)

            return R()

        def close(self):
            pass

    monkeypatch.setattr("src.output.get_db", lambda: FakeCon())
    signals = [
        Signal(
            ticker="NVDA",
            activity_score=2.1,
            p_magnitude=0.68,
            p_up=0.31,
            signal="SHORT",
            confidence="medium",
            threshold_pct=5.1,
            top_features=["skew_pct_rank_252d"],
            leakage_caveats=["forward_earnings_calendar"],
        )
    ]
    write("2026-05-24", signals, candidates=["NVDA", "AAPL"])

    json_path = tmp_path / "2026-05-24.json"
    md_path = tmp_path / "2026-05-24.md"
    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["date"] == "2026-05-24"
    assert payload["signals"][0]["ticker"] == "NVDA"
    assert "NVDA" in md_path.read_text()
