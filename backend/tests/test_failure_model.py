"""The failure model recovers the hazard it was trained on, and more wear never lowers the risk (scripts/stress/failure_model.py has the long version)."""

import numpy as np
import pytest
from scipy.stats import spearmanr

from app import ml


@pytest.fixture(scope="module")
def trained():
    clf, auc, importances = ml._train_failure(42)
    return clf, auc, importances


def _sample(rng, n):
    return np.column_stack([rng.uniform(0, 25, n), rng.uniform(0, 60, n), rng.gamma(1.2, 0.35, n), rng.gamma(1.0, 0.3, n), rng.uniform(0.3, 1.15, n), np.maximum(rng.normal(0, 6, n), 0)])


def test_the_score_follows_the_hazard_it_was_trained_on(trained):
    clf = trained[0]
    X = _sample(np.random.default_rng(5), 5000)
    p, h = clf.predict_proba(X)[:, 1], ml.failure_hazard(X)
    assert spearmanr(p, h)[0] > 0.95
    order = np.argsort(p)
    assert all(abs(p[c].mean() - h[c].mean()) < 0.04 for c in np.array_split(order, 10))  # calibrated against the hazard in every decile
    assert trained[1] > 0.82


def test_more_wear_never_lowers_the_score(trained):
    clf = trained[0]
    lo, hi = np.array([0, 0, 0, 0, 0.3, 0.0]), np.array([25, 60, 3.0, 2.5, 1.15, 15.0])
    base = _sample(np.random.default_rng(6), 60)
    for j in range(6):
        grid = np.linspace(lo[j], hi[j], 25)
        for b in base:
            rows = np.tile(b, (len(grid), 1))
            rows[:, j] = grid
            assert np.all(np.diff(clf.predict_proba(rows)[:, 1]) >= -1e-9), ml.FAIL_FEATURES[j]


def test_importances_are_shares_that_add_up_to_one(trained):
    imp = trained[2]
    assert [k for k, _ in imp] and abs(sum(v for _, v in imp) - 1) < 1e-9 and {k for k, _ in imp} == set(ml.FAIL_FEATURES)
