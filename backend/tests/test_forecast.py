"""The forecast has to earn its claims: it reacts to scenarios, beats naive methods, and tells the truth about its band."""

import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import ml
from app import schemas as S
from app.main import app
from app.sim.engine import Engine, Event

NETS = ("water", "electricity", "cooling")


@pytest.fixture(scope="module")
def trained():
    engine = Engine(42)
    return engine, ml.load_or_train(engine)


@pytest.mark.parametrize("net", NETS)
def test_model_beats_every_naive_method(trained, net):
    ev = trained[1].evidence[net]
    assert ev["skill"] > 0.2, f"{net} only {ev['skill']:.0%} better than the best naive method"
    assert all(ev["model"]["mae"] < b["mae"] for b in ev["baselines"].values())


@pytest.mark.parametrize("net", NETS)
def test_band_is_honest(trained, net):
    """Coverage on days the band never saw stays near the 80% it was built for (a ten-day test is noisy, so allow +-20 points)."""
    ev = trained[1].evidence[net]
    assert ev["nominal"] - 0.2 <= ev["coverage"] <= 1.0


@pytest.mark.parametrize("net", NETS)
def test_telling_the_model_about_the_scenario_helps(trained, net):
    sc = trained[1].evidence[net]["scenario"]
    assert sc["days"] >= 2 and sc["mape"] < sc["mape_blind"]


def test_training_window_ends_today_so_models_are_fresh(trained):
    m = trained[1]
    assert not ml.is_stale(m, datetime.now(timezone.utc))
    assert ml.is_stale(m, datetime.now(timezone.utc) + timedelta(days=2))


@pytest.mark.parametrize("name,event,holiday,heat", [("holiday", Event("holiday", None, 1.5), 1.5, 0.0), ("heat wave", Event("heat_wave", None, 46), 0.0, 46.0)])
def test_forecast_matches_the_twin_under_a_scenario(trained, name, event, holiday, heat):
    """Ground truth: run the twin itself through tomorrow with the scenario on, and compare network totals with the forecast."""
    engine, m = trained
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    rows = engine.history(start, 24, 30, schedule=lambda ts: [event])
    idx = pd.DatetimeIndex([t for t, _ in rows])
    temps = np.array([engine.ambient_at(t.to_pydatetime(), heat) for t in idx])
    for net in NETS:
        true = np.array([sum(r[f"demand:{net}:{z.id}"] for z in engine.g.topology.zones) for _, r in rows])
        pred = sum(ml.predict(m, net, z.id, idx, temps, holiday) for z in engine.g.topology.zones if (net, z.id) in m.series)
        assert np.mean(np.abs(pred - true) / true) < 0.09, f"{net} under {name}"


def test_running_a_scenario_changes_the_forecast_and_keeps_the_normal_day_line():
    with TestClient(app) as c:
        time.sleep(2.5)
        calm = S.DemandSeries.model_validate(c.get("/api/v1/series/demand?network=cooling&zoneId=all").json())
        assert calm.baseline is None and calm.drivers and calm.drivers.heat_wave is None and calm.hindcast
        c.post("/api/v1/scenarios/run", json={"scenarioId": "scn-heat-wave", "parameters": {"temperature": 46}})
        time.sleep(3)
        hot = S.DemandSeries.model_validate(c.get("/api/v1/series/demand?network=cooling&zoneId=all").json())
        assert hot.drivers.heat_wave == 46 and hot.baseline
        peak = lambda pts: max(p.predicted for p in pts)  # noqa: E731
        assert peak(hot.forecast) > 1.3 * peak(calm.forecast)
        assert abs(peak(hot.baseline) - peak(calm.forecast)) / peak(calm.forecast) < 0.1
        assert all(p.lower <= p.predicted <= p.upper for p in hot.forecast)
        c.post("/api/v1/scenarios/reset")


def test_evidence_endpoint_validates():
    with TestClient(app) as c:
        for net in NETS:
            ev = S.ForecastEvidence.model_validate(c.get(f"/api/v1/forecast/evidence?network={net}").json())
            assert len(ev.by_hour) == 24 and ev.baselines and ev.zones and 0 < ev.skill < 1
        assert c.get("/api/v1/forecast/evidence?network=gas").status_code == 404
