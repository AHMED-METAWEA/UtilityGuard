"""The simulation everything else is measured on (stress-test items S-1, S-2, S-3, S-6, S-8, S-9, S-10, S-11, S-12, S-13).

    PYTHONPATH=. .venv/bin/python scripts/stress/simulation.py [--quick]

Each check states its criterion before it runs. No network, no language model. Exits 1 when any check fails.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import math
import resource
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

START = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)  # a Friday, 11:00 in Cairo
STEP = timedelta(seconds=30)


def run_hash(seed: int, ticks: int, scenario: str | None = None) -> str:
    from app.scenarios import BY_ID, events_for
    from app.sim.engine import Engine
    e = Engine(seed)
    if scenario:
        for ev in events_for(scenario, {}, "stress"):
            e.add_event(ev)
    h = hashlib.sha256()
    ts = START
    for _ in range(ticks):
        _, truth, readings = e.step(ts)
        for k in sorted(readings):
            v = readings[k]
            h.update(f"{k}={'null' if v is None else format(v, '.17g')};".encode())
        ts += STEP
    return h.hexdigest()


def bounds_ok(engine, truth: dict, readings: dict) -> list[str]:
    bad = []
    for s in engine.g.topology.sensors:
        t, r = truth[s.id], readings[s.id]
        for name, v in (("truth", t), ("reading", r)):
            if v is None:
                continue
            if not math.isfinite(v):
                bad.append(f"{s.id} {name} not finite")
            elif s.kind == "pressure" and not -0.5 <= v <= 20:
                bad.append(f"{s.id} pressure {v:.2f}")
            elif s.kind == "flow" and v < -2:
                bad.append(f"{s.id} flow {v:.2f}")
            elif s.kind == "load" and v < -0.5:
                bad.append(f"{s.id} load {v:.2f}")
            elif s.kind == "temperature" and not -5 <= v <= 150:
                bad.append(f"{s.id} temperature {v:.2f}")
            elif s.kind == "voltage" and not -0.5 <= v <= 40:
                bad.append(f"{s.id} voltage {v:.2f}")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--hash", nargs=3, metavar=("SEED", "TICKS", "SCENARIO"), help="print the hash of a run, used by the determinism check")
    args = ap.parse_args()
    if args.hash:
        seed, ticks, sc = args.hash
        print(run_hash(int(seed), int(ticks), None if sc == "-" else sc))
        return

    from app.scenarios import CATALOG, events_for
    from app.sim.engine import Engine, Event

    failures = 0

    def report(item: str, ok: bool, text: str) -> None:
        nonlocal failures
        failures += not ok
        print(f"{item:<5} {'ok  ' if ok else 'FAIL'} {text}", flush=True)

    # S-1 determinism: the same seed in two separate processes gives the same bits
    ticks = 600 if args.quick else 2000
    for sc in (None, "scn-compound"):
        outs = [subprocess.run([sys.executable, __file__, "--hash", "42", str(ticks), sc or "-"], capture_output=True, text=True,
                               env={**__import__("os").environ, "PYTHONPATH": ".", "LLM_OFFLINE": "1"}).stdout.strip() for _ in range(2)]
        report("S-1", outs[0] == outs[1] and len(outs[0]) == 64, f"{ticks} ticks, seed 42, scenario {sc or 'none'}, two processes: {outs[0][:12]} / {outs[1][:12]}")
    a, b = run_hash(1, 300), run_hash(2, 300)
    report("S-1", a != b, "different seeds give different readings")

    # S-2 / S-3 / S-6: ten seeds, 24 simulated hours each, no scenario
    seeds = range(1, 4 if args.quick else 11)
    hours = 6 if args.quick else 24
    means, issues, rss = [], [], []
    for sd in seeds:
        e = Engine(sd)
        ts = START
        series = []
        for k in range(hours * 120):
            _, truth, readings = e.step(ts)
            issues += [f"seed {sd} tick {k}: {m}" for m in bounds_ok(e, truth, readings)][:3]
            if k % 20 == 0:
                series.append(sum(w for w, _, _ in e.zone_demand.values()))
            ts += STEP
        means.append(float(np.mean(series)))
        rss.append(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576)
    spread = (max(means) - min(means)) / np.mean(means)
    report("S-3", not issues, f"{len(list(seeds))} seeds x {hours} simulated hours x {len(Engine(1).g.topology.sensors)} sensors: {len(issues)} values out of range or not finite" + (f" e.g. {issues[:2]}" if issues else ""))
    report("S-2", spread < 0.12, f"mean total water demand over the day across seeds varies by {spread:.1%} (max - min over the mean; each date has its own random 3.5% demand and 2.2 C temperature draw)")
    report("S-6", rss[-1] < rss[0] * 1.10 + 20, f"peak memory after seed 1: {rss[0]:.0f} MB, after seed {len(rss)}: {rss[-1]:.0f} MB")

    # S-13 tick time
    e = Engine(42)
    ts = START
    times = []
    for _ in range(1000):
        t0 = time.perf_counter(); e.step(ts); times.append((time.perf_counter() - t0) * 1000); ts += STEP
    p95 = float(np.percentile(times, 95))
    report("S-13", p95 < 100, f"engine.step over 1000 ticks: mean {np.mean(times):.1f} ms, p95 {p95:.1f} ms, max {max(times):.1f} ms (the tick budget is 2000 ms)")

    # S-9 / S-3 under every scenario: finite and in range for an hour of simulated time
    broken = []
    for sc in CATALOG:
        e = Engine(42)
        for ev in events_for(sc.id, {}, "stress"):
            e.add_event(ev)
        ts = START
        for k in range(120):
            _, truth, readings = e.step(ts)
            broken += [f"{sc.id}: {m}" for m in bounds_ok(e, truth, readings)][:2]
            ts += STEP
    report("S-3", not broken, f"all {len(CATALOG)} scenarios, one simulated hour each: {len(broken)} values out of range" + (f" e.g. {broken[:3]}" if broken else ""))

    # S-9: events stacked on each other, the same fault twice, repair of something that is not broken
    e = Engine(42)
    ts = START
    pipe = Engine(42).g.zone_pipe["zone-gov"]
    for ev in (Event("pipe_burst", pipe, 0.5), Event("pipe_burst", pipe, 0.5), Event("heat_wave", None, 46.0), Event("holiday", None, 1.5), Event("iftar_surge", None, 0.4)):
        e.add_event(ev)
    bad = []
    for _ in range(300):
        _, truth, readings = e.step(ts); bad += bounds_ok(e, truth, readings)[:1]; ts += STEP
    removed = e.repair("leak", pipe)
    report("S-9", not bad and removed == 2 and e.repair("leak", pipe) == 0, f"two identical bursts + heat + holiday + surge: {len(bad)} bad values; one repair removed {removed} bursts, the second removed 0")

    # S-8: after a scenario and a reset the engine behaves like a fresh one
    fresh = run_hash(42, 200)
    e = Engine(42)
    for ev in events_for("scn-compound", {}, "x"):
        e.add_event(ev)
    ts = START
    for _ in range(400):
        e.step(ts); ts += STEP
    e.reset()
    h = hashlib.sha256()
    ts = START
    for _ in range(200):
        _, _, readings = e.step(ts)
        for k in sorted(readings):
            v = readings[k]
            h.update(f"{k}={'null' if v is None else format(v, '.17g')};".encode())
        ts += STEP
    report("S-8", h.hexdigest() == fresh, "run a scenario for 400 ticks, reset, then 200 ticks equals a never-used engine (bit for bit)")

    # S-10 / S-11: repair ends the fault and leaves the others
    e = Engine(42)
    ts = START
    pipes = list(dict.fromkeys(e.g.zone_pipe.values()))
    e.add_event(Event("pipe_burst", pipes[0], 0.7)); e.add_event(Event("pipe_burst", pipes[1], 0.7)); e.add_event(Event("heat_wave", None, 44.0))
    for _ in range(80):
        e.step(ts); ts += STEP
    leak_before = e.leak_m3h.get(pipes[0], 0.0)
    e.repair("leak", pipes[0])
    for _ in range(80):
        e.step(ts); ts += STEP
    leak_after, other = e.leak_m3h.get(pipes[0], 0.0), e.leak_m3h.get(pipes[1], 0.0)
    report("S-10/11", leak_before > 1 and leak_after < 0.05 * leak_before and other > 1 and any(x.kind == "heat_wave" for x in e.events),
           f"repair of one of two bursts: its leak {leak_before:.1f} -> {leak_after:.2f} m3/h, the other still {other:.1f} m3/h, heat wave still on")

    # S-12 the what-if copy leaves the live state alone
    from app.runtime import Runtime
    rt = Runtime(42)
    e = rt.engine
    ts = START
    for _ in range(50):
        e.step(ts); ts += STEP
    def digest(en):
        return hashlib.sha256(repr((sorted(en.state.items()), sorted(en.truth.items()), en.tick, [(x.kind, x.target) for x in en.events], sorted(en.leak_m3h.items()),
                                    en.rng.bit_generator.state["state"]["state"])).encode()).hexdigest()
    before = digest(e)
    sb = rt.clone()
    sb.engine.add_event(Event("pipe_burst", pipes[0], 1.0))
    t2 = ts
    for _ in range(100):
        sb.engine.step(t2); t2 += STEP
    report("S-12", digest(e) == before and not any(x.kind == "pipe_burst" for x in e.events), "a sandbox with a burst, run 100 ticks: the live engine's state hash is unchanged")

    print("failures:", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
