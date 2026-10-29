import asyncio
import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app.board import session as bs
from app.board.analysis import analyse, calculation_record, decision_object
from app.board.plan import INF, Params, all_orders, evaluate, plan_for, tasks_from_alerts
from app.cost import Assumptions
from app.detection import Detector
from app.runtime import Runtime
from app.scenarios import events_for
from app.schemas.board import BoardFact

T0 = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)


def reference_cost(order, p: Params) -> float:
    """The same schedule written the slow, obvious way, one order at a time."""
    free = [p.response] * p.crews
    done = [INF] * len(order)
    total = 0.0
    for i in order:
        c = min(range(p.crews), key=lambda k: free[k])
        start = free[c]
        relief = min((done[j] for j in range(len(order)) if p.relieved[i, j]), default=INF)
        if relief <= start:
            done[i] = relief
            total += p.rate[i] * relief
            continue
        end = start + p.hours[i]
        free[c] = end + p.switch
        done[i] = end
        wait = max(0.0, start - p.response)
        total += p.rate[i] * end + p.repair[i] * min(p.cap, 1 + (p.late[i] - 1) * wait / p.delay[i])
    return total


def make_params(n, crews, seed, relieved=()):
    r = np.random.default_rng(seed)
    rel = np.zeros((n, n), dtype=bool)
    for i, j in relieved:
        rel[i, j] = True
    return Params(rate=r.uniform(500, 50000, n), repair=r.uniform(1000, 60000, n), hours=r.uniform(1, 5, n), late=r.uniform(1.1, 1.9, n),
                  delay=r.uniform(4, 10, n), cap=3.0, response=1.0, switch=0.5, crews=crews, relieved=rel)


@pytest.mark.parametrize("n,crews,seed", [(2, 1, 1), (3, 2, 2), (4, 1, 3), (5, 2, 4), (5, 3, 5), (6, 2, 6)])
def test_vectorised_schedule_matches_the_slow_reference(n, crews, seed):
    p = make_params(n, crews, seed)
    perms = all_orders(n)
    fast = evaluate(perms, p)
    slow = np.array([reference_cost(list(o), p) for o in perms])
    assert np.allclose(fast, slow)


def test_relief_matches_the_reference_and_cancels_work():
    p = make_params(4, 1, 9, relieved=[(3, 0)])  # task 3 is moot once task 0 is done
    perms = all_orders(4)
    assert np.allclose(evaluate(perms, p), [reference_cost(list(o), p) for o in perms])
    plan = plan_for([0, 3, 1, 2], p)
    assert plan.dropped[3] and plan.end[3] == plan.end[0]  # it stops costing when task 0 finishes, and takes no crew
    assert not plan_for([3, 0, 1, 2], p).dropped[3]  # done first, it is real work


def test_single_crew_follows_smiths_rule_when_waiting_does_not_raise_repair_cost():
    p = make_params(5, 1, 11)
    p.late[:] = 1.0  # no escalation, so only the return per crew hour matters
    perms = all_orders(5)
    best = [int(x) for x in perms[int(np.argmin(evaluate(perms, p)))]]
    smith = sorted(range(5), key=lambda i: -p.rate[i] / p.hours[i])
    assert best == smith


def test_enough_crews_means_everything_starts_at_once():
    p = make_params(3, 3, 12)
    plan = plan_for([0, 1, 2], p)
    assert np.allclose(plan.start, p.response)


def test_a_rewrite_must_carry_exactly_the_drafts_figures():
    draft = "SS-04 costs 430,570 EGP every hour and needs 1.5 h. It starts at hour 1.0."
    assert bs.same_figures("I see SS-04 bleeding 430,570 EGP an hour, with 1.5 h of work from hour 1.", draft)
    assert not bs.same_figures("SS-04 costs 430,570 EGP every hour and needs 1.5 h.", draft)  # dropped the start hour
    assert not bs.same_figures("SS-04 costs 430,570 EGP every hour, needs 1.5 h and 3 crews. It starts at hour 1.0.", draft)  # added a figure


def test_number_and_asset_guards():
    facts = [BoardFact(label="x", value=22508, unit="EGP", source="calculated"), BoardFact(label="h", value=2.5, unit="h", source="calculated")]
    assert bs.numbers_ok("It costs 22,508 EGP and needs 2.5 h.", facts)
    assert bs.numbers_ok("About 22,500 EGP.", facts)
    assert not bs.numbers_ok("It costs 23,000 EGP.", facts)
    assert not bs.numbers_ok("It needs 7 hours.", facts)  # a small count is not free
    assert bs.numbers_ok("SS-04 and P-04 are fine.", facts)  # asset ids are not quantities
    assert bs.assets_ok("Restore SS-04 first.", {"SS-04"})
    assert not bs.assets_ok("Restore SS-03 first.", {"SS-04"})
    assert bs.assets_ok("Fix WP-GOV-01 now.", {"Main WP-GOV-01"})


def three_failures():
    rt = Runtime(42)
    det = Detector(rt)
    rt.tick_hooks.append(det.tick)

    async def go():
        for i in range(30):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        for ev in events_for("scn-compound", {}, "run-c"):
            rt.add_event(ev)
        for i in range(30, 130):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))

    asyncio.run(go())
    return rt, det


@pytest.fixture(scope="module")
def compound():
    rt, det = three_failures()
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    tasks = tasks_from_alerts(rt, det, A)
    return rt, det, A, tasks, analyse(tasks, A, "board-test")


def test_three_failures_become_three_tasks_on_three_networks(compound):
    _, _, _, tasks, _ = compound
    assert {t.agent for t in tasks} == {"water", "electricity", "cooling"}
    assert {t.kind for t in tasks} == {"substation_trip", "leak", "cooling_fault"}
    assert all(t.rate > 0 and t.hours > 0 and t.repair > 0 for t in tasks)


def test_the_chosen_order_is_the_cheapest_of_all_orders_and_beats_the_alternatives(compound):
    _, _, A, tasks, a = compound
    p = a.params
    best = min(reference_cost(list(o), p) for o in itertools.permutations(range(len(a.tasks))))
    assert a.plan.cost == pytest.approx(best)
    assert all(x["extra"] >= -1e-6 for x in a.alternatives)
    assert a.alternatives[-1]["id"] == "worst" and a.alternatives[-1]["extra"] > 0
    assert a.conflict and a.params.crews == 2  # three problems, two crews: somebody waits
    assert 0.5 < a.robust.win_share <= 1.0 and a.robust.p10 <= a.robust.p50 <= a.robust.p90


def test_the_costliest_problem_goes_first_and_the_wait_is_priced(compound):
    _, _, _, _, a = compound
    first = a.tasks[sorted(range(len(a.tasks)), key=lambda i: (a.plan.start[i], i))[0]]
    assert first.kind == "substation_trip"
    waiting = [i for i in range(len(a.tasks)) if a.plan.start[i] > a.params.response + 1e-9]
    assert waiting
    assert any(r["kind"] == "queue" for r in a.risks)
    assert a.extra_crew_saving is None or a.extra_crew_saving > 0


def test_provenance_record_reconciles_to_the_plan_cost(compound):
    _, _, A, _, a = compound
    rec = calculation_record(a, A, "calc-board-test")
    assert sum(item.amount for item in rec.cost.breakdown) == pytest.approx(a.plan.cost, abs=0.05 * len(rec.cost.breakdown))
    assert rec.cost.act_now == round(a.plan.cost, 2)
    assert {"crews_available", "crew_switch_hours"} <= {x.key for x in rec.explanation.assumptions}


def test_meeting_text_quotes_only_computed_figures(compound, monkeypatch):
    _, _, _, _, a = compound

    async def no_model(*args, **kwargs):  # the language model is unavailable: the code's own sentences are used
        return None, True

    monkeypatch.setattr(bs.llm, "json", no_model)
    drafts = bs.compose(a)
    assert {d.round for d in drafts} == {0, 1, 2, 3, 4}
    assert {d.agent for d in drafts} >= {"orchestrator", "water", "electricity", "cooling"}
    for d in drafts:
        text, source = asyncio.run(bs.voice(d, "s", 0))
        assert source == "template" and text == d.template
        assert bs.assets_ok(text, d.names)
        assert bs.numbers_ok(text, d.facts), (d.kind, d.agent, text)
    sd = bs.summary_draft(a)
    text, _ = asyncio.run(bs.voice(sd, "s", 0))
    assert bs.numbers_ok(text, sd.facts), text


def test_a_model_that_invents_a_figure_or_an_asset_is_overruled(compound, monkeypatch):
    _, _, _, _, a = compound
    d = bs.compose(a)[0]

    for lie in (d.template.replace("3 problems", "9 problems"),
                d.template + " The order of work does not matter today.9",
                d.template.replace("The board is in session.", "The board is in session. SS-99 is the real problem here."),
                "The board is in session."):
        async def liar(*args, _lie=lie, **kwargs):
            return {"text": _lie}, False

        monkeypatch.setattr(bs.llm, "json", liar)
        text, source = asyncio.run(bs.voice(d, "s", 0))
        assert source == "template" and text == d.template


@pytest.fixture(scope="module")
def storm():
    rt = Runtime(42)
    det = Detector(rt)
    rt.tick_hooks.append(det.tick)

    async def go():
        for i in range(30):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        for ev in events_for("scn-storm", {}, "run-s"):
            rt.add_event(ev)
        for i in range(30, 180):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))

    asyncio.run(go())
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    return rt, det, A, tasks_from_alerts(rt, det, A)


def test_storm_has_two_substations_to_restore_and_water_and_cooling_speak_as_dependants(storm, monkeypatch):
    rt, det, A, tasks = storm
    assert [t.kind for t in tasks] == ["substation_trip", "substation_trip"]
    a = analyse(tasks, A, "board-storm")
    assert not a.conflict  # two electrical crews, two substations: both start at once
    drafts = bs.compose(a)
    dependants = {d.agent for d in drafts if d.kind == "dependent"}
    assert dependants == {"water", "cooling"}
    for d in drafts:
        if d.kind == "dependent":
            assert "already counted" in d.template and bs.numbers_ok(d.template, d.facts)


def test_with_one_crew_the_costlier_substation_is_restored_first(storm):
    rt, det, A, tasks = storm
    one = A.with_override("crews_available", 1.0)
    a = analyse(tasks, one, "board-one")
    assert a.conflict and a.params.crews == 1
    first = min(range(len(a.tasks)), key=lambda i: a.plan.start[i])
    assert a.tasks[first].rate == max(t.rate for t in a.tasks)
    assert a.extra_crew_saving and a.extra_crew_saving > 0  # a second crew would help, and the board says by how much


def test_a_tripped_backup_overload_is_relieved_by_restoring_the_substation_that_feeds_it():
    from app.board.plan import Task
    from app.cost import IncidentFacts
    f = IncidentFacts(kind="energy_waste", zone_types={"zone-gov": "government"}, excess_mw=5.0)
    trip = IncidentFacts(kind="substation_trip", zone_types={"zone-gov": "government"}, unserved_mw=20.0)
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    mk = lambda tid, kind, facts, policy, rel=(): Task(id=tid, incident_id="inc", agent="electricity", asset_id=tid, asset_name=tid, kind=kind, policy=policy, facts=facts,  # noqa: E731
                                                       action="x", crew="x", rate=1000.0 if kind == "energy_waste" else 50000.0, repair=1000.0, hours=1.5, detected_at=0.0, relieved_by=rel)
    tasks = [mk("trip", "substation_trip", trip, "substation_trip"), mk("over", "energy_waste", f, "energy_waste", ("trip",))]
    a = analyse(tasks, A.with_override("crews_available", 1.0), "board-relief")
    steps = {s.task_id: s for s in __import__("app.board.analysis", fromlist=["board_steps"]).board_steps(a)}
    assert steps["over"].dropped and steps["over"].relief_hour == steps["trip"].end_hour
    assert steps["over"].repair_cost == 0


def test_more_problems_than_the_board_can_search_are_reported_not_silently_dropped():
    from app.board.plan import Task
    from app.cost import IncidentFacts
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    f = IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=100.0)
    tasks = [Task(id=f"a{i}", incident_id="inc", agent="water", asset_id=f"p{i}", asset_name=f"P-0{i}", kind="leak", policy="pipe_burst", facts=IncidentFacts(**{**f.__dict__, "leak_m3h": 50.0 + 20 * i}),
                  action="x", crew="x", rate=1000.0 + 500 * i, repair=1000.0, hours=2.0, detected_at=float(i)) for i in range(8)]
    a = analyse(tasks, A, "board-many")
    assert len(a.tasks) == 6 and a.omitted == 2
    assert {t.id for t in a.tasks} == {f"a{i}" for i in range(2, 8)}  # the six with the highest hourly cost
    assert any("not scheduled" in r["text"] for r in a.risks)


def test_a_single_job_says_so_instead_of_claiming_an_order_was_chosen(monkeypatch):
    from app.board.plan import Task
    from app.cost import IncidentFacts
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    f = IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=100.0)
    t = Task(id="a1", incident_id="inc", agent="water", asset_id="p1", asset_name="P-01", kind="leak", policy="pipe_burst", facts=f, action="Fix it", crew="x", rate=1000.0, repair=1000.0, hours=2.0, detected_at=0.0)
    a = analyse([t], A, "board-one-job")
    decision = next(d for d in bs.compose(a) if d.kind == "decision")
    assert "single job" in decision.template and "tried every order" not in decision.template
    assert not a.conflict
