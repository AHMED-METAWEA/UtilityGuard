"""FastAPI app: REST under /api/v1 and the live stream on /ws/stream."""

from __future__ import annotations

import itertools
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app import scenarios
from app.runtime import runtime
from app.schemas import Health, ScenarioRunRequest, ScenarioRunResponse, ScenarioState, ScenarioStopRequest, envelope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime.start()
    yield
    await runtime.stop()


app = FastAPI(title="UtilityGuard Twin", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
api = APIRouter(prefix="/api/v1")
_run_ids = itertools.count(1)


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


@api.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(alert_id: str) -> dict:
    a = runtime.alerts.get(alert_id)
    if not a:
        raise ApiError(404, "not_found", f"Alert {alert_id} does not exist.")
    a = a.model_copy(update={"state": "acknowledged"})
    runtime.alerts[alert_id] = a
    await runtime.broadcast("alert", a)
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
    for st in stopped:
        await runtime.broadcast("scenario_state", st)
    for ws in list(runtime.clients):
        await ws.send_json(envelope("snapshot", runtime.snapshot()))
    return {"status": "reset", "tick": runtime.engine.tick}


@app.websocket("/ws/stream")
async def stream(ws: WebSocket):
    await runtime.connect(ws)
    try:
        while True:
            await ws.receive_text()  # clients may ping; nothing else is expected
    except WebSocketDisconnect:
        runtime.disconnect(ws)


app.include_router(api)
