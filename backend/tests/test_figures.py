"""A figure cited by token is put in by code from the calculation, and nothing the model types around it can slip through."""

import asyncio
from datetime import datetime, timezone

from app.agents import specialists as sp
from app.agents.checker import REMOVED
from app.agents.figures import FigureSet, for_findings, for_record, for_sensors
from app.cost import Assumptions, IncidentFacts, compute
from app.schemas import Incident


def record(tmp_path, leak=140):
    return compute(IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=leak), Assumptions(tmp_path / "a.json"), "inc-1")


def test_a_token_becomes_the_calculated_figure(tmp_path):
    rec = record(tmp_path)
    fs = for_record(rec)
    out, unknown = fs.render("Acting now costs {{act_now}} and saves {{expected_saving}}.")
    assert unknown == []
    assert f"{rec.cost.act_now:,.0f} EGP" in out and f"{rec.cost.expected_saving:,.0f} EGP" in out and "{{" not in out


def test_a_token_that_is_not_in_the_table_is_removed_with_its_markup(tmp_path):
    fs = for_record(record(tmp_path))
    out, unknown = fs.render("It costs {{invented_cost}} or {made_up}.")
    assert unknown == ["{{invented_cost}}", "{made_up}"] and out == f"It costs {REMOVED} or {REMOVED}."


def test_models_drop_or_add_a_brace_and_a_unit(tmp_path):
    rec = record(tmp_path)
    fs = for_record(rec)
    shown = f"{rec.cost.act_now:,.0f} EGP"
    for written in ("{{act_now}", "{act_now}", "{{ act_now }}", "{{ACT_NOW}}"):
        assert fs.render(f"Cost {written}.")[0] == f"Cost {shown}.", written
    assert fs.render("Cost {{act_now}} EGP.")[0] == f"Cost {shown}."  # the token already carries its unit


def test_a_recorded_answer_follows_a_price_change(tmp_path):
    """An answer recorded with a token and replayed after an assumption changed shows today's figure, not the old one."""
    A = Assumptions(tmp_path / "a.json")
    facts = IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=140)
    before, after = compute(facts, A, "inc-1"), None
    A.update({"water_price": A["water_price"] * 1.2})
    after = compute(facts, A, "inc-1")
    assert before.cost.act_now != after.cost.act_now
    recorded = "Acting now costs {{act_now}}."
    assert for_record(before).render(recorded)[0] != for_record(after).render(recorded)[0]
    assert f"{after.cost.act_now:,.0f}" in for_record(after).render(recorded)[0]


def test_sensor_and_finding_figures_have_tokens_with_units():
    fs = for_sensors([{"sensor": "water-pipe-04-pressure", "unit": "bar", "now": 3.1, "min30": 2.9, "max30": 4.2}])
    assert fs.render("Pressure is {{water_pipe_04_pressure_now}}, down from {{water_pipe_04_pressure_max30}}.")[0] == "Pressure is 3.1 bar, down from 4.2 bar."
    f = type("F", (), {"id": "find-0007", "agent": "water", "cost": type("C", (), {"act_now": 36020.0, "expected_saving": 12500.0})()})()
    assert for_findings([f]).render("{{find_0007_act_now}} / {{find_0007_saving}}")[0] == "36,020 EGP / 12,500 EGP"


def test_the_table_shown_to_the_model_names_each_token_and_how_it_reads(tmp_path):
    rows = for_record(record(tmp_path)).table()
    assert rows[0]["token"] == "{{act_now}}"
    assert all(r["token"].startswith("{{") and r["means"] and r["reads"] for r in rows)


def test_a_specialist_that_cites_tokens_gets_the_calculated_figures_and_invented_ones_are_removed(tmp_path, monkeypatch):
    rt = _fake_runtime()
    A = Assumptions(tmp_path / "a.json")
    graph = sp.build_specialist(rt, A)
    facts = IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=140)
    inc = Incident(id="inc-1", title="Pipe burst", networks=["water"], zone_ids=["zone-gov"], severity="critical", state="open",
                   opened_at=datetime.now(timezone.utc), alert_ids=[])
    seen = {}

    async def fake(key, system, user, **kw):
        seen["user"] = user
        return {"headline": "Burst on the main", "explanation": "Acting now costs {{act_now}} against a delay cost of {{cost_of_delay}}, but 9,999,999 EGP is lost.",
                "probableCauses": ["Joint failure", "Pressure surge {{ghost}}"], "impact": "Gov zone", "action": "Isolate and repair"}, False

    monkeypatch.setattr(sp.llm, "json", fake)
    rt.alerts = {}
    out = asyncio.run(sp.run_specialist(graph, "water", inc, facts, "water-pipe-04"))
    rec = next(iter(rt.calculations.values()))
    assert f"{rec.cost.act_now:,.0f} EGP" in out.explanation and f"{rec.cost.cost_of_delay:,.0f} EGP" in out.explanation
    assert "9,999,999" not in out.explanation and REMOVED in out.explanation
    assert out.number_check == "corrected" and "{{ghost}}" not in " ".join(out.probable_causes)
    assert "{{act_now}}" in seen["user"] and '"cost"' not in seen["user"]  # the model is given the table of tokens, not a cost object to copy from


def _fake_runtime():
    """The smallest runtime the specialist graph needs: a topology, sensor history and the dicts it writes to."""
    from app.runtime import Runtime
    rt = Runtime(42)
    rt.alerts = {}
    return rt
