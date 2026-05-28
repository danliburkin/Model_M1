"""Tests for pull module."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from src.pull import PolygonRateLimiter, fetch_ndx_members


def test_rate_limiter_enforces_window():
    limiter = PolygonRateLimiter(max_calls=2, window=1.0)
    t0 = time.monotonic()
    limiter.wait()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.9


def test_fetch_ndx_members_uses_cache(tmp_path, monkeypatch):
    import settings

    cache = tmp_path / "ndx_members.json"
    cache.write_text('["AAPL", "MSFT"]')
    monkeypatch.setattr(settings, "NDX_CACHE_PATH", cache)
    monkeypatch.setattr(settings, "NDX_CACHE_DAYS", 7)
    members = fetch_ndx_members()
    assert "AAPL" in members
    assert "MSFT" in members


def test_redact_secrets():
    from src.pull import _redact_secrets

    with patch.dict(os.environ, {"POLYGON_API_KEY": "secret_key_abc123"}):
        url = "https://api.polygon.io/v3/foo?limit=5&apiKey=secret_key_abc123"
        assert "secret_key_abc123" not in _redact_secrets(url)
        assert "apiKey=[REDACTED]" in _redact_secrets(url)


def test_polygon_get_logs_and_returns(mock_requests):
    from src.pull import polygon_get

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True
    mock_requests.return_value = mock_resp

    with patch("src.pull.settings.POLYGON_API_KEY", "test_key"):
        r = polygon_get("https://api.polygon.io/v3/reference/options/contracts", {"limit": 1})
    assert r.status_code == 200


@pytest.fixture
def mock_requests():
    with patch("src.pull.requests.get") as m:
        with patch("src.pull.get_db") as dbm:
            dbm.return_value.execute = MagicMock()
            dbm.return_value.close = MagicMock()
            yield m
