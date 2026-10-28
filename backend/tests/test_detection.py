import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.detection import Detector
from app.runtime import Runtime
from app.scenarios import events_for

T0 = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)


def run(scenario: str | None, params=None, warm=30, ticks=60):
    rt = Runtime(42)
    det = Detector(rt)
    rt.tick_hooks.append(det.tick)
    found = []
    det.on_incident.append(lambda inc, facts, root: found.append((inc, facts, root)))

    async def go():
        for i in range(warm):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        if scenario:
            for ev in events_for(scenario, params or {}, "run-t"):
                rt.add_event(ev)
        for i in range(warm, warm + ticks):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))

    asyncio.run(go())
    return rt, det, found


def test_no_false_alarms_in_normal_operation():
    rt, _, found = run(None, ticks=200)
    assert found == [], [i.title for i, _, _ in found]


@pytest.mark.parametrize("scenario,kind", [
    ("scn-pipe-burst", "leak"),
    ("scn-substation-trip", "substation_trip"),
    ("scn-pump-failure", "pump_failure"),
    ("scn-cooling-fault", "cooling_fault"),
    ("scn-stuck-sensor", "sensor_fault"),
])
def test_scenario_detected_as_one_incident(scenario, kind):
    rt, det, found = run(scenario)
    kinds = [f.kind for _, f, _ in found]
    assert kinds and kinds[0] == kind, kinds
    assert len(found) == 1, [i.title for i, _, _ in found]


def test_leak_estimate_close_to_truth():
    rt, det, found = run("scn-pipe-burst", {"severity": 0.6})
    leak = det.facts_for(found[0][0].id)[0].leak_m3h
    assert 140 < leak < 220  # truth is 300 × 0.6 = 180 m3/h
    assert len(found[0][1].reasons) >= 3


def test_trip_groups_cross_network_alerts():
    rt, det, found = run("scn-substation-trip", {"targetAsset": "elec-sub-02"})
    inc = rt.incidents[found[0][0].id]
    assert "electricity" in inc.networks
    assets = {a for aid in inc.alert_ids for a in rt.alerts[aid].asset_ids}
    assert rt.engine.g.zone_pump["zone-gov"] in assets and "cool-plant-01" in assets


def test_alert_resolves_after_stop():
    rt = Runtime(42)
    det = Detector(rt)
    rt.tick_hooks.append(det.tick)

    async def go():
        for i in range(20):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        for ev in events_for("scn-pipe-burst", {}, "run-r"):
            rt.add_event(ev)
        for i in range(20, 50):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        rt.engine.clear_events("run-r")
        for i in range(50, 120):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))

    asyncio.run(go())
    assert rt.alerts and all(a.state == "resolved" for a in rt.alerts.values())
