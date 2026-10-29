"""Offline model comparison. Nothing here changes the production predictor or its RNG."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def ensemble_weights(oof_matrix: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Convex LGBM/RF weights minimising aggregate dev OOF MAE on a fixed 0.01 grid.

    Accepts only the two OOF prediction columns and dev targets, never test data.
    Ties prefer LightGBM. Weight fitting reuses OOF data (it is not nested CV).
    """
    matrix, target = np.asarray(oof_matrix), np.asarray(y)
    if matrix.ndim != 2 or matrix.shape != (len(target), 2) or not len(target):
        raise ValueError("Expected two OOF columns and a nonempty matching target")
    grid = np.linspace(1, 0, 101)
    errors = [np.mean(np.abs(matrix @ np.array([w, 1 - w]) - target)) for w in grid]
    weight = grid[int(np.argmin(errors))]
    return np.array([weight, 1 - weight])


def block_bootstrap(y: np.ndarray, live: np.ndarray, other: np.ndarray, days, seed: int = 731) -> float:
    """Share of 1,000 paired whole-day resamples with strictly lower live MAE.

    A tie is not a win, including the live model's self-comparison. This is a
    bootstrap win share, not a classical p-value or a posterior probability.
    """
    _, codes = np.unique(np.asarray(days), return_inverse=True)
    count = np.bincount(codes)
    delta = np.bincount(codes, weights=np.abs(live - y) - np.abs(other - y))
    draws = np.random.default_rng(seed).integers(0, len(count), size=(1000, len(count)))
    return float(np.mean(delta[draws].sum(axis=1) / count[draws].sum(axis=1) < 0))


def seasonal_predict(fit: pd.DataFrame, part: pd.DataFrame, per_day: int, timezone) -> np.ndarray:
    """Daily additive seasonal exponential smoothing, independently per zone on yn.

    Uses only training targets and calendar slots, with fixed alpha=.15,
    gamma=.10 and no trend or exogenous features. Missing fold slots are skipped,
    never filled from held-out targets. Blocked CV can train on both sides of a
    block, exactly as the production model does; this is not rolling-origin CV.
    """
    def slots(frame):
        local = frame.ts.dt.tz_convert(timezone)
        return ((local.dt.hour * 60 + local.dt.minute) * per_day // 1440).to_numpy()

    prediction = np.empty(len(part))
    for zone in fit.zone.unique():
        history = fit[fit.zone == zone].sort_values("ts")
        values, slot = history.yn.to_numpy(), slots(history)
        # Initialise on the first available training day only.
        initial = min(per_day, len(values))
        level = float(values[:initial].mean())
        seasonal = np.zeros(per_day)
        seasonal[slot[:initial]] = values[:initial] - level
        for value, s in zip(values[initial:], slot[initial:]):
            previous = level
            level = .15 * (value - seasonal[s]) + .85 * level
            seasonal[s] = .10 * (value - previous) + .90 * seasonal[s]
        pick = (part.zone == zone).to_numpy()
        prediction[pick] = (level + seasonal[slots(part[pick])]) * part.zone_base.to_numpy()[pick]
    return prediction


def compare(dev, te, block, live_oof, agg, best_baseline, live_seconds, seed, per_day):
    # Import lazily to keep the production training module independent of this path.
    from app.ml import CAIRO, FEATURES, FOLDS, _stats

    tests = {"lightgbm": agg.p.to_numpy()}
    dev_agg = dev.assign(lightgbm=live_oof).groupby("ts")[["y", "lightgbm"]].sum()
    seconds = {"lightgbm": live_seconds}
    rf_params = dict(n_estimators=200, max_depth=14, min_samples_leaf=2,
                     max_samples=.55, n_jobs=-1, random_state=seed + 103)
    factories = {
        "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "random_forest": lambda: RandomForestRegressor(**rf_params),
        "seasonal_smoothing": None,
    }

    def predict(model, part):
        inputs = part[FEATURES].copy()
        inputs["temperature"] = part.temp_fc.to_numpy()
        return model.predict(inputs) * part.zone_base.to_numpy()

    for name, factory in factories.items():
        elapsed = 0.0

        def fit_predict(training, holdout):
            nonlocal elapsed
            start = time.perf_counter()
            if factory is None:
                result = seasonal_predict(training, holdout, per_day, CAIRO)
                elapsed += time.perf_counter() - start
                return result
            fitted = factory().fit(training[FEATURES], training.yn)
            elapsed += time.perf_counter() - start
            return predict(fitted, holdout)

        test_prediction = fit_predict(dev, te)
        oof = np.empty(len(dev))
        for k in range(FOLDS):
            oof[block == k] = fit_predict(dev[block != k], dev[block == k])
        seconds[name] = elapsed
        tests[name] = te.assign(candidate=test_prediction).groupby("ts").candidate.sum().to_numpy()
        dev_agg[name] = dev.assign(candidate=oof).groupby("ts").candidate.sum()

    start = time.perf_counter()
    weights = ensemble_weights(dev_agg[["lightgbm", "random_forest"]].to_numpy(), dev_agg.y.to_numpy())
    seconds["ensemble"] = time.perf_counter() - start
    tests["ensemble"] = np.column_stack([tests["lightgbm"], tests["random_forest"]]) @ weights
    dev_agg["ensemble"] = dev_agg[["lightgbm", "random_forest"]].to_numpy() @ weights
    oof_mae = {name: _stats(dev_agg.y.to_numpy(), dev_agg[name].to_numpy())["mae"] for name in tests}
    selected = min(oof_mae, key=oof_mae.get)
    kinds = ["gradient boosting", "linear", "random forest", "statistical", "ensemble"]
    params = [
        "300 trees; learning_rate=.05; num_leaves=31; min_child_samples=20",
        "StandardScaler + Ridge(alpha=1); FEATURES only",
        "200 trees; depth=14; min_leaf=2; max_samples=.55; n_jobs=-1",
        f"Additive daily seasonality ({per_day} slots); alpha=.15; gamma=.10; no trend/features",
        f"LGBM={weights[0]:.2f}; RF={weights[1]:.2f}; dev OOF MAE grid, step=.01",
    ]
    local = agg.index.tz_convert(CAIRO)
    y = agg.y.to_numpy()
    rows = []
    for (name, prediction), kind, description in zip(tests.items(), kinds, params):
        stats = _stats(y, prediction)
        rows.append(dict(
            name=name, kind=kind, **stats, oof_mae=oof_mae[name],
            skill_vs_best_baseline=1 - stats["mae"] / best_baseline,
            fit_seconds=seconds[name], is_live=name == "lightgbm", is_selected=name == selected,
            params=description, p_lgbm_better=block_bootstrap(y, tests["lightgbm"], prediction, local.date),
            hourly_mape=[_stats(y[local.hour == h], prediction[local.hour == h])["mape"] for h in range(24)],
            weights=dict(zip(("lightgbm", "random_forest"), map(float, weights))) if name == "ensemble" else {},
            weights_source="dev_oof" if name == "ensemble" else "",
        ))
    return dict(
        comparison=rows, selected=selected, selected_matches_live=selected == "lightgbm",
        comparison_note=(
            "Same dev/test split and five blocked folds; train on other dev blocks, including later blocks (not rolling-origin). "
            "Feature models use FEATURES and normalised zone demand; statistical model uses only zone targets and daily slots. "
            "Selected by aggregate-by-timestamp dev OOF MAE. Ensemble weights use that same OOF data, not nested CV or test data. "
            "Production remains LightGBM, regardless of selection. Test metrics are uncorrected network totals. "
            "LightGBM bootstrap wins: 1,000 paired whole Cairo-day resamples, seed 731; ties are not wins (self=0). "
            "This win share is not a p-value; ten test days give limited evidence. "
            "Fit seconds sum full-dev and five fold fits; ensemble excludes component fits; statistical timing includes prediction."
        ),
    )
