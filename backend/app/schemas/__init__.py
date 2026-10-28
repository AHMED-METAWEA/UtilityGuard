"""Pydantic models for every shape in docs/contract.md.

Python fields are snake_case; JSON is camelCase via aliases. Unknown fields are
ignored on input (contract section 8) and outputs are always dumped by alias.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Network = Literal["water", "electricity", "cooling"]
AgentName = Literal["water", "electricity", "cooling", "orchestrator"]
Severity = Literal["info", "warning", "critical"]
AssetStatusValue = Literal["normal", "warning", "fault", "offline"]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


# ---------- 4.1 Topology ----------

class LatLng(Model):
    lat: float
    lng: float


class Zone(Model):
    id: str
    name: str
    type: Literal["government", "business", "residential", "services"]
    center: LatLng
    polygon: list[LatLng]


class Geometry(Model):
    type: Literal["point", "line"]
    coordinates: list[LatLng]


class Asset(Model):
    id: str
    network: Network
    kind: Literal["pipe", "pump", "reservoir", "substation", "feeder", "transformer", "plant", "loop"]
    name: str
    zone_id: str
    geometry: Geometry
    status: AssetStatusValue = "normal"


class Range(Model):
    min: float
    max: float


class Sensor(Model):
    id: str
    asset_id: str
    network: Network
    kind: Literal["pressure", "flow", "load", "voltage", "temperature"]
    unit: str
    normal_range: Range
    position: LatLng


class Link(Model):
    from_asset: str
    to_asset: str
    relation: str


class Topology(Model):
    zones: list[Zone]
    assets: list[Asset]
    sensors: list[Sensor]
    links: list[Link]


# ---------- 4.2 / 4.3 ----------

class Reading(Model):
    sensor_id: str
    timestamp: datetime
    value: float | None
    quality: Literal["ok", "suspect", "missing"]
    forecast: float | None = None


class ReadingBatch(Model):
    tick: int
    timestamp: datetime
    readings: list[Reading]


class AssetStatus(Model):
    asset_id: str
    status: AssetStatusValue
    health_score: float = Field(ge=0, le=100)
    failure_probability: float | None = Field(default=None, ge=0, le=1)


# ---------- 4.4 Alert ----------

class Alert(Model):
    id: str
    incident_id: str
    network: Network
    zone_id: str
    asset_ids: list[str]
    severity: Severity
    title: str
    summary: str
    detected_at: datetime
    state: Literal["open", "acknowledged", "resolved"] = "open"
    confidence: float = Field(ge=0, le=1)


class Incident(Model):
    id: str
    title: str
    networks: list[Network]
    zone_ids: list[str]
    severity: Severity
    state: Literal["open", "acknowledged", "resolved"]
    opened_at: datetime
    alert_ids: list[str]
    finding_ids: list[str] = []
    calculation_id: str | None = None


# ---------- 4.6A Explanation ----------

class Reason(Model):
    text: str
    evidence_type: Literal["measured", "forecast", "rule", "assumed"]
    evidence_ref: str | None = None


class CalcInput(Model):
    name: str
    value: float
    unit: str
    source: Literal["measured", "forecast", "rule", "assumed"]


class Calculation(Model):
    name: str
    formula: str
    inputs: list[CalcInput]
    result: float
    unit: str = "EGP"


class Assumption(Model):
    name: str
    key: str
    value: float
    unit: str
    source: str
    user_changed: bool = False


class Sensitivity(Model):
    input: str
    change_percent: float
    new_saving: float


class Explanation(Model):
    reasons: list[Reason]
    calculations: list[Calculation]
    assumptions: list[Assumption]
    sensitivity: list[Sensitivity]
    confidence: float = Field(ge=0, le=1)
    calculation_id: str | None = None


# ---------- 4.6 Cost ----------

class CostItem(Model):
    label: str
    amount: float
    cause: str


class Cost(Model):
    currency: Literal["EGP"] = "EGP"
    act_now: float
    delay_per_hour: float
    expected_saving: float
    delay_hours: float | None = None
    cost_of_delay: float | None = None
    breakdown: list[CostItem]
    explanation: Explanation | None = None


class CalculationRecord(Model):
    id: str
    incident_id: str
    created_at: datetime
    cost: Cost
    explanation: Explanation


# ---------- 4.5 Finding ----------

class PredictedImpact(Model):
    text: str
    zone_ids: list[str]


class RecommendedAction(Model):
    text: str
    crew_type: str
    urgency: Literal["immediate", "within_hours", "scheduled"]


class AgentFinding(Model):
    id: str
    incident_id: str
    agent: AgentName
    headline: str
    explanation: str
    probable_causes: list[str]
    predicted_impact: PredictedImpact
    recommended_action: RecommendedAction
    cost: Cost
    reasoning_steps: list[str]
    explain: Explanation
    created_at: datetime | None = None
    fallback: bool = False
    number_check: Literal["passed", "corrected"] = "passed"


# ---------- 4.7 / 4.8 ----------

class ForecastPoint(Model):
    timestamp: datetime
    predicted: float
    lower: float
    upper: float


class ForecastMetrics(Model):
    mae: float
    rmse: float
    mape: float


class Forecast(Model):
    network: Network
    zone_id: str
    unit: str
    horizon_hours: int
    points: list[ForecastPoint]
    metrics: ForecastMetrics
    top_features: list[FeatureImportance] = []


class FeatureImportance(Model):
    feature: str
    importance: float


class NetworkMetrics(Model):
    network: Network
    mae: float
    rmse: float
    mape: float
    unit: str


class ModelMetrics(Model):
    forecasting: list[NetworkMetrics]
    detection: dict[str, float]  # precision, recall, f1
    last_run: datetime
    feature_importances: dict[str, list[FeatureImportance]]


# ---------- 5 Scenarios ----------

class ScenarioParam(Model):
    name: str
    type: Literal["number", "asset", "zone", "select"]
    default: Any
    options: list[Any] | None = None
    min: float | None = None
    max: float | None = None


class Scenario(Model):
    id: str
    name: str
    description: str
    network: Network | Literal["multi"]
    parameters: list[ScenarioParam]


class ScenarioRunRequest(Model):
    scenario_id: str
    parameters: dict[str, Any] = {}


class ScenarioRunResponse(Model):
    run_id: str
    scenario_id: str
    started_at: datetime


class ScenarioStopRequest(Model):
    run_id: str


class ScenarioState(Model):
    run_id: str
    scenario_id: str
    state: Literal["running", "stopped"]
    started_at: datetime


# ---------- 6 Chat ----------

class ChatRequest(Model):
    message: str
    incident_id: str | None = None


class ChatAccepted(Model):
    message_id: str


class RouteStep(Model):
    agent: AgentName
    reason: str


class ChatChunk(Model):
    message_id: str
    text: str
    agent: AgentName = "orchestrator"


class ChatRoute(Model):
    message_id: str
    routing: list[RouteStep]


class ChatDone(Model):
    message_id: str
    agents_consulted: list[AgentName]
    routing: list[RouteStep]
    fallback: bool = False
    calculation_id: str | None = None
    finding_ids: list[str] = []


# ---------- 7 Other ----------

class Health(Model):
    status: Literal["ok", "degraded"]
    llm: Literal["live", "fallback"]
    tick: int
    seed: int
    active_runs: list[str]


class CompareSide(Model):
    label: str
    total_cost: float
    water_lost_m3: float
    outage_hours: float
    series: list[ForecastPoint]


class Compare(Model):
    scenario_id: str
    without_intervention: CompareSide
    with_intervention: CompareSide
    saving: float


class PlacementSuggestion(Model):
    position: LatLng
    near_asset_id: str
    network: Network
    kind: str
    detection_improvement: float
    reason: str


class DriftIndicator(Model):
    sensor_id: str
    drift: float
    unit: str
    status: Literal["ok", "watch", "drifting"]


class ErrorResponse(Model):
    code: str
    message: str


# ---------- WS envelope ----------

class Snapshot(Model):
    tick: int
    timestamp: datetime
    topology: Topology
    latest: list[Reading]
    assets: list[AssetStatus]
    alerts: list[Alert]
    incidents: list[Incident]
    findings: list[AgentFinding]
    scenarios: list[ScenarioState]
    llm: Literal["live", "fallback"]


WS_PAYLOADS: dict[str, type[Model]] = {
    "snapshot": Snapshot,
    "reading": ReadingBatch,
    "asset_status": AssetStatus,
    "alert": Alert,
    "agent_finding": AgentFinding,
    "scenario_state": ScenarioState,
    "forecast_update": Forecast,
    "chat_route": ChatRoute,
    "chat_chunk": ChatChunk,
    "chat_done": ChatDone,
}


def envelope(type_: str, payload: Model) -> dict[str, Any]:
    assert type_ in WS_PAYLOADS and isinstance(payload, WS_PAYLOADS[type_]), type_
    return {"type": type_, "payload": payload.dump()}


Forecast.model_rebuild()
