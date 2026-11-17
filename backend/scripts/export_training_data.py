"""Writes a copy of the data the models were trained and scored on, with a manifest that says exactly how it was made.

    PYTHONPATH=. .venv/bin/python scripts/export_training_data.py --days 365          # data/exports/training-365d/
    PYTHONPATH=. .venv/bin/python scripts/export_training_data.py --days 75 --out /somewhere

What is written (CSV, gzip):
- `forecast_<network>.csv.gz`: one row per district per half hour of the window. The target `y` and `yn` (the target in units of the district's base demand), every
  feature the model sees, `split` (dev or test), `fold` (the blocked cross-validation fold of a dev row, -1 for test), and `p`, the model's prediction: out-of-fold
  for dev rows (the model that predicted it never saw that block), from the final model for test rows.
- `failure_model.csv.gz`: the synthetic maintenance history the failure model is trained on, with the hazard it was drawn from. It is simulated by construction.
- `detector_training.csv.gz`: the fortnight of sensor history the Isolation Forest of the detector is fitted on.
- `MANIFEST.json`: seed, window, the calendar of heat and holiday days, hyper-parameters, features, row counts, the git commit and a SHA-256 for every file.
- `README.md`: a data dictionary.

The data is simulated by the twin (seeded), so a copy is a copy of the simulation, not of any real network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="history length in days (default: FORECAST_HISTORY_DAYS or 75)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    ap.add_argument("--with-comparison", action="store_true", help="also run the five-model comparison (slow, heavy on memory, not needed for the data)")
    args = ap.parse_args()

    import app.ml as ml
    import app.ml.comparison as comparison
    from app.runtime import Runtime
    from app.sim.engine import CALIBRATION_START

    if args.days:
        ml.HISTORY_DAYS = args.days
    if not args.with_comparison:
        comparison.compare = lambda *a, **k: {}
    days = ml.HISTORY_DAYS
    out = Path(args.out) if args.out else ROOT / "data" / "exports" / f"training-{days}d"
    out.mkdir(parents=True, exist_ok=True)

    engine = Runtime(args.seed).engine
    now = datetime.now(timezone.utc)
    capture: dict[str, pd.DataFrame] = {}
    m = ml.train(engine, now=now, capture=capture)

    files: dict[str, dict] = {}

    def write(name: str, frame: pd.DataFrame) -> None:
        path = out / name
        frame.to_csv(path, index=False, compression="gzip", float_format="%.5g")
        files[name] = {"rows": int(len(frame)), "columns": list(frame.columns), "bytes": path.stat().st_size, "sha256": sha(path)}

    for net in ml.NETWORKS:
        f = capture[net].copy()
        f["ts"] = pd.to_datetime(f["ts"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        keep = ["ts", "zone", "split", "fold", "y", "yn", "p", *ml.FEATURES, "temp_fc", "temp_blind", "heat"]
        write(f"forecast_{net}.csv.gz", f[[c for c in keep if c in f.columns]])
    write("failure_model.csv.gz", capture["failure"])

    sensors = engine.g.topology.sensors
    hist = engine.history(CALIBRATION_START, 14 * 24, 30)
    detector = pd.DataFrame([{"ts": t.strftime("%Y-%m-%dT%H:%M:%SZ"), **{s.id: r[s.id] for s in sensors}} for t, r in hist])
    write("detector_training.csv.gz", detector)

    end = ml.window_end(now)
    start = end - pd.Timedelta(days=days)
    cal = ml.scenario_calendar(args.seed, start.astimezone(ml.CAIRO).date(), days)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    manifest = {
        "written_at": now.isoformat(), "git_commit": commit, "seed": args.seed, "model_version": ml.MODEL_VERSION,
        "window": {"history_days": days, "test_days": ml.TEST_DAYS, "start": start.isoformat(), "end": end.isoformat(), "step_minutes": 30, "folds": ml.FOLDS},
        "calendar": [{"date": d.isoformat(), "heat_peak_c": v[0] or None, "holiday_factor": v[1] or None} for d, v in sorted(cal.items())],
        "features": ml.FEATURES, "networks": list(ml.NETWORKS),
        "demand_model": "lightgbm.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20), target in units of the district's base demand",
        "results_on_the_test_days": {n: {"mape": m.evidence[n]["model"]["mape"], "mae": m.evidence[n]["model"]["mae"], "skill_vs_best_baseline": m.evidence[n]["skill"],
                                         "band_coverage": m.evidence[n]["coverage"], "band_nominal": m.evidence[n]["nominal"]} for n in ml.NETWORKS},
        "failure_model_auc_on_its_own_synthetic_hazard": m.failure_auc,
        "simulated": True, "files": files,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    (out / "README.md").write_text(README.format(days=days, test=ml.TEST_DAYS, folds=ml.FOLDS, features=", ".join(ml.FEATURES)))
    print(f"wrote {len(files)} files to {out}")
    for n, f in files.items():
        print(f"  {n}: {f['rows']:,} rows, {f['bytes'] / 1e6:.1f} MB")


README = """# Training data copy

A copy of the data Care Grid's models were trained and scored on, made by `scripts/export_training_data.py`. `MANIFEST.json` says how: seed, window, the
calendar of heat and holiday days, the hyper-parameters, the git commit, and a SHA-256 for every file. **The data is simulated by the twin (seeded), so this is a
copy of the simulation and not of any real network.**

## forecast_<network>.csv.gz (water, electricity, cooling)

One row per district per half hour of the {days} day window. The last {test} days are the test slice; everything before is the development window.

| Column | Meaning |
| --- | --- |
| `ts` | Time, UTC. Local time is Africa/Cairo. |
| `zone` | District id (`zone-gov`, `zone-cbd`, ...). |
| `split` | `dev` (used for fitting and for the cross-validated predictions) or `test` (never used to fit or to choose anything). |
| `fold` | The blocked cross-validation fold of a `dev` row ({folds} folds of consecutive days), `-1` for `test`. |
| `y` | What happened: the district's demand (m3/h for water, MW for electricity and cooling). |
| `yn` | `y` divided by the district's base demand (`zone_base`), the unit the model works in. |
| `p` | The model's prediction, in the units of `y`. For `dev` rows it is out-of-fold (a model that never saw that block predicted it). For `test` rows it is the final model. |
| model inputs | {features} |
| `temp_fc` | The temperature the model was given: the true ambient plus the weather feed's error. |
| `temp_blind` | The same feed for a model that never heard of a heat wave. |
| `heat` | 1 on a heat-wave day. `holiday` is the holiday's residential factor, 0 on a normal day. |

`lag_24h` and `lag_168h` are the district's demand one day and one week earlier on a normal day, divided by `zone_base`.

## failure_model.csv.gz

The synthetic maintenance history the failure model is trained on: `age_years, run_hours_k, vibration_trend, pressure_variance, load_factor, temp_excess`, the `hazard`
(probability of failure the label was drawn from) and `failed` (the drawn label). It is simulated by construction, so the model's score on it measures how well it
recovers the hazard, not field accuracy.

## detector_training.csv.gz

The fortnight of half-hourly sensor readings (one column per sensor) the detector's Isolation Forest is fitted on.
"""

if __name__ == "__main__":
    main()
