"""Human approval of the board's decision, the crews it sends, and the tamper-evident log of all of it."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.board.analysis import analyse, calculation_record, decision_object
from app.board.audit import GENESIS, AuditLog
from app.board.dispatch import DecisionError
from app.board.plan import tasks_from_alerts
from app.board.session import Board
from app.cost import Assumptions
from app.detection import Detector
from app.runtime import Runtime
from app.scenarios import events_for
from app.schemas.board import BoardSession, ReviewRequest

T0 = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)


class World:
    """A runtime with the three failures running, a board with a proposal ready, and a clock the test advances."""

    def __init__(self, tmp_path: Path, monkeypatch, scenario: str = "scn-compound", ticks: int = 130):
        monkeypatch.setenv("AUDIT_PATH", str(tmp_path / "audit.jsonl"))
        self.rt = Runtime(42)
        self.det = Detector(self.rt)
        self.rt.tick_hooks.append(self.det.tick)
        self.A = Assumptions(path=Path("/nonexistent/assumptions.json"))
        self.board = Board(self.rt, SimpleNamespace(detector=self.det, A=self.A))
        self.i = 0
        self.scenario, self.ticks = scenario, ticks

    async def step(self, n: int) -> None:
        for _ in range(n):
            await self.rt.tick_once(T0 + timedelta(seconds=2 * self.i))
            self.i += 1

    async def start(self) -> None:
        await self.step(30)
        for ev in events_for(self.scenario, {}, "run-c"):
            self.rt.add_event(ev)
        await self.step(self.ticks - 30)

    async def propose(self, n: int = 1) -> BoardSession:
        tasks = tasks_from_alerts(self.rt, self.det, self.A)
        sid = f"board-{n:04d}"
        a = analyse(tasks, self.A, sid)
        rec = calculation_record(a, self.A, f"calc-board-{n:04d}")
        s = BoardSession(id=sid, state="done", started_at=datetime.now(timezone.utc), messages=[])
        s.decision = decision_object(a, "summary", "template", rec.id)
        self.board.sessions[sid] = s
        await self.board._propose(s, a)
        return s


def run(coro):
    return asyncio.run(coro)


def actions(world: World) -> list[str]:
    return [e.action for e in world.board.audit.entries()]


def test_approval_sends_the_crews_in_plan_order_clears_the_faults_and_the_detector_confirms(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        assert s.decision.status == "proposed"
        before = {e.kind for e in w.rt.engine.events}
        assert {"pipe_burst", "cooling_fault", "substation_trip"} <= before
        await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona", note="go", seconds_per_hour=1))
        assert s.decision.status == "executing" and s.decision.review.outcome == "approved" and not s.decision.review.override
        await w.step(400)
        return s

    s = run(go())
    d = s.decision
    assert d.status == "completed"
    assert {e.kind for e in w.rt.engine.events} & {"pipe_burst", "cooling_fault", "substation_trip"} == set()
    worked = [x for x in d.execution if x.state == "done"]
    assert worked and all(x.confirmed_at for x in d.execution)  # every alert was cleared by the detector, not by the crew's say-so
    starts = [x for x in sorted(worked, key=lambda x: x.started_at) if x.started_at]
    assert [x.plan_start_hour for x in starts] == sorted(x.plan_start_hour for x in starts)
    log = actions(w)
    for a in ("proposed", "approve", "crew_dispatched", "repair_done", "alert_cleared", "plan_completed"):
        assert a in log
    assert log.index("proposed") < log.index("approve") < log.index("crew_dispatched") < log.index("repair_done")
    assert w.board.audit.verify().ok


def test_the_approval_records_what_the_operator_saw(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        proposed = next(e for e in w.board.audit.entries() if e.action == "proposed")
        await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona"))
        return s, proposed

    s, proposed = run(go())
    assert s.decision.review.decision_hash == proposed.data["decision_hash"]
    assert s.decision.review.expected_cost == s.decision.review.board_cost == s.decision.plan.expected_cost


def test_a_different_order_is_allowed_priced_and_logged_as_an_override(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        worst = list(reversed([x.task_id for x in s.decision.plan.steps]))
        with pytest.raises(DecisionError) as e:
            await w.board.dispatch.approve(s.id, ReviewRequest(operator="x", order=worst[:-1]))
        assert e.value.code == "bad_order" and s.decision.status == "proposed"
        await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona", order=worst, note="the hospital is on that feeder", seconds_per_hour=1))
        return s

    s = run(go())
    r = s.decision.review
    assert r.override and r.cost_delta > 0 and r.expected_cost == pytest.approx(r.board_cost + r.cost_delta, abs=0.02)
    e = next(e for e in w.board.audit.entries() if e.action == "approve_with_changes")
    assert e.actor == "Eng. Mona" and e.data["override"] is True and "hospital" in e.detail


def test_preview_prices_an_order_without_changing_anything(tmp_path, monkeypatch):
    from app.schemas.board import PreviewRequest

    async def go():
        await w.start()
        s = await w.propose()
        n = len(w.board.audit.rows)
        ids = [x.task_id for x in s.decision.plan.steps]
        same = w.board.dispatch.preview(s.id, PreviewRequest(order=ids))
        worse = w.board.dispatch.preview(s.id, PreviewRequest(order=list(reversed(ids))))
        with pytest.raises(DecisionError):
            w.board.dispatch.preview(s.id, PreviewRequest(order=ids[:1]))
        assert len(w.board.audit.rows) == n and s.decision.status == "proposed" and s.decision.execution == []
        return same, worse

    w = World(tmp_path, monkeypatch)
    same, worse = run(go())
    assert not same.override and same.cost_delta == 0 and same.expected_cost == same.board_cost
    assert worse.override and worse.cost_delta > 0 and len(worse.steps) == len(same.steps)


def test_the_same_schedule_in_another_listing_is_not_an_override(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona", order=[x.task_id for x in s.decision.plan.steps]))
        return s

    assert run(go()).decision.review.override is False


def test_a_rejection_needs_a_reason_and_closes_the_proposal(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        with pytest.raises(DecisionError) as e:
            await w.board.dispatch.reject(s.id, ReviewRequest(operator="Eng. Mona", note=" "))
        assert e.value.code == "reason_required" and s.decision.status == "proposed"
        await w.board.dispatch.reject(s.id, ReviewRequest(operator="Eng. Mona", note="Crew 2 is on leave"))
        for fn in (w.board.dispatch.approve, w.board.dispatch.reject):
            with pytest.raises(DecisionError) as e2:
                await fn(s.id, ReviewRequest(operator="Eng. Mona", note="again"))
            assert e2.value.status == 409 and e2.value.code == "already_rejected"
        await w.step(40)
        return s

    s = run(go())
    assert s.decision.status == "rejected" and s.decision.execution == []
    assert any(e.kind == "pipe_burst" for e in w.rt.engine.events)  # nothing was repaired
    assert "crew_dispatched" not in actions(w)


def test_a_newer_proposal_supersedes_an_undecided_one(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        first = await w.propose(1)
        second = await w.propose(2)
        with pytest.raises(DecisionError) as e:
            await w.board.dispatch.approve(first.id, ReviewRequest(operator="Eng. Mona"))
        assert e.value.code == "already_superseded"
        await w.board.dispatch.approve(second.id, ReviewRequest(operator="Eng. Mona"))
        return first, second

    first, second = run(go())
    assert first.decision.status == "superseded" and second.decision.status == "executing"
    assert "superseded" in actions(w)


def test_a_proposal_whose_problems_all_cleared_is_stale(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        w.rt.engine.clear_events()
        await w.step(120)
        with pytest.raises(DecisionError) as e:
            await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona"))
        assert e.value.code == "nothing_to_do"
        return s

    assert run(go()).decision.status == "stale"


def test_the_log_is_a_hash_chain_and_any_edit_is_found(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona", seconds_per_hour=1))
        await w.step(60)

    run(go())
    log = w.board.audit
    assert log.verify().ok and log.rows[0]["prev_hash"] == GENESIS and len(log.rows) >= 4
    reloaded = AuditLog(tmp_path / "audit.jsonl")  # a restart keeps the log, and it still verifies
    assert len(reloaded.rows) == len(log.rows) and reloaded.verify().ok
    i = next(k for k, r in enumerate(log.rows) if r["action"] == "approve")
    log.rows[i]["data"]["expected_cost"] = 1.0  # someone edits the approved cost afterwards
    v = log.verify()
    assert not v.ok and v.broken_at == i + 1
    log.rows[i]["data"]["expected_cost"] = reloaded.rows[i]["data"]["expected_cost"]
    del log.rows[i]  # or removes the line
    assert not log.verify().ok


def test_the_file_log_detects_an_edit_made_on_disk(tmp_path):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    for k in range(3):
        log.append(run="r", actor="a", action="x", detail=str(k), data={"v": k})
    lines = path.read_text().splitlines()
    row = json.loads(lines[1])
    row["detail"] = "edited"
    lines[1] = json.dumps(row, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    v = AuditLog(path).verify()
    assert not v.ok and v.broken_at == 2


def test_export_does_not_let_typed_text_become_a_spreadsheet_formula(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.append(run="r", actor="=cmd()", action="approve", detail="+SUM(A1)")
    csv = log.csv()
    assert "'=cmd()" in csv and "'+SUM(A1)" in csv


def test_a_reset_is_logged_and_the_log_outlives_it(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        await w.propose()

    run(go())
    n = len(w.board.audit.rows)
    run_before = w.board.run
    w.rt.reset_state()
    assert w.board.sessions == {} and len(w.board.audit.rows) == n + 1 and w.board.audit.rows[-1]["action"] == "simulation_reset"
    assert w.board.audit.verify().ok


def test_repair_ends_only_the_faults_on_that_asset():
    from app.sim.engine import Engine, Event
    e = Engine(42)
    e.add_event(Event("pipe_burst", "water-pipe-04", 0.5))
    e.add_event(Event("pipe_burst", "water-pipe-05", 0.5))
    e.add_event(Event("heat_wave", None, 44.0))
    assert e.repair("leak", "water-pipe-04") == 1
    assert [x.target for x in e.events if x.kind == "pipe_burst"] == ["water-pipe-05"] and any(x.kind == "heat_wave" for x in e.events)
    assert e.repair("leak", "water-pipe-04") == 0


def test_problems_with_crews_on_them_are_not_proposed_again_or_approved_twice(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        first = await w.propose(1)
        await w.board.dispatch.approve(first.id, ReviewRequest(operator="Eng. Mona", seconds_per_hour=1))
        assert w.board.open_tasks() == [] and len(w.board.raw_tasks()) == 3  # nothing left to decide, though the detector still sees the faults
        late = await w.propose(2)  # a meeting that ran across the approval
        with pytest.raises(DecisionError) as e:
            await w.board.dispatch.approve(late.id, ReviewRequest(operator="Eng. Mona"))
        return late, e.value

    late, err = run(go())
    assert late.decision.status == "stale" and err.code == "already_stale" and "proposal_stale" in actions(w)
    assert "proposed" == actions(w)[0] and actions(w).count("proposed") == 1


def test_a_second_plan_for_problems_already_claimed_cannot_be_approved(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        a = await w.propose(1)
        b = BoardSession(id="board-0002", state="done", started_at=datetime.now(timezone.utc), messages=[], decision=a.decision.model_copy(update={"session_id": "board-0002"}))
        w.board.sessions[b.id] = b
        w.board.analyses[b.id] = w.board.analyses[a.id]
        await w.board.dispatch.approve(a.id, ReviewRequest(operator="Eng. Mona", seconds_per_hour=1))
        with pytest.raises(DecisionError) as e:
            await w.board.dispatch.approve(b.id, ReviewRequest(operator="Eng. Mona"))
        return e.value

    assert run(go()).code == "already_dispatched"


def test_the_same_fault_under_a_new_alert_id_is_still_the_same_problem(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    async def go():
        await w.start()
        s = await w.propose()
        await w.board.dispatch.approve(s.id, ReviewRequest(operator="Eng. Mona", seconds_per_hour=1))
        # the detector re-raises the burst under a new alert id (it did, live: "Pipe burst" became "Slow leak") while the crew is on its way
        burst = next(t for t in tasks_from_alerts(w.rt, w.det, w.A) if t.kind == "leak")
        old = w.rt.alerts[burst.id]
        w.rt.alerts["alert-9999"] = old.model_copy(update={"id": "alert-9999"})
        w.rt.alerts[old.id] = old.model_copy(update={"state": "resolved"})
        o = w.det.state.open[next(k for k, v in w.det.state.open.items() if v.alert.id == burst.id)]
        o.alert = w.rt.alerts["alert-9999"]
        await w.step(400)
        return s

    s = run(go())
    leak = next(x for x in s.decision.execution if "WP-GOV" in x.asset_name)
    assert leak.state == "done" and "Cleared" not in leak.note  # the crew was sent and the fault ended, not skipped as "already cleared"
    assert not any(e.kind == "pipe_burst" for e in w.rt.engine.events)
