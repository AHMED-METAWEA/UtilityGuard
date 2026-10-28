"""Measure detection precision and recall by replaying labelled scenario runs (spec 7)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from app.ml import detection_metrics
from app.scenarios import events_for

log = logging.getLogger("utilityguard.ml.eval")

CASES = {
    "scn-pipe-burst": ("leak", "targetAsset", ["water-pipe-04", "water-pipe-05", "water-pipe-08", "water-pipe-02"]),
    "scn-substation-trip": ("substation_trip", "targetAsset", ["elec-sub-01", "elec-sub-02", "elec-sub-03", "elec-sub-04"]),
    "scn-pump-failure": ("pump_failure", "targetAsset", ["water-pump-05", "water-pump-04", "water-pump-08"]),
    "scn-cooling-fault": ("cooling_fault", "targetAsset", ["cool-plant-01", "cool-plant-02"]),
    "scn-stuck-sensor": ("sensor_fault", "targetSensor", ["water-pipe-05-pressure", "water-pipe-04-pressure"]),
}
SEVERITIES = [0.15, 0.35, 0.7, 1.0]


def evaluate(rt_factory, detector, normal_runs: int = 8, ticks: int = 45) -> dict[str, float]:
    """Each run is one labelled example: positive if a fault was injected, predicted positive if
    the detector opened an incident of the right kind (wrong kind counts as a false positive)."""
    labels, preds = [], []
    rng = np.random.default_rng(5)
    t0 = datetime(2026, 10, 27, 6, tzinfo=timezone.utc)

    def one(scenario: str | None, params: dict, start: datetime) -> list[str]:
        rt = rt_factory()
        det = detector.clone(rt)
        found: list[str] = []
        det.on_incident.append(lambda inc, f, root: found.append(f.kind))
        rt.tick_hooks.append(det.tick)

        async def go():
            for i in range(20):
                await rt.tick_once(start + timedelta(seconds=2 * i))
            if scenario:
                for ev in events_for(scenario, params, "eval"):
                    rt.add_event(ev)
            for i in range(20, 20 + ticks):
                await rt.tick_once(start + timedelta(seconds=2 * i))
        asyncio.run(go())
        return found

    k = 0
    for scenario, (kind, pname, targets) in CASES.items():
        for target in targets:
            for sev in SEVERITIES if scenario != "scn-stuck-sensor" else [1.0]:
                start = t0 + timedelta(hours=int(rng.integers(0, 48)))
                found = one(scenario, {pname: target, "severity": sev}, start)
                labels.append(True)
                preds.append(bool(found) and found[0] == kind)
                if found and found[0] != kind:  # a wrong root cause is also a false alarm
                    labels.append(False)
                    preds.append(True)
                k += 1
    for i in range(normal_runs):
        found = one(None, {}, t0 + timedelta(hours=int(rng.integers(0, 72))))
        labels.append(False)
        preds.append(bool(found))
    m = detection_metrics(labels, preds)
    m["runs"] = float(len(labels))
    log.info("detection eval: %s", m)
    return m
