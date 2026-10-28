"""Every fixture must validate against its schema and survive a lossless round trip."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app import schemas as S

FIX = Path(__file__).resolve().parents[2] / "fixtures"

REST = {
    "topology": S.Topology, "health": S.Health, "alerts": list[S.Alert], "incidents": list[S.Incident],
    "scenarios": list[S.Scenario], "scenario_run": S.ScenarioRunResponse, "forecast": S.Forecast,
    "metrics": S.ModelMetrics, "assumptions": list[S.Assumption], "calculation": S.CalculationRecord,
    "chat_accepted": S.ChatAccepted, "compare": S.Compare, "sensor_placement": list[S.PlacementSuggestion],
    "drift": list[S.DriftIndicator], "error": S.ErrorResponse,
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
