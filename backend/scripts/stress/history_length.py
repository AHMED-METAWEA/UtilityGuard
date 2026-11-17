"""Does a longer training history give a better forecast? Same test days, different amounts of history.

The test slice is the last ten days of the window, with the same two heat days and two holidays whatever the window's length (the calendar picks them by their
distance from the end), so a model trained on 365 days and one trained on 75 are scored on identical days. Nothing here is tuned on those days: the only
thing that changes between runs is how many days come before them.

Each length runs in its own process so the memory of one run is not carried into the next (this is meant to be runnable on an 8 GB laptop).

    PYTHONPATH=. .venv/bin/python scripts/stress/history_length.py --lengths 75 180 365 --seeds 42
    PYTHONPATH=. .venv/bin/python scripts/stress/history_length.py --one 180 --seed 42      # a single run, prints JSON

Writes data/stress/history_length.json and ../docs/measurements/forecast-history-length.md.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def one(days: int, seed: int, now: datetime | None) -> dict:
    import app.ml as ml
    import app.ml.comparison as comparison
    from app.ml.verdict import assess
    from app.runtime import Runtime

    comparison.compare = lambda *a, **k: {}  # the five-model comparison is not what is being measured here, and it is the heavy part
    ml.HISTORY_DAYS = days
    engine = Runtime(seed).engine
    t0 = time.time()
    m = ml.train(engine, now=now)
    took = time.time() - t0
    out = {"days": days, "seed": seed, "train_seconds": round(took, 1), "peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576), "networks": {}}
    for net, e in m.evidence.items():
        v = assess(e["skill"], e["coverage"], e["nominal"], e["model"]["mape"], e["by_day_type"])
        out["networks"][net] = {
            "mape": e["model"]["mape"], "mae": e["model"]["mae"], "skill": e["skill"], "coverage": e["coverage"], "scenario_mape": e["scenario"]["mape"],
            "scenario_blind": e["scenario"]["mape_blind"], "train_days": e["train_days"], "verdict": v["label"],
            "by_day_type": {d["name"]: [d["days"], d["mape"]] for d in e["by_day_type"]}}
    out["failure_auc"] = m.failure_auc
    return out


def report(rows: list[dict], seeds: list[int]) -> str:
    out = ["# Forecast: does more history help?\n",
           f"Measured {datetime.now().strftime('%d %B %Y')} with `scripts/stress/history_length.py` (seeds {seeds}). Every row is scored on the same last ten days, "
           "including the same two heat days and two holidays; only the number of days before them changes. Nothing was tuned on those days. "
           "The data is simulated, so this says how the method behaves on this twin, not how it would on equipment in the field.\n"]
    for net in ("water", "electricity", "cooling"):
        out += [f"## {net.capitalize()}\n", "| History (days) | Seed | Test MAPE | Test MAE | Skill vs best baseline | Band coverage (80% intended) | Error on heat and holiday test days | Verdict | Train time | Peak memory |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in sorted(rows, key=lambda r: (r["seed"], r["days"])):
            n = r["networks"][net]
            out.append(f"| {r['days']} | {r['seed']} | {n['mape']:.2f}% | {n['mae']:.2f} | {n['skill']:.0%} | {n['coverage']:.0%} | {n['scenario_mape']:.2f}% | {n['verdict']} | {r['train_seconds']:.0f} s | {r['peak_mb']} MB |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lengths", type=int, nargs="*", default=[75, 180, 365])
    ap.add_argument("--seeds", type=int, nargs="*", default=[42])
    ap.add_argument("--one", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--now", default=None, help="ISO time to train as of, so runs on different days are comparable")
    ap.add_argument("--out", default=str(ROOT / "data" / "stress" / "history_length.json"))
    ap.add_argument("--report", default=str(ROOT.parent / "docs" / "measurements" / "forecast-history-length.md"))
    args = ap.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if args.one:
        print("RESULT " + json.dumps(one(args.one, args.seed, now)), flush=True)
        return
    rows = []
    for seed in args.seeds:
        for days in args.lengths:
            print(f"training on {days} days, seed {seed} ...", flush=True)
            r = subprocess.run([sys.executable, __file__, "--one", str(days), "--seed", str(seed), "--now", now.isoformat()], capture_output=True, text=True, cwd=ROOT,
                               env={**__import__("os").environ, "PYTHONPATH": str(ROOT), "LLM_OFFLINE": "1"})
            line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
            if not line:
                print("  failed:", r.stderr[-400:], flush=True)
                continue
            rows.append(json.loads(line[7:]))
            n = rows[-1]["networks"]
            print(f"  {rows[-1]['train_seconds']:.0f} s, {rows[-1]['peak_mb']} MB; MAPE " + ", ".join(f"{k} {v['mape']:.2f}%" for k, v in n.items()), flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(report(rows, args.seeds))
    print(report(rows, args.seeds))


if __name__ == "__main__":
    main()
