"""The AI lab's claims are measured on the twin engine; these tests hold the numbers it shows to account."""

import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.lab.detectors import scan
from app.lab.harness import ONSET, Bench, Fault, random_start
from app.main import app, lab_cache
from app.schemas.lab import (
    LabBenchmark, LabLocalization, LabQuality, QualityNow, SensorValue, TrialResult,
)

API = "/api/v1"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.get(f"{API}/lab/benchmark")  # builds once if the cache is cold
        yield c


def bench_scores(c):
    return {d.id: d for d in LabBenchmark.model_validate(c.get(f"{API}/lab/benchmark").json()).detectors}


def test_detector_benchmark_claims(client):
    b = LabBenchmark.model_validate(client.get(f"{API}/lab/benchmark").json())
    d = {x.id: x for x in b.detectors}
    assert set(d) == {"d1", "d2", "d3", "d4"} and b.config.test_runs >= 100 and b.config.clean_hours > 100
    assert d["d4"].detection_rate >= 0.95 and d["d4"].false_alarms_per_100h <= 2.0
    assert d["d4"].mean_delay_cost < d["d1"].mean_delay_cost, "the supervised model must beat the plain threshold on cost of delay"
    assert d["d4"].unseen_detection_rate is not None and d["d4"].unseen_detection_rate >= 0.9, "locations never seen in training"
    assert d[b.best_detector_id].false_alarms_per_100h <= 5.0
    assert all(0 <= x.detection_rate <= 1 for x in b.detectors)


def test_leak_curve_gets_easier_with_size(client):
    b = LabBenchmark.model_validate(client.get(f"{API}/lab/benchmark").json())
    d4 = [next(r for r in p.results if r.detector_id == "d4") for p in b.leak_curve]
    assert [p.leak_m3h for p in b.leak_curve] == sorted(p.leak_m3h for p in b.leak_curve)
    assert d4[-1].median_delay_min <= d4[0].median_delay_min


def test_delay_cost_follows_the_prices(client):
    before = bench_scores(client)["d4"]
    client.put(f"{API}/assumptions", json={"water_price": 14})
    try:
        after = bench_scores(client)["d4"]
        assert after.median_delay_min == before.median_delay_min and after.mean_delay_cost > before.mean_delay_cost
    finally:
        client.put(f"{API}/assumptions", json={"water_price": 8})


def test_detectors_never_see_the_fault_tag():
    b = Bench(42)
    start = random_start(np.random.default_rng(1))
    run = b.run(Fault("pipe_burst", b.g.zone_pipe["zone-gov"], 0.5), 5, start)
    blind = replace(run, fault=None, facts={})
    m = lab_cache.models()
    for k, det in m.det.items():
        assert scan(det.flag(run), run.onset) == scan(det.flag(blind), run.onset), k


def test_localization_accuracy_and_live_incident(client):
    acc = LabLocalization.model_validate(client.get(f"{API}/lab/localization").json())
    assert acc.top1 >= 0.9 and acc.top3 >= acc.top1 and acc.runs >= 90
    client.post(f"{API}/scenarios/reset")
    client.post(f"{API}/scenarios/run", json={"scenarioId": "scn-pipe-burst", "parameters": {"targetAsset": "water-pipe-04", "severity": 0.6}})
    for _ in range(40):
        time.sleep(1)
        inc = [i for i in client.get(f"{API}/incidents").json() if "burst" in i["title"].lower()]
        if inc:
            break
    assert inc, "the burst was never detected"
    r = client.get(f"{API}/incidents/{inc[0]['id']}/localization")
    assert r.status_code == 200
    loc = r.json()
    assert loc["candidates"][0]["assetId"] == "water-pipe-04"
    assert 100 <= loc["candidates"][0]["leakM3H"] <= 220  # 300 m3/h x 0.6 once the ramp is done
    client.post(f"{API}/scenarios/reset")


def test_trial_and_sensor_what_if(client):
    pipe = "water-pipe-06"
    t = TrialResult.model_validate(client.post(f"{API}/lab/trial", json={"pipeId": pipe, "leakM3H": 90, "seed": 4}).json())
    assert t.truth_asset_id == pipe and t.hit_rank == 1 and len(t.detectors) == 4 and t.leak_m3h == 90, "the size sent must be the size injected"
    assert next(d for d in t.detectors if d.detector_id == "d4").detected
    own = [s for s in lab_cache.models().bench.sensors if s.startswith(pipe + "-")]
    off = TrialResult.model_validate(client.post(f"{API}/lab/trial", json={"pipeId": pipe, "leakM3H": 90, "seed": 4, "disabledSensors": own}).json())
    d4_on = next(d for d in t.detectors if d.detector_id == "d4").delay_min
    d4_off = next(d for d in off.detectors if d.detector_id == "d4")
    assert off.disabled_sensors == sorted(own) and off.localization.sensors_used == off.localization.sensors_total - len(own)
    assert (not d4_off.detected) or d4_off.delay_min >= d4_on


def test_sensor_value_ranks_pipe_sensors_above_pump_sensors(client):
    sv = SensorValue.model_validate(client.get(f"{API}/lab/sensors").json())
    cost = {r.asset_id: r.extra_cost for r in sv.rows}
    assert max(v for k, v in cost.items() if "pipe" in k) > 100 and max(v for k, v in cost.items() if "pump" in k) < 50
    assert [r.extra_cost for r in sv.rows] == sorted((r.extra_cost for r in sv.rows), reverse=True)


def test_quality_flags_are_measured_and_quiet(client):
    q = LabQuality.model_validate(client.get(f"{API}/lab/quality").json())
    by = {k.kind: k for k in q.kinds}
    assert q.false_flags_per_100_sensor_hours < 0.5 and q.process_false_flags <= q.process_runs * 2
    assert by["sensor_dropout:1"].detection_rate >= 0.9 and by["stuck_sensor:1"].detection_rate >= 0.9 and by["sensor_drift:1"].detection_rate >= 0.9


def test_live_quality_flags_a_dropped_sensor_and_truth_is_untouched(client):
    client.post(f"{API}/scenarios/reset")
    for _ in range(60):  # the flags need a full window of history after a reset
        time.sleep(1.5)
        if client.get(f"{API}/health").json()["tick"] >= 34:
            break
    assert QualityNow.model_validate(client.get(f"{API}/quality").json()).sensors_total == 89
    sid = "water-pipe-02-flow-in"
    client.post(f"{API}/scenarios/run", json={"scenarioId": "scn-sensor-dropout", "parameters": {"targetSensor": sid}})
    found = False
    for _ in range(30):
        time.sleep(1)
        found = any(f["sensorId"] == sid and f["status"] == "missing" for f in client.get(f"{API}/quality").json()["flagged"])
        if found:
            break
    client.post(f"{API}/scenarios/reset")
    assert found


def test_engine_sensor_faults_touch_telemetry_only():
    from datetime import datetime, timezone
    from app.sim.engine import Engine, Event
    e = Engine(42)
    ts = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    sid = "water-pipe-03-pressure"
    for k in range(5):
        e.step(ts)
    e.add_event(Event("sensor_drift", sid, 1.0))
    e.add_event(Event("sensor_dropout", "water-pipe-03-flow-in"))
    truth_before = None
    for k in range(10):
        _, truth, readings = e.step(ts)
    clean = Engine(42)
    for k in range(15):
        _, t2, r2 = clean.step(ts)
    assert abs(truth[sid] - t2[sid]) < 1e-9, "the truth must not move"
    span = e.g.sensor_index[sid].normal_range.max - e.g.sensor_index[sid].normal_range.min
    assert readings[sid] - r2[sid] == pytest.approx(0.01 * span * 10, abs=0.05 * span)  # 1% of range per tick for 10 ticks
    assert readings["water-pipe-03-flow-in"] is None
