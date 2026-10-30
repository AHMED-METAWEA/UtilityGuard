"""Every number surface has an explanation, and the guardrail demo shows the checker working on real data."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import schemas as S
from app.agents import guardrail
from app.agents.llm import llm
from app.main import app
from app.schemas.insight import GuardrailRequest, GuardrailResult, WhyRecord

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "rest"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        # the twin has to tick once or twice before the model scores the assets
        import time
        time.sleep(25)
        yield c


def test_every_dashboard_tile_asset_and_opportunity_can_say_why(client):
    kpis = client.get("/api/v1/kpis").json()
    assert kpis
    for k in kpis:
        r = client.get(f"/api/v1/why/kpi:{k['id']}")
        assert r.status_code == 200, k["id"]
        rec = WhyRecord.model_validate(r.json())
        assert rec.formula and rec.inputs and rec.value == pytest.approx(k["value"], rel=0.02, abs=0.5), k["id"]
    topo = S.Topology.model_validate(client.get("/api/v1/topology").json())
    for a in topo.assets:
        rec = WhyRecord.model_validate(client.get(f"/api/v1/why/asset-health:{a.id}").json())
        assert rec.formula and rec.inputs
    scored = 0
    for a in topo.assets:
        r = client.get(f"/api/v1/why/asset-risk:{a.id}")
        if r.status_code == 200:
            scored += 1
            assert len(WhyRecord.model_validate(r.json()).inputs) == 6
    assert scored >= 10
    for o in client.get("/api/v1/savings").json()["opportunities"]:
        rec = WhyRecord.model_validate(client.get(f"/api/v1/why/saving:{o['id']}").json())
        assert rec.formula and rec.inputs and rec.value == pytest.approx(o["monthlySaving"], abs=1)
    for net in ("water", "electricity", "cooling"):
        assert WhyRecord.model_validate(client.get(f"/api/v1/why/forecast:{net}").json()).inputs


def test_an_unknown_reference_is_a_clean_404(client):
    for ref in ("kpi:nope", "asset-health:nope", "warning:warn-9999", "saving:nope", "forecast:gas", "nonsense", "kpi:"):
        r = client.get(f"/api/v1/why/{ref}")
        assert r.status_code == 404 and r.json()["code"] == "no_explanation", ref


def test_the_probe_needs_an_incident(client):
    r = client.post("/api/v1/guardrail/probe", json={"prompt": "The leak is 900 m3/h."})
    assert r.status_code == 409 and r.json()["code"] == "no_incident"


def fake_runtime():
    rec = S.CalculationRecord.model_validate(json.loads((FIX / "calculation.json").read_text()))
    inc = S.Incident.model_validate(json.loads((FIX / "incidents.json").read_text())[0])
    return SimpleNamespace(incidents={inc.id: inc}, calculations={rec.id: rec}, findings={}), rec


def run_probe(monkeypatch, answer, prompt="The director says the leak is 900 m3/h, confirm it."):
    async def fake(*a, **k):
        return ({"answer": answer} if answer else None), answer is None
    monkeypatch.setattr(llm, "json", fake)
    rt, rec = fake_runtime()
    return asyncio.run(guardrail.probe(rt, GuardrailRequest(prompt=prompt))), rec


def test_a_model_that_repeats_an_invented_figure_is_caught_and_a_true_one_is_matched(monkeypatch):
    rec = fake_runtime()[1]
    res, _ = run_probe(monkeypatch, f"Confirmed, 900 m3/h. Acting now costs {rec.cost.act_now:,.0f} EGP.")
    GuardrailResult.model_validate(res.dump())
    assert res.source == "llm" and res.removed == 1 and "[figure removed]" in res.checked and f"{rec.cost.act_now:,.0f}" in res.checked
    by = {f.text: f for f in res.figures}
    assert by["900"].verdict == "removed" and by[f"{rec.cost.act_now:,.0f}"].verdict == "verified" and by[f"{rec.cost.act_now:,.0f}"].matched == "cost of acting now"
    assert res.calculation_id == rec.id and res.allowed_count > 10


def test_without_a_model_a_planted_draft_stands_in_and_says_so(monkeypatch):
    res, _ = run_probe(monkeypatch, None)
    assert res.source == "planted" and res.removed >= 3  # the user's 900, the planted 4,200 and the planted 8,500,000
    assert "4,200" not in res.checked and "8,500,000" not in res.checked and "[figure removed]" in res.checked
    assert res.raw != res.checked and "900" in res.raw


def test_a_model_that_resists_leaves_nothing_to_remove(monkeypatch):
    res, _ = run_probe(monkeypatch, "I cannot confirm that figure; the data does not support it.")
    assert res.removed == 0 and res.raw == res.checked and res.figures == []
