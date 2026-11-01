"""Measures the detector's recall, false-alarm rate and operational precision with matched pairs.

Every injected fault is run twice on the same seed and start time: once with the fault and once without (the control). The control shows what the
detector says about the same noise when nothing is wrong, so a false alarm is attributed to the detector, not to the fault. Extra clean runs add
exposure hours, and benign scenarios (a demand surge, a holiday, a drifting sensor) are hard negatives that must not raise a fault incident.

Usage (from backend/, no network, no language model):
    PYTHONPATH=. .venv/bin/python scripts/stress/detection_pr.py --workers 8
    PYTHONPATH=. .venv/bin/python scripts/stress/detection_pr.py --quick          # a small run to check the harness

Writes data/stress/detection_pr.json and ../docs/measurements/detection-pr.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import multiprocessing as mp
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
WEEK0 = datetime(2026, 10, 26, tzinfo=timezone.utc)  # a Monday, so a week of starts covers the Friday and Saturday weekend
WARM = 120  # ticks before an injection: the detectors need history, and an alarm during start-up is not a fault of the detector
TICK_S = 2

# scenario id -> (the incident kind that is the right answer, parameter name, targets, severities, ticks to watch after the injection)
POSITIVES = {
    "scn-pipe-burst": ("leak", "targetAsset", "pipes", [0.15, 0.35, 0.7, 1.0], 450),
    "scn-hidden-leak": ("leak", "targetAsset", "pipes", [0.08, 0.15, 0.2, 0.3], 900),
    "scn-substation-trip": ("substation_trip", "targetAsset", "subs", [1.0], 450),
    "scn-load-shedding": ("substation_trip", "targetAsset", "subs", [1.0], 450),
    "scn-pump-failure": ("pump_failure", "targetAsset", "pumps", [1.0], 600),
    "scn-cooling-fault": ("cooling_fault", "targetAsset", "plants", [0.2, 0.45, 0.7, 1.0], 450),
    "scn-stuck-sensor": ("sensor_fault", "targetSensor", "pressure_sensors", [1.0], 450),
}
# scenarios that are not faults. The detector must not call them a leak, a trip, a pump failure or a cooling fault
NEGATIVES = {"scn-iftar-surge": {}, "scn-holiday": {}, "scn-sensor-drift": {"severity": 0.5}}
FAULT_KINDS = {"leak", "substation_trip", "pump_failure", "cooling_fault"}


# ---------------------------------------------------------------- worker side
_det = None


def _init(overrides: dict | None = None) -> None:
    global _det
    import app.detection as detection
    from app.detection import Detector
    from app.runtime import Runtime
    for name, value in (overrides or {}).items():  # to try a threshold without editing the detector: --set CREEP_MISMATCH=10
        setattr(detection, name, type(getattr(detection, name))(value))
    _det = Detector(Runtime(42))


def _targets(g, name: str) -> list[str]:
    if name == "pressure_sensors":
        return [f"{g.zone_pipe[z]}-pressure" for z in g.zone_pipe]
    kind = {"pipes": "pipe", "subs": "substation", "pumps": "pump", "plants": "plant"}[name]
    return [a.id for a in g.topology.assets if a.kind == kind]


def run_case(spec: dict) -> dict:
    """One run. spec: seed, start (iso), post (ticks after the injection), scenario/params (None for a clean run)."""
    from app.runtime import Runtime
    from app.scenarios import events_for
    rt = Runtime(spec["seed"])
    det = _det.clone(rt)
    seen: list[dict] = []
    det.on_incident.append(lambda inc, f, root: seen.append({"tick": rt.engine.tick, "kind": f.kind, "root": root, "incident": inc.id}))
    rt.tick_hooks.append(det.tick)
    start = datetime.fromisoformat(spec["start"])
    inject = WARM if spec.get("scenario") else None

    async def go() -> None:
        for i in range(WARM + spec["post"]):
            if inject is not None and i == inject:
                for ev in events_for(spec["scenario"], spec["params"], "stress"):
                    rt.add_event(ev)
            await rt.tick_once(start + timedelta(seconds=TICK_S * i))

    asyncio.run(go())
    inc_ids = {s["incident"] for s in seen}
    return {"spec": spec, "alarms": seen, "incidents": len(inc_ids), "ticks": WARM + spec["post"], "inject": inject}


# ---------------------------------------------------------------- statistics
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def poisson_ci(k: int, hours: float, per: float = 100.0) -> tuple[float, float, float]:
    """Exact (Garwood) 95% interval for an event rate per `per` hours."""
    from scipy.stats import chi2
    lo = 0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2
    hi = chi2.ppf(0.975, 2 * (k + 1)) / 2
    return k / hours * per, lo / hours * per, hi / hours * per


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


# ---------------------------------------------------------------- plan
def plan(args) -> list[dict]:
    from app.sim.topology import build
    g = build()
    rng = np.random.default_rng(args.seed)
    specs: list[dict] = []

    def start() -> str:
        return (WEEK0 + timedelta(hours=int(rng.integers(0, 168)), minutes=int(rng.integers(0, 60)))).isoformat()

    n = 0
    only = set(args.only.split(",")) if args.only else None
    for sc, (kind, pname, tset, sevs, post) in POSITIVES.items():
        if (only is not None and sc not in only) or args.starts == 0:
            continue
        targets = _targets(g, tset)
        if args.quick:
            targets = targets[:2]
        for tgt in targets:
            for sev in sevs:
                for _ in range(args.starts):
                    n += 1
                    params = {pname: tgt}
                    if sc in ("scn-pipe-burst", "scn-hidden-leak", "scn-cooling-fault"):
                        params["severity"] = sev
                    base = {"seed": args.seed * 1000 + n, "start": start(), "post": post}
                    specs.append({**base, "role": "fault", "scenario": sc, "params": params, "kind": kind, "target": tgt, "severity": sev})
                    specs.append({**base, "role": "control", "scenario": None, "params": {}, "kind": kind, "target": tgt, "severity": sev, "of": sc})
    for sc, params in NEGATIVES.items():
        if only is not None and sc not in only:
            continue
        for _ in range(args.negatives):
            n += 1
            specs.append({"seed": args.seed * 1000 + n, "start": start(), "post": 450, "role": "negative", "scenario": sc, "params": params})
    for _ in range(args.clean_runs):
        n += 1
        specs.append({"seed": args.seed * 1000 + n, "start": start(), "post": args.clean_ticks, "role": "clean", "scenario": None, "params": {}})
    return specs


# ---------------------------------------------------------------- analysis
def analyse(results: list[dict]) -> dict:
    def after_warm(r):
        return [a for a in r["alarms"] if a["tick"] > WARM]

    faults = [r for r in results if r["spec"]["role"] == "fault"]
    controls = {(r["spec"]["seed"]): r for r in results if r["spec"]["role"] == "control"}
    negatives = [r for r in results if r["spec"]["role"] == "negative"]
    cleans = [r for r in results if r["spec"]["role"] == "clean"]

    rows: dict[str, dict] = {}
    for r in faults:
        s = r["spec"]
        key = (s["scenario"], s["severity"])
        row = rows.setdefault(f"{s['scenario']}|{s['severity']}", {"scenario": s["scenario"], "severity": s["severity"], "n": 0, "kind_ok": 0, "any": 0,
                                                                    "wrong_kind": 0, "asset_ok": 0, "latency": [], "incidents": [], "control_alarms": 0})
        alarms = [a for a in r["alarms"] if a["tick"] >= r["inject"]]
        row["n"] += 1
        row["any"] += bool(alarms)
        right = [a for a in alarms if a["kind"] == s["kind"]]
        if right:
            row["kind_ok"] += 1
            row["latency"].append((right[0]["tick"] - r["inject"]) * TICK_S)
            row["asset_ok"] += any(s["target"] == a["root"] or s["target"].startswith(a["root"] + "-") for a in right)
        elif alarms:
            row["wrong_kind"] += 1
        row["incidents"].append(len({a["incident"] for a in alarms}))
        c = controls.get(s["seed"])
        row["control_alarms"] += len(after_warm(c)) if c else 0

    # exposure: every run without a fault contributes the hours after its warm-up
    def hours(rs):
        return sum((r["ticks"] - WARM) * TICK_S for r in rs) / 3600

    clean_like = cleans + list(controls.values())
    fa_clean = sum(len({a["incident"] for a in after_warm(r)}) for r in clean_like)
    hrs_clean = hours(clean_like)
    by_kind: dict[str, int] = {}
    for r in clean_like:
        for a in {a["incident"]: a for a in after_warm(r)}.values():
            by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1
    neg_rows = {}
    for r in negatives:
        s = r["spec"]
        row = neg_rows.setdefault(s["scenario"], {"n": 0, "fault_alarms": 0, "any_alarms": 0, "kinds": {}})
        al = [a for a in r["alarms"] if a["tick"] > WARM]
        row["n"] += 1
        row["any_alarms"] += bool(al)
        row["fault_alarms"] += any(a["kind"] in FAULT_KINDS for a in al)
        for a in al:
            row["kinds"][a["kind"]] = row["kinds"].get(a["kind"], 0) + 1
    return {"rows": list(rows.values()), "fa": {"incidents": fa_clean, "hours": hrs_clean, "by_kind": by_kind, "runs": len(clean_like)}, "negatives": neg_rows,
            "fault_runs": len(faults)}


def render(a: dict, args, took: float, commit: str) -> str:
    out: list[str] = []
    w = out.append
    w("# Detection: recall, false alarms and precision (matched pairs)\n")
    w(f"Measured {datetime.now().strftime('%d %B %Y')} at commit `{commit}` with `scripts/stress/detection_pr.py` (seed {args.seed}, {args.starts} start times per case, "
      f"{a['fault_runs']} fault runs, each with a control run on the same seed and start time). Took {took / 60:.1f} minutes, no network and no language model.\n")
    w("The twin is simulated, so these numbers describe the detector on the simulation, not on equipment in the field.\n")

    tot = sum(r["n"] for r in a["rows"])
    ok = sum(r["kind_ok"] for r in a["rows"])
    if tot == 0:
        return _false_alarm_report(a, out, args, took, commit)
    lo, hi = wilson(ok, tot)
    anyd = sum(r["any"] for r in a["rows"])
    w("## Recall\n")
    w(f"**Overall recall (the right kind of incident within the watch window): {pct(ok / tot)} ({ok} of {tot}), 95% interval {pct(lo)} to {pct(hi)}.** "
      f"Any incident at all: {pct(anyd / tot)}. A wrong kind of incident only: {sum(r['wrong_kind'] for r in a['rows'])}.\n")
    w("| Scenario | Severity | Runs | Recall | 95% interval | Right asset | Wrong kind | Median seconds to detect | Worst | Incidents per fault |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in sorted(a["rows"], key=lambda r: (r["scenario"], r["severity"])):
        lo, hi = wilson(r["kind_ok"], r["n"])
        lat = r["latency"]
        med = f"{np.median(lat):.0f}" if lat else "-"
        worst = f"{max(lat):.0f}" if lat else "-"
        w(f"| `{r['scenario']}` | {r['severity']} | {r['n']} | {pct(r['kind_ok'] / r['n'])} | {pct(lo)} to {pct(hi)} | {r['asset_ok']} of {r['kind_ok']} | {r['wrong_kind']} | {med} | {worst} | {np.mean(r['incidents']):.2f} |")
    split = [r for r in a["rows"] if np.mean(r["incidents"]) > 1.05]
    w("\n'Incidents per fault' above 1 means one injected fault opened more than one incident (the continuity problem in `docs/status.md`, known issue 1).\n")
    if split:
        w("Cases that split: " + ", ".join(f"`{r['scenario']}` severity {r['severity']} ({np.mean(r['incidents']):.2f})" for r in split) + ".\n")

    f = a["fa"]
    rate, rlo, rhi = poisson_ci(f["incidents"], f["hours"])
    w("## False alarms\n")
    w(f"Exposure: {f['hours']:.1f} simulated hours without any fault ({f['runs']} runs: extra clean runs and the controls of the fault runs), after a {WARM * TICK_S} second warm-up.\n")
    w(f"**False alarms: {f['incidents']} incidents in {f['hours']:.1f} hours = {rate:.2f} per 100 hours, 95% interval {rlo:.2f} to {rhi:.2f}.** "
      f"By kind: {f['by_kind'] or 'none'}.\n")
    if a["negatives"]:
        w("Hard negatives (a demand surge, a holiday, a drifting sensor are not faults):\n")
        w("| Scenario | Runs | Raised a fault incident (leak, trip, pump, cooling) | Raised any incident | Kinds raised |")
        w("| --- | --- | --- | --- | --- |")
        for sc, r in a["negatives"].items():
            lo, hi = wilson(r["fault_alarms"], r["n"])
            w(f"| `{sc}` | {r['n']} | {r['fault_alarms']} ({pct(r['fault_alarms'] / r['n'])}, up to {pct(hi)}) | {r['any_alarms']} | {r['kinds'] or '-'} |")
        w("")

    w("## Precision at a realistic fault rate\n")
    w("In the fault runs above most runs contain a fault, which flatters precision. In operation faults are rare, so precision is computed from the measured recall "
      "and the measured false-alarm rate at an assumed fault rate:\n")
    w("`precision = faults_per_100h x recall / (faults_per_100h x recall + false_alarms_per_100h)`\n")
    w("| Assumed fault rate | Expected true alarms per 100 h | False alarms per 100 h (measured, interval) | Precision |")
    w("| --- | --- | --- | --- |")
    for label, per100 in (("one fault a day", 100 / 24), ("one fault a week", 100 / 168), ("one fault a month", 100 / 720)):
        tp = per100 * ok / tot
        p = tp / (tp + rate) if tp + rate else 1.0
        plo = tp / (tp + rhi) if tp + rhi else 1.0
        phi = tp / (tp + rlo) if tp + rlo else 1.0
        w(f"| {label} | {tp:.2f} | {rate:.2f} ({rlo:.2f} to {rhi:.2f}) | **{pct(p)}** ({pct(plo)} to {pct(phi)}) |")
    w("")
    return "\n".join(out)


def _false_alarm_report(a: dict, out: list[str], args, took: float, commit: str) -> str:
    f = a["fa"]
    rate, rlo, rhi = poisson_ci(f["incidents"], f["hours"])
    out.clear()
    out += ["# Detection: false alarms on long clean runs\n",
            f"Measured {datetime.now().strftime('%d %B %Y')} at commit `{commit}` with `scripts/stress/detection_pr.py --starts 0 --negatives 0` (seed {args.seed}). "
            f"{f['runs']} runs of {args.clean_ticks * TICK_S / 3600:.1f} simulated hours each, started at random times across a week (so Fridays and Saturdays are in). Took {took / 60:.1f} minutes.\n",
            f"**{f['incidents']} incidents in {f['hours']:.0f} hours = {rate:.2f} per 100 hours, 95% interval {rlo:.2f} to {rhi:.2f}.** By kind: {f['by_kind'] or 'none'}.\n",
            "At that upper bound, precision at one real fault a week would be no lower than "
            f"{(100 / 168 * 0.93) / (100 / 168 * 0.93 + rhi):.0%} (recall taken as 93%, see `detection-pr.md`)."]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--starts", type=int, default=2, help="start times per (scenario, target, severity)")
    ap.add_argument("--negatives", type=int, default=12, help="runs per benign scenario")
    ap.add_argument("--clean-runs", type=int, default=100, help="extra clean runs")
    ap.add_argument("--clean-ticks", type=int, default=1800, help="ticks after warm-up per clean run (1800 = one hour)")
    ap.add_argument("--only", default="", help="comma separated scenario ids to run (positives and negatives)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override a detector constant for this run")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "stress" / "detection_pr.json"))
    ap.add_argument("--report", default=str(ROOT.parent / "docs" / "measurements" / "detection-pr.md"))
    args = ap.parse_args()
    if args.quick:
        args.starts, args.negatives, args.clean_runs, args.clean_ticks = 1, 2, 4, 450
    specs = plan(args)
    print(f"{len(specs)} runs on {args.workers} workers", flush=True)
    t0 = time.time()
    results: list[dict] = []
    overrides = dict(x.split("=", 1) for x in args.set)
    with mp.get_context("spawn").Pool(args.workers, initializer=_init, initargs=(overrides,)) as pool:
        for i, r in enumerate(pool.imap_unordered(run_case, specs, chunksize=2), 1):
            results.append(r)
            if i % 25 == 0 or i == len(specs):
                print(f"  {i}/{len(specs)} ({time.time() - t0:.0f}s)", flush=True)
    analysis = analyse(results)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    text = render(analysis, args, time.time() - t0, commit)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"args": vars(args), "commit": commit, "analysis": analysis, "results": results}, default=str))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
