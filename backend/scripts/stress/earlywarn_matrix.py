"""The early warning on a matrix of wear times, pumps and seeds, plus the cases that must NOT warn (stress-test items R-5, R-6, R-7, R-8, R-9).

    PYTHONPATH=. .venv/bin/python scripts/stress/earlywarn_matrix.py [--quick] [--clean-hours 24]

It runs the twin in process (no server, no language model), fast-forwarded: a `Runtime`, the detector and the ML service with its early warning, then a pump that wears for
`minutesToFailure` minutes before it stops, exactly the `scn-pump-wear` event. Needs the cached models (`backend/data/models.pkl`) from a server start. Criteria, written before the run:

  EW-1  Every wear of 5 minutes or more is warned about before the pump stops, on every pump and seed. The shortest wear that is still warned about is reported; a wear of 3
        minutes may be too fast for a warning that needs three samples in a row, and if so that limit is stated, not hidden.
  EW-2  The warning comes before the failure incident (the detector's alarm), never after.
  EW-3  No warning in a clean run of the stated length (24 hours by default) and none in a run with a power cut that stops and restarts the pumps.
  EW-4  Two pumps wearing at once are both warned about.
  EW-5  A warning is raised only for the pumps that wear: the other pumps and the transformers stay silent in every run above.
Exits 1 when a criterion fails (EW-1 for 3 minutes is reported separately and does not fail the run on its own).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from pathlib import Path

from app.cost import Assumptions
from app.detection import Detector
from app.ml.service import MLService
from app.runtime import Runtime
from app.sim.engine import Event

START = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)  # a Monday, 10:00 in Cairo
DT = timedelta(seconds=2)
bad = 0


def verdict(name: str, ok: bool, detail: str) -> None:
    global bad
    bad += 0 if ok else 1
    print(f"{name}  {'PASS' if ok else 'FAIL'}  {detail}", flush=True)


async def build(seed: int):
    rt = Runtime(seed)
    det = Detector(rt)
    rt.detector = det
    rt.tick_hooks.append(det.tick)
    mls = MLService(rt, det)
    mls.warnings.A = Assumptions(path=Path("/nonexistent/assumptions.json"))  # the app sets this; without it a warning cannot be priced and is never opened
    return rt, mls


async def run_case(seed: int, pumps: list[str], minutes: float, tail_ticks: int = 150, extra: list[Event] | None = None):
    rt, mls = await build(seed)
    ts = START
    for _ in range(90):
        await rt.tick_once(ts)
        ts += DT
    begin = rt.engine.tick
    for p in pumps:
        rt.add_event(Event(kind="pump_failure", target=p, severity=1.0, run_id="matrix", params={"precursor": True, "ramp_ticks": int(minutes * 30)}))
    for ev in extra or []:
        rt.add_event(ev)
    first_warning: dict[str, int] = {}
    stopped: dict[str, int] = {}
    incident_at: int | None = None
    all_warned: set[str] = set()
    for i in range(int(minutes * 30) + tail_ticks):
        await rt.tick_once(ts)
        ts += DT
        for w in mls.warnings.warnings.values():
            all_warned.add(w.asset_id)
            first_warning.setdefault(w.asset_id, rt.engine.tick - begin)
        for p in pumps:
            st = rt.asset_status.get(p)
            if st and st.status in ("offline", "fault") and p not in stopped:
                stopped[p] = rt.engine.tick - begin
        if incident_at is None and any(a.title.lower().startswith("pump") for a in rt.alerts.values()):
            incident_at = rt.engine.tick - begin
    return {"first_warning": first_warning, "stopped": stopped, "incident_at": incident_at, "warned": all_warned}


async def main_async(args) -> None:
    rt, _ = await build(42)
    pumps_all = [a.id for a in rt.engine.g.topology.assets if a.kind == "pump"]
    zone_pumps = [rt.engine.g.zone_pump[z] for z in ("zone-cbd", "zone-gov", "zone-res-n")]
    minutes_list = [3, 5, 8] if args.quick else [3, 5, 8, 12, 20]
    seeds = [42] if args.quick else [42, 7]
    rows = []
    stray = set()
    for seed in seeds:
        for pump in zone_pumps:
            for minutes in minutes_list:
                r = await run_case(seed, [pump], minutes)
                w, s = r["first_warning"].get(pump), r["stopped"].get(pump)
                lead = None if w is None or s is None else (s - w) * 2
                rows.append((seed, pump, minutes, w, s, lead, r["incident_at"]))
                stray |= {a for a in r["warned"] if a != pump}
                print(f"  seed {seed} {pump} wear {minutes:>2} min: warning at tick {w}, pump stopped at tick {s}, lead {lead} s, failure alarm at tick {r['incident_at']}", flush=True)
    long_wear = [r for r in rows if r[2] >= 5]
    ok = all(r[3] is not None and r[4] is not None and r[3] < r[4] for r in long_wear)
    verdict("EW-1", ok, f"{sum(1 for r in long_wear if r[3] is not None and r[4] is not None and r[3] < r[4])}/{len(long_wear)} wears of 5 minutes or more warned before the pump stopped")
    short = [r for r in rows if r[2] == 3]
    print(f"      wear of 3 minutes: warned in {sum(1 for r in short if r[3] is not None and r[4] is not None and r[3] < r[4])}/{len(short)} cases (reported, not a failure)")
    leads = sorted(r[5] for r in long_wear if r[5] is not None)
    if leads:
        print(f"      lead time over wears of 5 minutes or more: min {leads[0]} s, median {leads[len(leads) // 2]} s, max {leads[-1]} s")
    verdict("EW-2", all(r[6] is None or r[3] is None or r[3] <= r[6] for r in rows), "the warning is never later than the failure alarm")
    verdict("EW-5", not stray, f"assets warned that were not wearing: {sorted(stray) or 'none'}")

    if not args.quick:
        r = await run_case(42, zone_pumps[:2], 8)
        verdict("EW-4", all(p in r["first_warning"] and r["first_warning"][p] < r["stopped"].get(p, 10 ** 9) for p in zone_pumps[:2]), f"two pumps wearing at once: warned at ticks {r['first_warning']}, stopped at {r['stopped']}")

    cut = Event(kind="substation_trip", target="elec-sub-02", severity=1.0, run_id="matrix", params={"cause": "load_shedding"}, duration=90)
    r = await run_case(42, [], 1, tail_ticks=400, extra=[cut])
    verdict("EW-3a", not r["warned"], f"power cut that stops and restarts the pumps: warnings {sorted(r['warned']) or 'none'}")

    if args.clean_hours:
        rt, mls = await build(42)
        ts = START
        warned: set[str] = set()
        ticks = int(args.clean_hours * 1800)
        for i in range(ticks):
            await rt.tick_once(ts)
            ts += DT
            if i % 50 == 0:
                warned |= {w.asset_id for w in mls.warnings.warnings.values()}
        warned |= {w.asset_id for w in mls.warnings.warnings.values()}
        verdict("EW-3b", not warned, f"{args.clean_hours:g} simulated hours without any scenario: warnings {sorted(warned) or 'none'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--clean-hours", type=float, default=24.0)
    args = ap.parse_args()
    asyncio.run(main_async(args))
    print(f"\n{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
