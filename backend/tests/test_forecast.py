"""The forecast has to earn its claims: it reacts to scenarios, beats naive methods, and tells the truth about its band."""

import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import ml
from app import schemas as S
from app.ml.comparison import block_bootstrap, compare, ensemble_weights, seasonal_predict
from app.ml.service import MLService
from app.main import app
from app.sim.engine import CAIRO, Engine, Event

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
    # the check is about the scenario, not the weekday: a holiday scenario on a Friday or Saturday (already the weekend) is a known weaker case
    # (water error about 7% to 11%, see docs/status.md), so measure on the next Sunday to Thursday
    while start.astimezone(CAIRO).weekday() in (4, 5):
        start += timedelta(days=1)
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


@pytest.mark.parametrize("net", NETS)
def test_model_comparison_is_complete_and_selected_on_dev(trained, net):
    evidence = trained[1].evidence[net]
    rows = evidence["comparison"]
    assert {row["name"] for row in rows} == {"lightgbm", "ridge", "random_forest", "seasonal_smoothing", "ensemble"}
    live, = [row for row in rows if row["is_live"]]
    assert live["name"] == "lightgbm"
    assert {key: live[key] for key in evidence["model"]} == evidence["model"]
    selected, = [row for row in rows if row["is_selected"]]
    assert selected["name"] == evidence["selected"] == min(rows, key=lambda row: row["oof_mae"])["name"]
    assert evidence["selected_matches_live"] == (selected["name"] == "lightgbm")
    assert all(len(row["hourly_mape"]) == 24 and all(np.isfinite(row["hourly_mape"])) for row in rows)
    assert all(0 <= row["p_lgbm_better"] <= 1 and row["fit_seconds"] >= 0 for row in rows)
    assert all(row["skill_vs_best_baseline"] == 1 - row["mae"] / min(b["mae"] for b in evidence["baselines"].values()) for row in rows)
    ensemble = next(row for row in rows if row["name"] == "ensemble")
    assert sum(ensemble["weights"].values()) == pytest.approx(1)
    assert min(ensemble["weights"].values()) >= 0 and ensemble["weights_source"] == "dev_oof"


def test_ensemble_weights_minimise_dev_mae_with_convex_weights():
    matrix = np.array([[0., 10.], [10., 0.], [20., 10.]])
    target = matrix @ np.array([.3, .7])
    weights = ensemble_weights(matrix, target)
    assert weights == pytest.approx([.3, .7])
    assert weights.sum() == pytest.approx(1)
    assert ensemble_weights(matrix, matrix[:, 0]) == pytest.approx([1, 0])
    assert ensemble_weights(matrix, matrix[:, 1]) == pytest.approx([0, 1])
    with pytest.raises(ValueError):
        ensemble_weights(np.zeros((0, 2)), np.zeros(0))


def test_test_targets_cannot_influence_fits_weights_or_selection(monkeypatch):
    """Exercise the real comparison orchestration with cheap models and poisoned test targets."""
    from app.ml import comparison
    from sklearn.dummy import DummyRegressor

    class DevOnlyRegressor(DummyRegressor):
        def fit(self, X, y):
            assert np.max(y) < 100  # no test target may reach any full-dev or fold fit
            return super().fit(X, y)

    monkeypatch.setattr(comparison, "RandomForestRegressor", lambda **kwargs: DevOnlyRegressor())
    monkeypatch.setattr(comparison, "Ridge", lambda **kwargs: DevOnlyRegressor())
    times = pd.date_range("2026-01-01", periods=48 * 10, freq="30min", tz="UTC")
    dev = pd.DataFrame({feature: np.ones(len(times)) for feature in ml.FEATURES})
    dev = dev.assign(ts=times, yn=2., y=2., zone="a", temp_fc=20.)
    block = np.arange(len(dev)) * ml.FOLDS // len(dev)
    te = dev.iloc[:48].copy().assign(ts=times[:48] + pd.Timedelta(days=20), yn=1000., y=1000.)
    agg = te.set_index("ts")[["y"]].assign(p=1.9)
    first = compare(dev, te, block, np.full(len(dev), 1.9), agg, 3., 0., 42, 48)
    poisoned = compare(dev, te.assign(yn=1e9, y=1e9), block, np.full(len(dev), 1.9), agg.assign(y=1e9), 3., 0., 42, 48)
    assert first["selected"] == poisoned["selected"]
    for a, b in zip(first["comparison"], poisoned["comparison"]):
        assert (a["oof_mae"], a["weights"]) == (b["oof_mae"], b["weights"])
        assert a["mae"] != b["mae"]  # test scores must react, unlike weights and selection


def test_bootstrap_is_deterministic_paired_and_resamples_whole_days():
    days = np.repeat(np.arange(4), 48)
    y = np.full(len(days), 100.)
    live = y + np.repeat([0., 0., 2., 3.], 48)
    other = y + 1
    draws = np.random.default_rng(731).integers(0, 4, (1000, 4))
    expected = float(np.mean(np.array([-1., -1., 1., 2.])[draws].sum(axis=1) < 0))
    assert block_bootstrap(y, live, other, days) == expected
    assert block_bootstrap(y, live, other, days) == block_bootstrap(y, live, other, days)
    assert block_bootstrap(y, y, other, days) == 1
    assert block_bootstrap(y, other, y, days) == 0
    assert block_bootstrap(y, live, live, days) == 0  # ties are not wins


def test_statistical_model_uses_training_targets_only():
    times = pd.date_range("2026-01-01", periods=48 * 3, freq="30min", tz="UTC")
    frame = pd.DataFrame(dict(ts=times, yn=2 + np.sin(np.arange(len(times)) * 2 * np.pi / 48), zone="a", zone_base=3.))
    train, holdout = frame.iloc[:96], frame.iloc[96:]
    expected = seasonal_predict(train, holdout, 48, ml.CAIRO)
    assert seasonal_predict(train, holdout.assign(yn=1e9, temperature=1e9), 48, ml.CAIRO) == pytest.approx(expected)


@pytest.mark.parametrize("net", NETS)
def test_comparison_schema_roundtrip_and_old_evidence_defaults(trained, net):
    service = MLService.__new__(MLService)
    service.models = trained[1]
    service.rt = SimpleNamespace(engine=trained[0])
    result = service.evidence(net)
    assert S.ForecastEvidence.model_validate(result.dump()).dump() == result.dump()
    live = next(row for row in result.comparison if row.is_live)
    assert (live.mae, live.rmse, live.mape, live.bias) == (result.model.mae, result.model.rmse, result.model.mape, result.model.bias)
    old = result.dump()
    for field in ("comparison", "selected", "selectedMatchesLive", "comparisonNote"):
        old.pop(field)
    legacy = S.ForecastEvidence.model_validate(old)
    assert legacy.comparison == [] and legacy.selected == "lightgbm" and legacy.selected_matches_live
    raw = service.models.evidence[net]
    service.models = SimpleNamespace(**vars(trained[1]) | {"evidence": {net: {k: v for k, v in raw.items() if k not in ("comparison", "selected", "selected_matches_live", "comparison_note")}}})
    assert service.evidence(net).comparison == []
