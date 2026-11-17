"""The failure model: does it recover the hazard it was trained on, are its scores sensible, and does more wear never mean less risk (stress-test items R-1, R-2, R-3).

    PYTHONPATH=. .venv/bin/python scripts/stress/failure_model.py [--seed 42]

The model is trained on a synthetic hazard (`app.ml.failure_hazard`), so what this measures is how well it recovers that hazard, not how well it would predict real failures; the
risk shown in the app is a ranking score, not a calibrated chance of failing. Criteria, written before the run:

  FM-1  Spearman correlation between the model's score and the true hazard on 20,000 fresh samples is at least 0.95.
  FM-2  In ten equal-count bins of the score, the mean score is within 0.03 of the mean true hazard in every bin (a calibration check against the hazard, not against noisy labels).
  FM-3  The AUC on fresh labels is within 0.02 of the AUC the true hazard itself achieves (the ceiling for this noise).
  FM-4  Monotonic: sweeping one feature over its training range with the others fixed (400 random base points, 25 steps), at most 2% of the steps lower the score by more than
        0.02, and none lowers it by more than 0.10. Every term of the hazard rises with every feature, so a falling score is a model artefact.
  FM-5  Scores stay in [0, 1], and an asset at the worst corner of the feature space scores higher than one at the best corner.
Exits 1 when a criterion fails.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from app import ml

LO = np.array([0, 0, 0, 0, 0.3, 0.0])  # the training range of each feature (age, run hours, vibration trend, pressure variance, load factor, temperature excess)
HI = np.array([25, 60, 3.0, 2.5, 1.15, 15.0])


def sample(rng: np.random.Generator, n: int) -> np.ndarray:
    return np.column_stack([rng.uniform(0, 25, n), rng.uniform(0, 60, n), rng.gamma(1.2, 0.35, n), rng.gamma(1.0, 0.3, n), rng.uniform(0.3, 1.15, n), np.maximum(rng.normal(0, 6, n), 0)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    clf, auc_test, importances = ml._train_failure(args.seed)
    rng = np.random.default_rng(args.seed + 999)
    X = sample(rng, 20000)
    p = clf.predict_proba(X)[:, 1]
    h = ml.failure_hazard(X)
    y = rng.uniform(size=len(X)) < h
    bad = 0

    rho = float(spearmanr(p, h)[0])
    ok = rho >= 0.95
    bad += not ok
    print(f"FM-1  Spearman(score, true hazard) = {rho:.4f}  {'PASS' if ok else 'FAIL'}")

    order = np.argsort(p)
    gaps = []
    for chunk in np.array_split(order, 10):
        gaps.append(float(p[chunk].mean() - h[chunk].mean()))
    ok = max(abs(g) for g in gaps) <= 0.03
    bad += not ok
    print(f"FM-2  calibration gap per decile (score minus true hazard): {[round(g, 3) for g in gaps]}  max {max(abs(g) for g in gaps):.3f}  {'PASS' if ok else 'FAIL'}")

    auc_model, auc_ceiling = float(roc_auc_score(y, p)), float(roc_auc_score(y, h))
    ok = auc_ceiling - auc_model <= 0.02
    bad += not ok
    print(f"FM-3  AUC {auc_model:.4f} against the hazard's own {auc_ceiling:.4f} (training-split AUC {auc_test:.4f})  {'PASS' if ok else 'FAIL'}")

    base = sample(rng, 400)
    steps = drops = big = 0
    worst = 0.0
    per_feature = {}
    for j, name in enumerate(ml.FAIL_FEATURES):
        grid = np.linspace(LO[j], HI[j], 25)
        f_steps = f_drops = 0
        for b in base:
            rows = np.tile(b, (len(grid), 1))
            rows[:, j] = grid
            s = clf.predict_proba(rows)[:, 1]
            d = np.diff(s)
            steps += len(d)
            f_steps += len(d)
            lower = d < -0.02
            drops += int(lower.sum())
            f_drops += int(lower.sum())
            big += int((d < -0.10).sum())
            worst = min(worst, float(d.min()))
        per_feature[name] = f_drops / f_steps
    ok = drops / steps <= 0.02 and big == 0
    bad += not ok
    print(f"FM-4  steps that lower the score by more than 0.02: {drops}/{steps} = {drops / steps:.2%} (largest fall {worst:.3f}, falls over 0.10: {big})  {'PASS' if ok else 'FAIL'}")
    print("      share of falling steps per feature:", {k: f"{v:.1%}" for k, v in per_feature.items()})

    best, worst_corner = clf.predict_proba(LO[None, :])[0, 1], clf.predict_proba(HI[None, :])[0, 1]
    ok = bool(p.min() >= 0 and p.max() <= 1 and worst_corner > best)
    bad += not ok
    print(f"FM-5  scores in [{p.min():.4f}, {p.max():.4f}]; best corner {best:.4f}, worst corner {worst_corner:.4f}  {'PASS' if ok else 'FAIL'}")
    print("      importances:", [(k, round(v, 3)) for k, v in importances])
    print(f"\n{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
