"""Every fixture must validate against its schema and survive a lossless round trip."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app import schemas as S
from app.schemas import lab as L

FIX = Path(__file__).resolve().parents[2] / "fixtures"

REST = {
    "topology": S.Topology, "health": S.Health, "alerts": list[S.Alert], "incidents": list[S.Incident],
    "scenarios": list[S.Scenario], "scenario_run": S.ScenarioRunResponse, "forecast": S.Forecast,
    "metrics": S.ModelMetrics, "assumptions": list[S.Assumption], "calculation": S.CalculationRecord,
    "chat_accepted": S.ChatAccepted, "compare": S.Compare, "sensor_placement": list[S.PlacementSuggestion],
    "drift": list[S.DriftIndicator], "error": S.ErrorResponse, "decision": S.DecisionReport,
    "sensor_history": S.SensorHistory, "kpis": list[S.Kpi], "savings": S.SavingsReport, "series_demand": S.DemandSeries,
    "forecast_evidence": S.ForecastEvidence, "lab_benchmark": L.LabBenchmark, "lab_sensors": L.SensorValue, "lab_localization": L.LabLocalization,
    "lab_quality": L.LabQuality, "incident_localization": L.Localization, "lab_trial": L.TrialResult, "quality_now": L.QualityNow, "board_session": S.BoardSession, "board_decision_executing": S.BoardDecision, "warnings": list[S.EarlyWarning], "why_kpi": S.WhyRecord, "why_warning": S.WhyRecord, "guardrail_probe": S.GuardrailResult, "series_substations": S.SubstationSeries, "series_cooling_efficiency": S.DailySeries,
}


def load(rel):
    return json.loads((FIX / rel).read_text())


def roundtrip(tp, data):
    ta = TypeAdapter(tp)
    obj = ta.validate_python(data)
    assert ta.dump_python(obj, mode="json", by_alias=True, exclude_none=True) == data


@pytest.mark.parametrize("name", REST)
def test_rest_fixture(name):
    roundtrip(REST[name], load(f"rest/{name}.json"))


@pytest.mark.parametrize("type_", S.WS_PAYLOADS)
def test_ws_fixture(type_):
    msg = load(f"ws/{type_}.json")
    assert msg["type"] == type_
    roundtrip(S.WS_PAYLOADS[type_], msg["payload"])


def test_audit_fixture_is_a_valid_chain():
    from app.board.audit import GENESIS
    from app.schemas.board import AuditVerification
    data = load("rest/audit.json")
    entries = [S.AuditEntry.model_validate(e) for e in data["entries"]]
    AuditVerification.model_validate(data["verification"])
    prev = GENESIS
    for e in entries:
        assert e.prev_hash == prev
        prev = e.hash
    assert data["verification"]["ok"] and data["verification"]["head"] == prev and data["verification"]["entries"] == len(entries)


def test_burst_sequence():
    seq = load("sequence/burst.json")
    types = [s["message"]["type"] for s in seq]
    for want in ("scenario_state", "reading", "alert", "agent_finding"):
        assert want in types
    for step in seq:
        S.WS_PAYLOADS[step["message"]["type"]].model_validate(step["message"]["payload"])


def test_unknown_fields_ignored():
    data = load("ws/alert.json")["payload"] | {"futureField": 1}
    assert "futureField" not in S.Alert.model_validate(data).dump()


def test_ids_follow_naming_rules():
    topo = S.Topology.model_validate(load("rest/topology.json"))
    prefix = {"water": "water-", "electricity": "elec-", "cooling": "cool-"}
    assert all(z.id.startswith("zone-") for z in topo.zones)
    assets = {a.id for a in topo.assets}
    for a in topo.assets:
        assert a.id.startswith(prefix[a.network])
    for s in topo.sensors:
        assert s.asset_id in assets and s.id.startswith(s.asset_id + "-")
    for link in topo.links:
        assert link.from_asset in assets and link.to_asset in assets
