"""Tests for model module."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model import PurgedKFold, calibrate_k


def test_purged_kfold_produces_splits():
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    pkf = PurgedKFold(n_splits=3, horizon=5, embargo=2)
    splits = list(pkf.split(dates.values, groups=dates.values))
    assert len(splits) == 3
    for train_idx, test_idx in splits:
        assert len(test_idx) > 0
        assert not set(train_idx) & set(test_idx)


def test_calibrate_k_returns_reasonable():
    rng = np.random.default_rng(0)
    returns = rng.normal(0, 0.02, 500)
    sigmas = np.full(500, 0.02)
    k = calibrate_k("TEST", returns, sigmas, target=0.30)
    assert 0.5 <= k <= 3.0
