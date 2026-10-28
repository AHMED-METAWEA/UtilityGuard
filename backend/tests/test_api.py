"""Real backend outputs must match the contract schemas (same models the fixtures use)."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import schemas as S
from app.main import app
from app.runtime import Runtime, runtime


def test_rest_endpoints_validate():
    with TestClient(app) as c:
        S.Health.model_validate(c.get("/api/v1/health").json())
        topo = S.Topology.model_validate(c.get("/api/v1/topology").json())
        assert len(topo.zones) == 9 and len(topo.sensors) > 80
        for sc in c.get("/api/v1/scenarios").json():
            S.Scenario.model_validate(sc)
        err = c.post("/api/v1/alerts/alert-9999/acknowledge")
        assert err.status_code == 404
        S.ErrorResponse.model_validate(err.json())


def test_ws_snapshot_then_readings():
    with TestClient(app) as c, c.websocket_connect("/ws/stream") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        S.Snapshot.model_validate(first["payload"])
        seen = set()
        for _ in range(40):
            msg = ws.receive_json()
            S.WS_PAYLOADS[msg["type"]].model_validate(msg["payload"])
            seen.add(msg["type"])
            if msg["type"] == "reading":
                break
        assert "reading" in seen


def test_scenario_run_stop_reset():
    with TestClient(app) as c:
        r = c.post("/api/v1/scenarios/run", json={"scenarioId": "scn-pipe-burst", "parameters": {"severity": 0.8}})
        run = S.ScenarioRunResponse.model_validate(r.json())
        assert run.run_id in c.get("/api/v1/health").json()["activeRuns"]
        assert c.post("/api/v1/scenarios/stop", json={"runId": run.run_id}).json()["state"] == "stopped"
        assert c.post("/api/v1/scenarios/reset").json()["status"] == "reset"
        assert c.post("/api/v1/scenarios/run", json={"scenarioId": "scn-nope"}).status_code == 404


def _trace(seed: int) -> list[float]:
    rt = Runtime(seed)
    t0 = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)

    async def go():
        for i in range(10):
            await rt.tick_once(t0 + timedelta(seconds=2 * i))

    asyncio.run(go())
    return [rt.latest[s].value for s in sorted(rt.latest)]


def test_simulation_is_deterministic():
    assert _trace(42) == _trace(42)
    assert _trace(42) != _trace(7)


def test_burst_propagates():
    rt = Runtime(42)
    pipe = rt.engine.g.zone_pipe["zone-gov"]
    t0 = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
    from app.scenarios import events_for

    async def go():
        for i in range(10):
            await rt.tick_once(t0 + timedelta(seconds=2 * i))
        before = rt.latest[f"{pipe}-pressure"].value
        for ev in events_for("scn-pipe-burst", {}, "run-x"):
            rt.add_event(ev)
        for i in range(10, 40):
            await rt.tick_once(t0 + timedelta(seconds=2 * i))
        return before, rt.latest[f"{pipe}-pressure"].value, rt.asset_status[pipe].status

    before, after, status = asyncio.run(go())
    assert after < before - 1.0
    assert status in ("warning", "fault")
    assert runtime is not rt


def test_forecast_and_metrics_validate():
    with TestClient(app) as c:
        f = S.Forecast.model_validate(c.get("/api/v1/forecast?network=water&zoneId=zone-gov&horizonHours=24").json())
        assert len(f.points) == 48 and all(p.lower <= p.predicted <= p.upper for p in f.points)
        assert 0 < f.metrics.mape < 15 and f.top_features
        m = S.ModelMetrics.model_validate(c.get("/api/v1/metrics").json())
        assert {n.network for n in m.forecasting} == {"water", "electricity", "cooling"}
        assert 0 < m.detection["precision"] <= 1 and 0 < m.detection["recall"] <= 1
        assert c.get("/api/v1/forecast?network=cooling&zoneId=zone-res-2").status_code == 404
        S.ErrorResponse.model_validate(c.get("/api/v1/forecast?network=cooling&zoneId=zone-res-2").json())


def test_assumptions_roundtrip():
    with TestClient(app) as c:
        items = [S.Assumption.model_validate(a) for a in c.get("/api/v1/assumptions").json()]
        assert any(a.key == "water_price" for a in items)
        r = c.put("/api/v1/assumptions", json={"water_price": 9.5}).json()
        assert next(a for a in r["assumptions"] if a["key"] == "water_price")["value"] == 9.5
        assert c.put("/api/v1/assumptions", json={"nope": 1}).status_code == 422
        c.post("/api/v1/scenarios/reset")
        assert next(a for a in c.get("/api/v1/assumptions").json() if a["key"] == "water_price")["value"] == 8
