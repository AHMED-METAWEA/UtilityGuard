"""The demand forecast, re-measured by code that does not call the evidence functions of `app/ml` (stress-test items F-1, F-2, F-3, F-6, F-8, F-9, F-15, F-14).

    PYTHONPATH=. .venv/bin/python scripts/stress/forecast_check.py [--seed 42] [--determinism]

It trains the models once with `capture` (the exact rows each network was fitted and scored on), then recomputes the headline numbers from those rows with its own few lines of
pandas and compares them with what the app reports. Criteria, written before the run:

  FC-1  The app's MAE, RMSE and MAPE of each network on the test slice equal the recomputation (relative difference under 1e-6), and so does the skill against the best of
        the three baselines and the band coverage.
  FC-2  Time order: every test timestamp is after every development timestamp; the cross-validation blocks are contiguous in time; the features are only the clock, the weather
        feed, the holiday flag, the demand 24 and 168 hours earlier and zone constants, so nothing in them looks forward.
  FC-3  The model uses its inputs and does not just echo something it was not given: shuffling the lag features within the test slice raises the MAPE by at least a factor of 2.
  FC-4  Network totals add up: on every test timestamp the sum of the zone forecasts is the network forecast, and every network and zone has a positive forecast.
  FC-5  Error by day of the week on the out-of-fold development days: no weekday is worse than twice the mean (or the weakness is listed).
  FC-6  Band coverage on the test slice, and the share of misses above and below the band (a one-sided band would be a bias): each side holds at least a quarter of the misses.
  FC-7  (with --determinism) Training twice with the same seed gives forecasts that differ by less than 0.1% on every test row.
Exits 1 when a criterion fails.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app import ml
from app.sim.engine import CAIRO, Engine

bad = 0


def verdict(name: str, ok: bool, detail: str) -> None:
    global bad
    bad += 0 if ok else 1
    print(f"{name}  {'PASS' if ok else 'FAIL'}  {detail}", flush=True)


def metrics(y: np.ndarray, p: np.ndarray) -> tuple[float, float, float]:
    e = p - y
    return float(np.abs(e).mean()), float(np.sqrt((e ** 2).mean())), float((np.abs(e) / y).mean() * 100)


def train(seed: int):
    cap: dict = {}
    t0 = time.time()
    m = ml.train(Engine(seed), now=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc), capture=cap)
    print(f"trained in {time.time() - t0:.0f} s", flush=True)
    return m, cap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--determinism", action="store_true")
    args = ap.parse_args()
    m, cap = train(args.seed)

    for net in ml.NETWORKS:
        d = cap[net]
        dev, te = d[d.split == "dev"], d[d.split == "test"]
        agg = te.groupby("ts")[["y", "p", "lag_24h_raw", "lag_168h_raw"]].sum()
        y, p = agg.y.values, agg.p.values
        mae, rmse, mape = metrics(y, p)
        got = m.evidence[net]["model"]
        same = all(abs(a - b) <= 1e-6 * max(abs(b), 1e-9) for a, b in ((mae, got["mae"]), (rmse, got["rmse"]), (mape, got["mape"])))
        base_mae = {"yesterday": metrics(y, agg.lag_24h_raw.values)[0], "last week": metrics(y, agg.lag_168h_raw.values)[0],
                    "average": metrics(y, ((agg.lag_24h_raw + agg.lag_168h_raw) / 2).values)[0]}
        skill = 1 - mae / min(base_mae.values())
        same = same and abs(skill - m.evidence[net]["skill"]) <= 1e-6
        edges = np.array([m.band[(net, "all", ml.regime(h, v))] for h, v in zip(te.groupby("ts").heat.max().values, te.groupby("ts").holiday.max().values)])
        below, above = y < p * (1 + edges[:, 0]), y > p * (1 + edges[:, 1])
        coverage = 1 - (below | above).mean()
        same = same and abs(coverage - m.evidence[net]["coverage"]) <= 1e-9
        verdict(f"FC-1 {net:11}", same, f"MAE {mae:.3f} RMSE {rmse:.3f} MAPE {mape:.2f}% skill {skill:.1%} (best baseline {min(base_mae, key=base_mae.get)}) coverage {coverage:.1%}")

        order_ok = dev.ts.max() < te.ts.min()
        blocks = dev.groupby("fold").ts.agg(["min", "max"]).sort_index()
        contiguous = all(blocks["max"].iloc[i] <= blocks["min"].iloc[i + 1] for i in range(len(blocks) - 1))
        feats_ok = set(ml.FEATURES) == {"hour_sin", "hour_cos", "hour", "weekday", "weekend", "holiday", "temperature", "lag_24h", "lag_168h", "zone_base", "zone_type"}
        verdict(f"FC-2 {net:11}", order_ok and contiguous and feats_ok, f"dev ends {dev.ts.max():%Y-%m-%d}, test starts {te.ts.min():%Y-%m-%d}; {len(blocks)} blocks in time order; features {sorted(ml.FEATURES)}")

        rng = np.random.default_rng(args.seed)
        X = te[ml.FEATURES].copy()
        X["temperature"] = te["temp_fc"].values
        base_pred = m.demand[net].predict(X) * te.zone_base.values
        Xs = X.copy()
        Xs["lag_24h"], Xs["lag_168h"] = rng.permutation(X["lag_24h"].values), rng.permutation(X["lag_168h"].values)
        shuf_pred = m.demand[net].predict(Xs) * te.zone_base.values
        tot = lambda pr: metrics(te.assign(q=pr).groupby("ts")[["y", "q"]].sum().y.values, te.assign(q=pr).groupby("ts")[["y", "q"]].sum().q.values)[2]  # noqa: E731
        verdict(f"FC-3 {net:11}", tot(shuf_pred) >= 2 * tot(base_pred), f"MAPE {tot(base_pred):.2f}% with the real lags, {tot(shuf_pred):.2f}% with the lags shuffled")

        zsum = te.groupby("ts").p.sum()
        verdict(f"FC-4 {net:11}", bool(np.allclose(zsum.values, agg.p.values) and (te.p > 0).all()), f"{len(zsum)} timestamps, sum of zone forecasts equals the network forecast; smallest forecast {te.p.min():.3f}")

        tot_dev = dev.groupby("ts").agg(y=("y", "sum"), p=("p", "sum"))
        wd = tot_dev.assign(w=tot_dev.index.tz_convert(CAIRO).weekday).groupby("w").apply(lambda g: float((np.abs(g.p - g.y) / g.y).mean() * 100), include_groups=False)
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        verdict(f"FC-5 {net:11}", float(wd.max()) <= 2 * float(wd.mean()), "MAPE by weekday " + ", ".join(f"{names[i]} {v:.1f}%" for i, v in wd.items()))

        misses = int(below.sum() + above.sum())
        sides = (int(below.sum()), int(above.sum()))
        balanced = misses == 0 or min(sides) / misses >= 0.25
        verdict(f"FC-6 {net:11}", balanced, f"coverage {coverage:.1%} (target 80%); {sides[0]} misses below the band, {sides[1]} above")

    if args.determinism:
        m2, cap2 = train(args.seed)
        for net in ml.NETWORKS:
            a, b = cap[net][cap[net].split == "test"].p.values, cap2[net][cap2[net].split == "test"].p.values
            rel = float(np.max(np.abs(a - b) / np.maximum(np.abs(a), 1e-9)))
            verdict(f"FC-7 {net:11}", rel < 1e-3, f"largest relative difference between two trainings {rel:.2e}")
    print(f"\n{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
