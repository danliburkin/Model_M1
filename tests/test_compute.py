"""Tests for compute module."""

from __future__ import annotations

import math

from src.compute import bsm_delta, compute_iv


def test_compute_iv_valid_call():
    iv = compute_iv(mid_price=10.0, S=100.0, K=100.0, T=0.25, r=0.05, flag="call")
    assert iv is not None
    assert 0.01 < iv < 2.0


def test_compute_iv_arbitrage_violation():
    iv = compute_iv(mid_price=150.0, S=100.0, K=100.0, T=0.25, r=0.05, flag="call")
    assert iv is None


def test_compute_iv_zero_price():
    assert compute_iv(0, 100, 100, 0.25, 0.05, "call") is None


def test_bsm_delta_atm_call():
    d = bsm_delta(S=100, K=100, T=0.5, r=0.05, sigma=0.3, flag="call")
    assert 0.4 < d < 0.7


def test_bsm_delta_put_negative():
    d = bsm_delta(S=100, K=100, T=0.5, r=0.05, sigma=0.3, flag="put")
    assert d < 0
