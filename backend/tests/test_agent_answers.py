"""What the agents say has to be true of the situation they are in: every open incident has its own finding, the owner's questions are answered from every incident's own figures,
and an answer recorded for another situation is never replayed."""

import asyncio
import os

import pytest

os.environ.setdefault("LLM_OFFLINE", "1")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_OFFLINE", "1")
    from app.agents.llm import llm
    monkeypatch.setattr(llm, "client", None)  # the rules-only wording: what the owner gets when the language model is out of reach
    monkeypatch.setattr(llm, "cache", {})  # and no recorded answer, so what is asserted is what the code writes


def world(question: str):
    from scripts.stress.agent_review import run

    async def go():
        rt, svc = await run("scn-compound")
        out = await svc.orchestrator.ainvoke({"question": question, "incident_id": None, "on_route": None})
        return rt, svc, out

    return asyncio.run(go())


def test_findings_finished_in_the_same_instant_keep_their_own_ids():
    """Three analyses that finish together once got the same id, and the last replaced the others: only one of three open incidents had a finding."""
    from scripts.stress.agent_review import run

    rt, _ = asyncio.run(run("scn-compound"))
    ids = [f.id for f in rt.findings.values()]
    assert len(ids) == len(set(ids)) == len([i for i in rt.incidents.values()]) == 3
    assert {f.incident_id for f in rt.findings.values()} == set(rt.incidents)


def test_what_to_do_first_lists_every_open_incident_with_its_own_figures_most_expensive_hour_first():
    rt, svc, out = world("What should we do first and why?")
    fs = sorted(rt.findings.values(), key=lambda f: -f.cost.delay_per_hour)
    text = out["answer"]
    assert len(fs) == 3
    last = -1
    for f in fs:  # in order, each with its own hourly cost and cost of acting now
        pos = text.index(f.headline)
        assert pos > last
        last = pos
        assert f"{f.cost.delay_per_hour:,.0f} EGP an hour" in text and f"{f.cost.act_now:,.0f} EGP to act now" in text
    assert f"{sum(f.cost.delay_per_hour for f in fs):,.0f} EGP for each hour" in text
    assert "[figure removed]" not in text


def test_the_cost_of_waiting_n_hours_is_the_cost_models_formula_for_every_incident_not_one_incidents_record():
    from app.cost import _summary, policy_kind

    rt, svc, out = world("If we wait 10 hours before sending a crew, how much does it cost?")
    text, total = out["answer"], 0.0
    for f in rt.findings.values():
        facts = svc.detector.facts_for(f.incident_id)[0]
        want = _summary(facts, svc.A.with_override(f"delay_hours_{policy_kind(facts)}", 10.0))["cost_of_delay"]
        assert f"{rt.incidents[f.incident_id].title}: {want:,.0f} EGP" in text
        total += want
    assert f"Together: {total:,.0f} EGP" in text
    cooling = next(f for f in rt.findings.values() if f.agent == "cooling")  # the cooling fault's own horizon is 10 hours, so waiting 10 hours is its own cost of delay
    assert f"{cooling.cost.cost_of_delay:,.0f} EGP" in text


def test_an_answer_naming_an_asset_that_is_not_in_play_is_refused():
    from app.agents.orchestrator import names_ok

    assert names_ok("Isolate the burst at Main WP-GOV-01 and restore SS-04.", {"SS-04", "WP-GOV-01", "P-01"}) is True
    assert names_ok("Isolate the damaged pipe at P-04.", {"SS-04", "WP-GOV-01", "P-01"}) is False


def test_a_recorded_answer_is_replayed_only_for_the_situation_it_was_recorded_in():
    """The key of a recorded answer carries the open incidents. Recorded for a pipe burst, it is not replayed for a substation trip that was asked the same question."""
    from app.agents.orchestrator import situation
    from scripts.stress.agent_review import run

    async def go(scn):
        rt, svc = await run(scn)
        return situation(rt, svc.A, "what should we do first?", None, None)["signature"]

    a, b = asyncio.run(go("scn-pipe-burst")), asyncio.run(go("scn-substation-trip"))
    assert a and b and a != b
