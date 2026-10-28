"""Generate /fixtures from the Pydantic schemas so every sample is valid by construction.

Run: .venv/bin/python -m scripts.make_fixtures
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.schemas import (
    AgentFinding, Alert, Assumption, AssetStatus, CalcInput, Calculation, CalculationRecord, ChatAccepted,
    ChatChunk, ChatDone, Compare, CompareSide, Cost, CostItem, DriftIndicator, Explanation, FeatureImportance,
    Forecast, ForecastMetrics, ForecastPoint, Health, Incident, LatLng, ModelMetrics, NetworkMetrics,
    PlacementSuggestion, PredictedImpact, Reading, ReadingBatch, Reason, RecommendedAction, RouteStep, Scenario,
    ScenarioParam, ScenarioRunResponse, ScenarioState, Sensitivity, Snapshot, envelope,
)
from app.sim.engine import Engine
from app.sim.topology import build

ROOT = Path(__file__).resolve().parents[2] / "fixtures"
T0 = datetime(2026, 10, 28, 11, 30, 0, tzinfo=timezone.utc)
Engine()  # calibrates normal ranges in place, same as the live backend
g = build()
topo = g.topology


def write(rel: str, data) -> None:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def mid(s):
    r = s.normal_range
    return round((r.min + r.max) / 2, 2)


# ---------- readings ----------
def batch(tick: int, overrides: dict[str, float] | None = None) -> ReadingBatch:
    ts = T0 + timedelta(seconds=2 * tick)
    out = []
    for i, s in enumerate(topo.sensors):
        v = mid(s) * (1 + 0.02 * math.sin(tick / 5 + i))
        if overrides and s.id in overrides:
            v = overrides[s.id]
        out.append(Reading(sensor_id=s.id, timestamp=ts, value=round(v, 2), quality="ok",
                           forecast=round(mid(s), 2)))
    return ReadingBatch(tick=tick, timestamp=ts, readings=out)


PIPE, PUMP, ZONE = g.zone_pipe["zone-gov"], g.zone_pump["zone-gov"], "zone-gov"

# ---------- explanation and cost (pipe burst in zone-gov) ----------
assumptions = [
    Assumption(name="Water production cost", key="water_price", value=8, unit="EGP/m3", source="default assumption"),
    Assumption(name="Electricity tariff", key="elec_tariff", value=1.6, unit="EGP/kWh", source="default assumption"),
    Assumption(name="Cooling energy cost", key="cooling_price", value=2.2, unit="EGP/ton-h", source="default assumption"),
    Assumption(name="Crew call-out cost", key="crew_callout", value=3400, unit="EGP/dispatch", source="default assumption"),
    Assumption(name="Pipe repair parts", key="parts_pipe", value=15000, unit="EGP/repair", source="default assumption"),
    Assumption(name="Disruption penalty, government zone", key="penalty_government", value=2500, unit="EGP/h", source="default assumption"),
    Assumption(name="Hours until repair if delayed", key="delay_hours", value=6, unit="h", source="default assumption"),
]
calcs = [
    Calculation(name="Water loss per hour", formula="Q_leak × P_water",
                inputs=[CalcInput(name="Q_leak", value=140, unit="m3/h", source="measured"),
                        CalcInput(name="P_water", value=8, unit="EGP/m3", source="assumed")], result=1120),
    Calculation(name="Cost of delay per hour", formula="C_loss + C_disruption + C_secondary",
                inputs=[CalcInput(name="C_loss", value=1120, unit="EGP/h", source="rule"),
                        CalcInput(name="C_disruption", value=2500, unit="EGP/h", source="assumed"),
                        CalcInput(name="C_secondary", value=200, unit="EGP/h", source="rule")], result=3820),
    Calculation(name="Repair now", formula="crew_callout + parts + equipment",
                inputs=[CalcInput(name="crew_callout", value=3400, unit="EGP", source="assumed"),
                        CalcInput(name="parts", value=15000, unit="EGP", source="assumed")], result=18400),
    Calculation(name="Saving from acting now", formula="(C_delay × t_delay + C_repair_late) − C_repair_now",
                inputs=[CalcInput(name="C_delay", value=3820, unit="EGP/h", source="rule"),
                        CalcInput(name="t_delay", value=6, unit="h", source="assumed"),
                        CalcInput(name="C_repair_late", value=31500, unit="EGP", source="rule"),
                        CalcInput(name="C_repair_now", value=18400, unit="EGP", source="rule")], result=36020),
]
reasons = [
    Reason(text="Pressure fell from 3.8 to 2.1 bar in 4 minutes", evidence_type="measured", evidence_ref=f"{PIPE}-pressure"),
    Reason(text="Inflow is 140 m3/h higher than outflow", evidence_type="measured", evidence_ref=f"{PIPE}-flow-in"),
    Reason(text="Neighbouring sensors read normal, so this is not a sensor fault", evidence_type="rule", evidence_ref="rule:neighbour-consistency"),
    Reason(text="Pump P-04 draw is up 12% as it compensates", evidence_type="measured", evidence_ref=f"{PUMP}-flow"),
]
explain = Explanation(
    reasons=reasons, calculations=calcs, assumptions=assumptions,
    sensitivity=[Sensitivity(input="water_price", change_percent=-20, new_saving=34676),
                 Sensitivity(input="water_price", change_percent=20, new_saving=37364)],
    confidence=0.92, calculation_id="calc-0001")
cost = Cost(act_now=18400, delay_per_hour=3820, expected_saving=36020, delay_hours=6, cost_of_delay=54420,
            breakdown=[CostItem(label="Crew call-out", amount=3400, cause="assumed crew cost"),
                       CostItem(label="Pipe repair parts", amount=15000, cause="assumed parts cost"),
                       CostItem(label="Water loss", amount=6720, cause="140 m3/h × 6 h × 8 EGP"),
                       CostItem(label="Service disruption", amount=15000, cause="2,500 EGP/h × 6 h"),
                       CostItem(label="Secondary effects", amount=1200, cause="pump overdraw 200 EGP/h × 6 h"),
                       CostItem(label="Late repair", amount=31500, cause="grown burst repair cost")],
            explanation=explain)

alert = Alert(id="alert-0001", incident_id="inc-0001", network="water", zone_id=ZONE, asset_ids=[PIPE, PUMP],
              severity="critical", title="Pipe burst suspected",
              summary="Pressure drop and flow mismatch on the Government District main.",
              detected_at=T0 + timedelta(seconds=40), confidence=0.92)
incident = Incident(id="inc-0001", title="Pipe burst, Government District", networks=["water"], zone_ids=[ZONE],
                    severity="critical", state="open", opened_at=alert.detected_at, alert_ids=[alert.id],
                    finding_ids=["find-0001"], calculation_id="calc-0001")
finding = AgentFinding(
    id="find-0001", incident_id="inc-0001", agent="water", headline="Estimated leak of 140 m3/h on the Government main",
    explanation="The Government District main is losing about 140 m3/h. Send a pipe repair crew now.",
    probable_causes=["Pipe burst on the main segment", "Joint failure under pressure surge"],
    predicted_impact=PredictedImpact(text="Pressure in the Government District falls below 2.5 bar within 10 minutes.", zone_ids=[ZONE, "zone-cbd"]),
    recommended_action=RecommendedAction(text="Isolate the segment and send a pipe repair crew", crew_type="pipe repair", urgency="immediate"),
    cost=cost, reasoning_steps=["Read pressure and flow on the main", "Flow mismatch 140 m3/h detected",
                                "Checked neighbouring sensors: consistent", "Queried cost model", "Ranked action"],
    explain=explain, created_at=T0 + timedelta(seconds=43))
record = CalculationRecord(id="calc-0001", incident_id="inc-0001", created_at=finding.created_at, cost=cost, explanation=explain)

scenarios = [
    Scenario(id="scn-pipe-burst", name="Pipe burst", description="A main pipe bursts and loses water.", network="water",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default=PIPE),
                         ScenarioParam(name="severity", type="number", default=0.6, min=0.1, max=1)]),
    Scenario(id="scn-stuck-sensor", name="Stuck sensor", description="A sensor freezes on one value.", network="water",
             parameters=[ScenarioParam(name="targetSensor", type="select", default=f"{g.zone_pipe['zone-cbd']}-pressure")]),
    Scenario(id="scn-substation-trip", name="Substation trip", description="A substation trips; pumps and cooling lose power.", network="multi",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default="elec-sub-02")]),
    Scenario(id="scn-pump-failure", name="Pump failure", description="A pump degrades and stops.", network="water",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default=g.zone_pump["zone-cbd"])]),
    Scenario(id="scn-cooling-fault", name="Cooling plant fault", description="A chiller fails and supply temperature rises.", network="cooling",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default="cool-plant-01")]),
    Scenario(id="scn-heat-wave", name="Heat wave", description="Air temperature rises to 46 °C.", network="multi",
             parameters=[ScenarioParam(name="temperature", type="number", default=46, min=38, max=50)]),
    Scenario(id="scn-holiday", name="Holiday demand", description="Government zones quiet, residential busy.", network="multi",
             parameters=[ScenarioParam(name="factor", type="number", default=1.3, min=1, max=1.8)]),
]

# forecast
fpts = []
for h in range(24):
    base = 640 * (1 + 0.35 * math.sin((h + 2) / 24 * 2 * math.pi - math.pi / 2))
    fpts.append(ForecastPoint(timestamp=T0 + timedelta(hours=h), predicted=round(base, 1),
                              lower=round(base * 0.93, 1), upper=round(base * 1.07, 1)))
feats = [FeatureImportance(feature=f, importance=v) for f, v in
         [("hour_of_day", 0.38), ("lag_1h", 0.24), ("temperature", 0.16), ("weekday", 0.12), ("lag_24h", 0.10)]]
forecast = Forecast(network="water", zone_id=ZONE, unit="m3/h", horizon_hours=24, points=fpts,
                    metrics=ForecastMetrics(mae=18.4, rmse=24.1, mape=3.2), top_features=feats)
metrics = ModelMetrics(
    forecasting=[NetworkMetrics(network="water", mae=18.4, rmse=24.1, mape=3.2, unit="m3/h"),
                 NetworkMetrics(network="electricity", mae=0.9, rmse=1.3, mape=3.8, unit="MW"),
                 NetworkMetrics(network="cooling", mae=0.6, rmse=0.8, mape=4.6, unit="MW")],
    detection={"precision": 0.93, "recall": 0.89, "f1": 0.91}, last_run=T0 - timedelta(minutes=20),
    feature_importances={"forecast-water": feats,
                         "failure": [FeatureImportance(feature=f, importance=v) for f, v in
                                     [("vibration_trend", 0.31), ("run_hours", 0.27), ("pressure_variance", 0.22), ("age_years", 0.20)]]})

statuses = [AssetStatus(asset_id=a.id, status="normal", health_score=92.0, failure_probability=0.03) for a in topo.assets]
burst_reading = batch(20, {f"{PIPE}-pressure": 2.1, f"{PIPE}-flow-in": 1980, f"{PIPE}-flow-out": 1840, f"{PUMP}-flow": 760})
run_state = ScenarioState(run_id="run-0001", scenario_id="scn-pipe-burst", state="running", started_at=T0 + timedelta(seconds=30))

# ---------- REST ----------
write("rest/topology.json", topo.dump())
write("rest/health.json", Health(status="ok", llm="live", tick=20, seed=42, active_runs=[]).dump())
write("rest/alerts.json", [alert.dump()])
write("rest/incidents.json", [incident.dump()])
write("rest/scenarios.json", [s.dump() for s in scenarios])
write("rest/scenario_run.json", ScenarioRunResponse(run_id="run-0001", scenario_id="scn-pipe-burst", started_at=run_state.started_at).dump())
write("rest/forecast.json", forecast.dump())
write("rest/metrics.json", metrics.dump())
write("rest/assumptions.json", [a.dump() for a in assumptions])
write("rest/calculation.json", record.dump())
write("rest/chat_accepted.json", ChatAccepted(message_id="msg-0001").dump())
write("rest/compare.json", Compare(
    scenario_id="scn-pipe-burst",
    without_intervention=CompareSide(label="No action for 6 h", total_cost=54420, water_lost_m3=840, outage_hours=6,
                                     series=[ForecastPoint(timestamp=T0 + timedelta(hours=h), predicted=3.8 - 0.25 * h, lower=3.7 - 0.25 * h, upper=3.9 - 0.25 * h) for h in range(7)]),
    with_intervention=CompareSide(label="Repair crew sent now", total_cost=18400, water_lost_m3=140, outage_hours=1,
                                  series=[ForecastPoint(timestamp=T0 + timedelta(hours=h), predicted=2.1 if h == 0 else 3.7, lower=2.0 if h == 0 else 3.6, upper=2.2 if h == 0 else 3.8) for h in range(7)]),
    saving=36020).dump())
write("rest/sensor_placement.json", [PlacementSuggestion(
    position=LatLng(lat=30.0285, lng=31.7285), near_asset_id=g.zone_pipe["zone-cbd"], network="water", kind="pressure",
    detection_improvement=0.14, reason="Longest unmonitored segment between two pressure sensors").dump()])
write("rest/drift.json", [DriftIndicator(sensor_id=f"{g.zone_pipe['zone-cbd']}-pressure", drift=0.18, unit="bar", status="watch").dump(),
                          DriftIndicator(sensor_id="elec-sub-02-load", drift=0.4, unit="MW", status="ok").dump()])
write("rest/error.json", {"code": "not_found", "message": "Incident inc-9999 does not exist."})

# ---------- WS ----------
snap = Snapshot(tick=0, timestamp=T0, topology=topo, latest=batch(0).readings, assets=statuses, alerts=[], incidents=[],
                findings=[], scenarios=[], llm="live")
write("ws/snapshot.json", envelope("snapshot", snap))
write("ws/reading.json", envelope("reading", batch(1)))
write("ws/asset_status.json", envelope("asset_status", AssetStatus(asset_id=PIPE, status="fault", health_score=34, failure_probability=0.88)))
write("ws/alert.json", envelope("alert", alert))
write("ws/agent_finding.json", envelope("agent_finding", finding))
write("ws/scenario_state.json", envelope("scenario_state", run_state))
write("ws/forecast_update.json", envelope("forecast_update", forecast))
write("ws/chat_chunk.json", envelope("chat_chunk", ChatChunk(message_id="msg-0001", text="A trip at SS-02 stops pump P-04, ")))
routing = [RouteStep(agent="electricity", reason="Substation trip and load transfer"),
           RouteStep(agent="water", reason="SS-02 powers pumps P-04 and P-05"),
           RouteStep(agent="cooling", reason="SS-02 powers DCP-01")]
write("ws/chat_done.json", envelope("chat_done", ChatDone(message_id="msg-0001", agents_consulted=["electricity", "water", "cooling"],
                                                          routing=routing, calculation_id="calc-0001")))

# ---------- full scenario sequence: normal -> burst -> alert -> finding -> resolution ----------
seq = [
    (0, envelope("scenario_state", run_state)),
    (2000, envelope("reading", batch(16, {f"{PIPE}-pressure": 3.1, f"{PIPE}-flow-in": 1960, f"{PIPE}-flow-out": 1890}))),
    (2000, envelope("reading", burst_reading)),
    (500, envelope("asset_status", AssetStatus(asset_id=PIPE, status="fault", health_score=34, failure_probability=0.88))),
    (300, envelope("asset_status", AssetStatus(asset_id=PUMP, status="warning", health_score=71, failure_probability=0.22))),
    (500, envelope("alert", alert)),
    (3200, envelope("agent_finding", finding)),
    (8000, envelope("alert", alert.model_copy(update={"state": "resolved"}))),
    (0, envelope("asset_status", AssetStatus(asset_id=PIPE, status="normal", health_score=88, failure_probability=0.05))),
    (0, envelope("asset_status", AssetStatus(asset_id=PUMP, status="normal", health_score=92, failure_probability=0.03))),
    (0, envelope("scenario_state", run_state.model_copy(update={"state": "stopped"}))),
]
write("sequence/burst.json", [{"delayMs": d, "message": m} for d, m in seq])
print(f"fixtures written to {ROOT}")
