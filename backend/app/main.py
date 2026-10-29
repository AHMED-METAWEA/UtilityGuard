"""FastAPI app: REST under /api/v1 and the live stream on /ws/stream."""

from __future__ import annotations

import asyncio
import itertools
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.exceptions import HTTPException

import time
from datetime import timedelta

from app import dashboard, decision as decision_mod, extras, savings as savings_mod, scenarios
from app.agents.service import AgentService
from app.lab.benchmark import LabCache, summarize as summarize_benchmark, summarize_localization, summarize_quality, summarize_sensors
from app.lab.live import live_localization
from app.lab.quality import live_quality
from app.schemas.board import ConveneRequest
from app.schemas.lab import QualityFlagItem, QualityNow, TrialRequest
from app.ml.service import MLService
from app.runtime import runtime
from app.schemas import SensorHistory, SensorHistoryPoint, ChatAccepted, ChatRequest, Health, ScenarioRunRequest, ScenarioRunResponse, ScenarioState, ScenarioStopRequest, envelope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime.start()
    asyncio.get_running_loop().run_in_executor(None, lab_cache.warm)  # builds the detector benchmark once, off the event loop
    yield
    await runtime.stop()


app = FastAPI(title="UtilityGuard Twin", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
api = APIRouter(prefix="/api/v1")
_run_ids = itertools.count(1)
service = AgentService(runtime)
ml_service = MLService(runtime, service.detector)
history = dashboard.History(runtime.seed)
lab_cache = LabCache(runtime.seed)
_savings_cache: dict = {}


def get_savings():
    key = tuple(sorted(service.A.values.items()))
    hit = _savings_cache.get('v')
    if hit and hit[0] == key and time.time() - hit[1] < 20:
        return hit[2]
    rep = savings_mod.build_savings(runtime, history, service.A)
    _savings_cache['v'] = (key, time.time(), rep)
    return rep


@app.exception_handler(ApiError)
async def _api_error(_: Request, e: ApiError):
    return JSONResponse(status_code=e.status, content={"code": e.code, "message": e.message})


@app.exception_handler(HTTPException)
async def _http_error(_: Request, e: HTTPException):
    return JSONResponse(status_code=e.status_code, content={"code": "http_error", "message": str(e.detail)})


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError):
    return JSONResponse(status_code=422, content={"code": "invalid_request", "message": str(e.errors()[:3])})


def out(model):
    if isinstance(model, list):
        return [m.dump() for m in model]
    return model.dump()


@api.get("/health")
def health() -> dict:
    return out(Health(status="ok", llm=runtime.llm_mode, tick=runtime.engine.tick, seed=runtime.seed,
                      active_runs=[r.run_id for r in runtime.runs.values() if r.state == "running"]))


@api.get("/topology")
def topology() -> dict:
    return out(runtime.engine.g.topology)


@api.get("/alerts")
def alerts() -> list:
    return out(sorted(runtime.alerts.values(), key=lambda a: a.detected_at, reverse=True))


@api.get("/incidents")
def incidents() -> list:
    return out(sorted(runtime.incidents.values(), key=lambda i: i.opened_at, reverse=True))


@api.get("/incidents/{incident_id}/decision")
def decision(incident_id: str) -> dict:
    inc = runtime.incidents.get(incident_id)
    if not inc:
        raise ApiError(404, "not_found", f"Incident {incident_id} does not exist.")
    finding = next((runtime.findings[id_] for id_ in reversed(inc.finding_ids)
                    if id_ in runtime.findings and runtime.findings[id_].incident_id == incident_id
                    and runtime.findings[id_].explain.calculation_id == inc.calculation_id), None)
    if not finding:
        raise ApiError(404, "not_found", f"Finding for incident {incident_id} does not exist.")
    return out(decision_mod.from_finding(finding, service.A, runtime.seed))


@api.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(alert_id: str) -> dict:
    a = runtime.alerts.get(alert_id)
    if not a:
        raise ApiError(404, "not_found", f"Alert {alert_id} does not exist.")
    a = a.model_copy(update={"state": "acknowledged"})
    runtime.alerts[alert_id] = a
    await runtime.broadcast("alert", a)
    inc = runtime.incidents.get(a.incident_id)
    if inc and inc.state == "open" and all(runtime.alerts[x].state != "open" for x in inc.alert_ids):
        runtime.incidents[inc.id] = inc.model_copy(update={"state": "acknowledged"})
        await runtime.broadcast("incident", runtime.incidents[inc.id])
    return out(a)


@api.get("/scenarios")
def list_scenarios() -> list:
    return out(scenarios.CATALOG)


@api.post("/scenarios/run")
async def run_scenario(req: ScenarioRunRequest) -> dict:
    if req.scenario_id not in scenarios.BY_ID:
        raise ApiError(404, "unknown_scenario", f"Scenario {req.scenario_id} does not exist.")
    run_id = f"run-{next(_run_ids):04d}"
    for ev in scenarios.events_for(req.scenario_id, req.parameters, run_id):
        runtime.add_event(ev)
    st = ScenarioState(run_id=run_id, scenario_id=req.scenario_id, state="running", started_at=datetime.now(timezone.utc))
    runtime.runs[run_id] = st
    await runtime.broadcast("scenario_state", st)
    return out(ScenarioRunResponse(run_id=run_id, scenario_id=req.scenario_id, started_at=st.started_at))


@api.post("/scenarios/stop")
async def stop_scenario(req: ScenarioStopRequest) -> dict:
    st = runtime.runs.get(req.run_id)
    if not st:
        raise ApiError(404, "unknown_run", f"Run {req.run_id} does not exist.")
    runtime.engine.clear_events(req.run_id)
    st = st.model_copy(update={"state": "stopped"})
    runtime.runs[req.run_id] = st
    await runtime.broadcast("scenario_state", st)
    return out(st)


@api.post("/scenarios/reset")
async def reset() -> dict:
    stopped = [r.model_copy(update={"state": "stopped"}) for r in runtime.runs.values() if r.state == "running"]
    runtime.reset_state()
    service.A.reset()
    for st in stopped:
        await runtime.broadcast("scenario_state", st)
    for ws in list(runtime.clients):
        await ws.send_json(envelope("snapshot", runtime.snapshot()))
    return {"status": "reset", "tick": runtime.engine.tick}


@api.post("/chat")
async def chat(req: ChatRequest) -> dict:
    if not req.message.strip():
        raise ApiError(422, "empty_message", "Message must not be empty.")
    return out(ChatAccepted(message_id=service.chat(req.message, req.incident_id)))


@api.get("/calculations/{calc_id}")
def calculation(calc_id: str) -> dict:
    rec = runtime.calculations.get(calc_id)
    if not rec:
        raise ApiError(404, "not_found", f"Calculation {calc_id} does not exist.")
    return out(rec)


@api.get("/assumptions")
def get_assumptions() -> list:
    return out(service.A.all())


@api.put("/assumptions")
async def put_assumptions(changes: dict[str, float]) -> dict:
    try:
        service.A.update(changes)
    except KeyError as e:
        raise ApiError(422, "unknown_assumption", f"Unknown assumption {e.args[0]}.") from e
    n = await service.recompute()
    return {"assumptions": out(service.A.all()), "recomputed": n}


@api.get("/forecast")
def forecast(network: str = "water", zoneId: str = "zone-gov", horizonHours: int = 24) -> dict:  # noqa: N803
    try:
        return out(ml_service.forecast(network, zoneId, max(1, min(horizonHours, 72))))
    except KeyError as e:
        raise ApiError(404, "no_forecast", f"No forecast for {network} in {zoneId}.") from e


@api.get("/lab/benchmark")
async def lab_benchmark() -> dict:
    data = await asyncio.to_thread(lab_cache.ensure)
    return out(summarize_benchmark(data, service.A))


@api.get("/lab/localization")
async def lab_localization() -> dict:
    data = await asyncio.to_thread(lab_cache.ensure)
    return out(summarize_localization(data, len(runtime.engine.g.topology.sensors)))


@api.get("/lab/quality")
async def lab_quality() -> dict:
    data = await asyncio.to_thread(lab_cache.ensure)
    return out(summarize_quality(data))


@api.get("/quality")
async def quality_now() -> dict:
    qm = await asyncio.to_thread(lab_cache.live_quality_model)
    flags = live_quality(runtime, qm)
    return out(QualityNow(sensors_total=len(qm.sensors), flagged=[QualityFlagItem(sensor_id=f.sensor_id, status=f.status, detail=f.detail) for f in flags]))


@api.get("/lab/sensors")
async def lab_sensors() -> dict:
    data = await asyncio.to_thread(lab_cache.ensure)
    return out(summarize_sensors(data, service.A))


@api.post("/lab/trial")
async def lab_trial(req: TrialRequest) -> dict:
    return out(await asyncio.to_thread(lab_cache.trial, req, service.A))


@api.get("/incidents/{incident_id}/localization")
async def incident_localization(incident_id: str) -> dict:
    inc = runtime.incidents.get(incident_id)
    if not inc:
        raise ApiError(404, "no_incident", f"No incident {incident_id}.")
    if service.detector.state.incident_kind.get(incident_id) != "leak":
        raise ApiError(404, "no_localization", "Localization applies to leak incidents.")
    m = await asyncio.to_thread(lab_cache.models)
    return out(live_localization(runtime, m.loc, incident_id))


@api.post("/board/convene")
async def board_convene(req: ConveneRequest) -> dict:
    if req.incident_id and req.incident_id not in runtime.incidents:
        raise ApiError(404, "no_incident", f"No incident {req.incident_id}.")
    if not service.board.open_tasks():
        raise ApiError(409, "nothing_to_decide", "There are no open problems for the board to decide on.")
    return out(service.board.convene(req.incident_id))


@api.get("/board/sessions/{session_id}")
async def board_session(session_id: str) -> dict:
    s = service.board.sessions.get(session_id)
    if not s:
        raise ApiError(404, "no_session", f"No board session {session_id}.")
    return out(s)


@api.get("/board/latest")
async def board_latest(incidentId: str | None = None) -> dict:
    s = service.board.latest(incidentId)
    if not s:
        raise ApiError(404, "no_session", "The board has not met yet.")
    return out(s)


@api.get("/forecast/evidence")
def forecast_evidence(network: str = "water") -> dict:
    if network not in ("water", "electricity", "cooling"):
        raise ApiError(404, "no_forecast", f"No forecast evidence for {network}.")
    return out(ml_service.evidence(network))


@api.get("/metrics")
def metrics() -> dict:
    return out(ml_service.metrics())


@api.get("/incidents/{incident_id}/report")
def report(incident_id: str) -> Response:
    if incident_id not in runtime.incidents:
        raise ApiError(404, "not_found", f"Incident {incident_id} does not exist.")
    pdf = extras.incident_pdf(runtime, incident_id)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="utilityguard-{incident_id}.pdf"'})


@api.get("/compare")
async def compare(scenarioId: str = "scn-pipe-burst") -> dict:  # noqa: N803
    if scenarioId not in scenarios.BY_ID:
        raise ApiError(404, "unknown_scenario", f"Scenario {scenarioId} does not exist.")
    return out(await extras.compare(runtime, service, scenarioId))


@api.get("/sensor-placement")
def sensor_placement() -> list:
    return out(extras.sensor_placement(runtime))


@api.get("/drift")
def drift() -> list:
    return out(extras.drift(runtime))


@api.get("/kpis")
def kpis() -> list:
    return out(dashboard.build_kpis(runtime, history, get_savings().total_monthly))


@api.get("/series/demand")
def series_demand(network: str = "water", zoneId: str = "all", hours: int = 24) -> dict:  # noqa: N803
    try:
        return out(dashboard.demand_series(runtime, history, ml_service, network, zoneId, max(6, min(hours, 48))))
    except KeyError as e:
        raise ApiError(404, "no_series", f"No demand series for {network} in {zoneId}.") from e


@api.get("/series/substations")
def series_substations() -> dict:
    return out(dashboard.substation_series(runtime, history))


@api.get("/series/cooling-efficiency")
def series_cooling_efficiency(days: int = 30) -> dict:
    return out(dashboard.cooling_efficiency_daily(runtime, history, max(7, min(days, 60))))


@api.get("/sensors/{sensor_id}/history")
def sensor_history(sensor_id: str, minutes: int = 10) -> dict:
    sensor = runtime.engine.g.sensor_index.get(sensor_id)
    if not sensor:
        raise ApiError(404, "not_found", f"Sensor {sensor_id} does not exist.")
    vals = runtime.history.get(sensor_id, [])
    exp = runtime.expected_history.get(sensor_id, [])
    n = min(len(vals), max(5, min(minutes, 30) * 30))  # one reading every 2 s
    pts = []
    for k in range(len(vals) - n, len(vals)):
        v = vals[k]
        if v != v:  # a missing reading
            continue
        e = exp[k] if k < len(exp) else None
        pts.append(SensorHistoryPoint(t=runtime.now - timedelta(seconds=2 * (len(vals) - 1 - k)), v=round(v, 3), expected=round(e, 3) if e is not None else None))
    return out(SensorHistory(sensor_id=sensor_id, unit=sensor.unit, normal_range=sensor.normal_range, points=pts))


@api.get("/savings")
def savings() -> dict:
    return out(get_savings())


@api.get("/findings")
def findings() -> list:
    return out(list(runtime.findings.values()))


@app.websocket("/ws/stream")
async def stream(ws: WebSocket):
    await runtime.connect(ws)
    try:
        while True:
            await ws.receive_text()  # clients may ping; nothing else is expected
    except WebSocketDisconnect:
        runtime.disconnect(ws)


app.include_router(api)


# One command for the demo: the backend also serves the built web app (npm run build in frontend/).
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
