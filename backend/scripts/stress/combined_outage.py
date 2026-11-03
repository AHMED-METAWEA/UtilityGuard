"""What happens when every failure that reaches a zone happens at once: does the twin price a whole-district outage as something other than its worst single failure?

    PYTHONPATH=. .venv/bin/python scripts/stress/combined_outage.py [--zone zone-cbd]

For each zone, a sandbox copy of the twin gets the substation trip, the cooling-plant fault, the pump failure and the main burst that reach it, all at once, and the real
detector and cost engine price what they find (the same way `what_if` does one asset). Criteria, written before the run:

  CO-1  With the substation among them, the substation's incident costs what it costs alone (within 1 EGP), and the pump and the main never come up as incidents of their
        own: the loss of power already stops the pumps, so the water effect is explained by it. A cooling plant that another substation feeds is a separate incident
        (priced as alone), because the power cut does not reach it. (First version of this criterion expected one incident in every zone; zone-cult and zone-pres showed
        that the plant serving them is fed from the other substation, so the criterion was wrong, not the detector.)
  CO-2  Without the substation, every incident the detector raises costs what the same failure costs alone (within 1 EGP), and the cost lines that appear in more than
        one incident are listed, because adding the incidents counts those lines twice.
Exits 1 when CO-1 fails. CO-2 is a measurement and reports, it does not fail the run.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import timedelta
from pathlib import Path

from app.cost import Assumptions, compute
from app.detection import Detector
from app.runtime import Runtime
from app.sim.engine import Event
from scripts.export_for_health import AT, DEFAULT_OUT, KINDS

TICKS = 60


async def run(rt, ids: list[str], A) -> list:
    g = rt.engine.g
    sb = rt.clone()
    det = rt.detector.clone(sb)
    found: list = []
    det.on_incident.append(lambda inc, f, root: found.append((f, root)))
    sb.tick_hooks.append(det.tick)
    for a in ids:
        sb.engine.add_event(Event(kind=KINDS[g.asset_index[a].kind], target=a, severity=0.7, run_id="what-if"))
    for i in range(TICKS):
        await sb.tick_once(rt.now + timedelta(seconds=2 * (i + 1)))
    return [(root, compute(f, A, "what-if").cost) for f, root in found]


async def main_async(zones: list[str]) -> int:
    rt = Runtime(42)
    rt.detector = Detector(rt)
    rt.tick_hooks.append(rt.detector.tick)
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    for i in range(60):
        await rt.tick_once(AT + timedelta(seconds=2 * i))
    with (DEFAULT_OUT / "scenarios.csv").open(encoding="utf-8") as f:
        single = {r["asset_id"]: float(r["cost_of_delay_egp"]) for r in csv.DictReader(f) if r["cost_of_delay_egp"]}
        f.seek(0)
        reach = list(csv.DictReader(f))
    bad = 0
    for zone in zones:
        ids = [r["asset_id"] for r in reach if zone in r["affected_zones"].split(";")]
        subs = [a for a in ids if a.startswith("elec-")]
        got = await run(rt, ids, A)
        if subs:
            ok = any(root in subs and abs(c.cost_of_delay - single[root]) <= 1 for root, c in got) and all(
                root in subs or (root.startswith("cool-plant") and abs(c.cost_of_delay - single[root]) <= 1) for root, c in got)
            bad += not ok
            print(f"CO-1 {zone:13} {'PASS' if ok else 'FAIL'}  all of {ids} at once: {len(got)} incident(s), {[(root, round(c.cost_of_delay)) for root, c in got]}; the substation alone costs {[round(single[a]) for a in subs]}", flush=True)
        rest = [a for a in ids if a not in subs]
        got = await run(rt, rest, A)
        lines: dict = {}
        for root, c in got:
            for it in c.breakdown:
                lines.setdefault(it.label, []).append(root)
        same = all(abs(c.cost_of_delay - single[root]) <= 1 for root, c in got)
        dup = {k: v for k, v in lines.items() if len(v) > 1}
        print(f"CO-2 {zone:13} {'same as alone' if same else 'DIFFERENT from alone'}: {rest} -> {len(got)} incident(s) {[(root, round(c.cost_of_delay)) for root, c in got]}; lines counted in more than one incident: {sorted(dup) or 'none'}", flush=True)
    print(f"\n{bad} failed")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", action="append")
    args = ap.parse_args()
    zones = args.zone or sorted({z for r in csv.DictReader((DEFAULT_OUT / "scenarios.csv").open(encoding="utf-8")) for z in r["affected_zones"].split(";") if z})
    return asyncio.run(main_async(zones))


if __name__ == "__main__":
    sys.exit(main())
