"""From a proposal to action: the human approves (or rejects), then crews are sent and the twin shows the result.

Nothing here runs without a person's approval. Approval is of the whole plan, in the board's order or in an order the person
chose; the cost of any other order is computed before it is accepted, and the difference is written to the log.

Crew work is shown on a demo clock: one planned hour of work takes `seconds_per_hour` seconds of the twin's time, because a
real three hour repair cannot be watched. The plan itself, its hours and its costs are not scaled.

When a crew finishes, the fault on that asset ends in the twin (`Engine.repair`). The detector is then the one that decides the
problem is gone: `confirmed_at` is set when it clears the alert, not when the crew says it is done.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from datetime import datetime, timezone

from app.board.audit import decision_hash
from app.board.analysis import Analysis, board_steps
from app.board.plan import plan_for, signature
from app.schemas.board import BoardDecision, BoardStep, ExecutionStep, OrderPreview, PreviewRequest, Review, ReviewRequest
from app.sim.engine import TICK_SECONDS

log = logging.getLogger("utilityguard.dispatch")

DEFAULT_SECONDS_PER_HOUR = float(os.getenv("DISPATCH_SECONDS_PER_HOUR", "20"))
CONFIRM_WINDOW_TICKS = 300  # how long after the work the detector is given to clear the alert before we stop waiting


class DecisionError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclasses.dataclass
class _Run:
    session_id: str
    analysis: Analysis
    seconds_per_hour: float
    tick0: int
    dropped: set[str] = dataclasses.field(default_factory=set)  # tasks another repair relieves: no crew is sent
    finished_tick: int | None = None


class Dispatcher:
    def __init__(self, board):
        self.board = board
        self.rt = board.rt
        self.runs: dict[str, _Run] = {}
        self.rt.tick_hooks.append(self.tick)
        self.rt.reset_hooks.append(self.runs.clear)

    # ---------- the human's decision ----------
    def _proposal(self, session_id: str) -> tuple[BoardDecision, Analysis]:
        s = self.board.sessions.get(session_id)
        if not s:
            raise DecisionError(404, "no_session", f"No board session {session_id}.")
        d = s.decision
        if d is None:
            raise DecisionError(409, "no_decision", "The board has not reached a decision yet.")
        if d.status != "proposed":
            raise DecisionError(409, f"already_{d.status}", f"This decision is already {d.status}.")
        a = self.board.analyses.get(session_id)
        if a is None:
            raise DecisionError(409, "no_analysis", "The numbers behind this proposal are no longer in memory; ask the board to meet again.")
        return d, a

    @staticmethod
    def key(t) -> tuple[str, str]:
        """A problem is the same problem when it is the same kind of fault on the same asset, whatever alert id the detector gives it."""
        return (t.kind, t.asset_id)

    def claimed(self) -> set[tuple[str, str]]:
        """Problems an approved plan has sent crews to and the detector has not yet confirmed clear."""
        return {self.key(t) for run in self.runs.values() for t in run.analysis.tasks}

    def _open(self) -> set[tuple[str, str]]:
        return {self.key(t) for t in self.board.raw_tasks()}

    def _order(self, d: BoardDecision, a: Analysis, order: list[str] | None):
        """The plan for an order the person chose (or the board's own), with the steps to show and whether it is a different schedule."""
        if order is None:
            return a.plan, d.plan.steps, False
        ids = [t.id for t in a.tasks]
        if sorted(order) != sorted(ids):
            raise DecisionError(422, "bad_order", "The order must list every task of the proposal exactly once.")
        plan = plan_for([a.idx(i) for i in order], a.params)
        override = signature(plan) != signature(a.plan)
        return plan, (board_steps(dataclasses.replace(a, plan=plan)) if override else d.plan.steps), override

    def preview(self, session_id: str, req: PreviewRequest) -> OrderPreview:
        d, a = self._proposal(session_id)
        plan, steps, override = self._order(d, a, req.order)
        return OrderPreview(order=[s.task_id for s in steps], steps=steps, expected_cost=round(float(plan.cost), 2), board_cost=round(float(a.plan.cost), 2),
                            cost_delta=round(float(plan.cost - a.plan.cost), 2), override=override)

    async def approve(self, session_id: str, req: ReviewRequest) -> BoardDecision:
        d, a = self._proposal(session_id)
        now_open = self._open()
        if self.claimed() & {self.key(t) for t in a.tasks}:
            raise DecisionError(409, "already_dispatched", "Crews are already working on some of these problems under another approved plan.")
        covered = [t.id for t in a.tasks if self.key(t) in now_open]
        if not covered:
            d.status = "stale"
            self.board.log(actor="system", action="proposal_stale", session_id=session_id, detail="Every problem in the proposal had already cleared when it was approved.")
            await self.rt.broadcast("board_decision", d)
            raise DecisionError(409, "nothing_to_do", "Every problem in this proposal has already cleared.")
        uncovered = len(now_open - {self.key(t) for t in a.tasks} - self.claimed())
        plan, steps, override = self._order(d, a, req.order)
        sph = req.seconds_per_hour or DEFAULT_SECONDS_PER_HOUR
        now = datetime.now(timezone.utc)
        cost_delta = float(plan.cost - a.plan.cost)
        d.review = Review(outcome="approved", operator=req.operator.strip(), at=now, note=req.note.strip(), override=override, order=[s.task_id for s in steps],
                          expected_cost=round(float(plan.cost), 2), board_cost=round(float(a.plan.cost), 2), cost_delta=round(cost_delta, 2),
                          decision_hash=decision_hash(d.dump()), seconds_per_hour=sph)
        by_id = {t.id: t for t in a.tasks}
        d.execution = [ExecutionStep(task_id=s.task_id, asset_id=by_id[s.task_id].asset_id, asset_name=s.asset_name, action=s.action, crew_slot=s.crew_slot,
                                     plan_start_hour=s.start_hour, plan_end_hour=s.end_hour, state="waiting",
                                     note="No crew needed: the repair of another asset relieves it." if s.dropped else "")
                       for s in steps]
        dropped = {s.task_id for s in steps if s.dropped}
        d.status = "executing"
        self.runs[session_id] = _Run(session_id, dataclasses.replace(a, plan=plan), sph, self.rt.engine.tick, dropped)
        self.board.log(actor=d.review.operator, action="approve_with_changes" if override else "approve", session_id=session_id, subject=d.calculation_id,
                       detail=(f"Approved a different order from the board's; it costs {abs(cost_delta):,.0f} EGP {'more' if cost_delta >= 0 else 'less'} in expectation."
                               if override else "Approved the board's plan as proposed.") + (f" Note: {d.review.note}" if d.review.note else ""),
                       data={"expected_cost": d.review.expected_cost, "board_cost": d.review.board_cost, "cost_delta": d.review.cost_delta, "override": override,
                             "decision_hash": d.review.decision_hash, "tasks": len(steps), "not_in_proposal": uncovered, "seconds_per_hour": sph,
                             "order": " > ".join(d.review.order)})
        await self.rt.broadcast("board_decision", d)
        return d

    async def reject(self, session_id: str, req: ReviewRequest) -> BoardDecision:
        d, a = self._proposal(session_id)
        reason = req.note.strip()
        if len(reason) < 3:
            raise DecisionError(422, "reason_required", "Say why the proposal is rejected.")
        d.review = Review(outcome="rejected", operator=req.operator.strip(), at=datetime.now(timezone.utc), note=reason, decision_hash=decision_hash(d.dump()))
        d.status = "rejected"
        self.board.log(actor=d.review.operator, action="reject", session_id=session_id, subject=d.calculation_id, detail=f"Rejected the proposal. Reason: {reason}",
                       data={"expected_cost": round(float(a.plan.cost), 2), "decision_hash": d.review.decision_hash})
        await self.rt.broadcast("board_decision", d)
        return d

    # ---------- the crews ----------
    async def tick(self, rt) -> None:
        for sid, run in list(self.runs.items()):
            d = self.board.sessions[sid].decision
            if d is None or d.status not in ("executing", "completed"):
                self.runs.pop(sid, None)
                continue
            changed = await self._advance(rt, run, d)
            if changed:
                await rt.broadcast("board_decision", d)
            if run.finished_tick is not None and (all(x.confirmed_at for x in d.execution) or rt.engine.tick - run.finished_tick > CONFIRM_WINDOW_TICKS):
                self.runs.pop(sid, None)

    async def _advance(self, rt, run: _Run, d: BoardDecision) -> bool:
        a, changed = run.analysis, False
        elapsed = (rt.engine.tick - run.tick0) * TICK_SECONDS / run.seconds_per_hour
        now = datetime.now(timezone.utc)
        dropped = run.dropped
        open_ = self._open()
        tasks = {t.id: t for t in a.tasks}
        for x in d.execution:
            t = tasks[x.task_id]
            if x.state == "waiting" and x.task_id in dropped:
                if elapsed >= x.plan_end_hour:
                    x.state, x.finished_at, changed = "skipped", now, True
                    self.board.log(actor="dispatch", action="no_crew_needed", session_id=run.session_id, subject=x.task_id,
                                   detail=f"{x.asset_name}: no crew sent, the repair of another asset relieves it.", data={"plan_hour": x.plan_end_hour})
            elif x.state == "waiting" and elapsed >= x.plan_start_hour:
                if self.key(t) not in open_:
                    x.state, x.finished_at, x.note, changed = "skipped", now, "Cleared before the crew arrived.", True
                    self.board.log(actor="dispatch", action="skipped", session_id=run.session_id, subject=x.task_id,
                                   detail=f"{x.asset_name}: the problem had already cleared, no crew sent.")
                else:
                    x.state, x.started_at, changed = "working", now, True
                    self.board.log(actor="dispatch", action="crew_dispatched", session_id=run.session_id, subject=x.task_id,
                                   detail=f"Crew {x.crew_slot} started on {x.asset_name}: {x.action}.", data={"crew": x.crew_slot, "plan_hour": x.plan_start_hour})
            elif x.state == "working" and elapsed >= x.plan_end_hour:
                if self.key(t) not in open_:
                    x.state, x.finished_at, x.note, changed = "done", now, "Already clear when the crew finished.", True
                    removed = 0
                else:
                    removed = rt.engine.repair(t.kind, t.asset_id)
                    x.state, x.finished_at, changed = "done", now, True
                    if not removed:
                        x.note = "The crew finished but no active fault was left to clear."
                self.board.log(actor="dispatch", action="repair_done", session_id=run.session_id, subject=x.task_id,
                               detail=f"Crew {x.crew_slot} finished {x.asset_name}. " + ("The twin now shows the fault cleared." if removed else x.note),
                               data={"faults_cleared": removed, "plan_hour": x.plan_end_hour})
            if x.state in ("done", "skipped") and x.confirmed_at is None and x.finished_at:
                if self.key(t) not in open_:
                    x.confirmed_at, changed = now, True
                    self.board.log(actor="detector", action="alert_cleared", session_id=run.session_id, subject=x.task_id,
                                   detail=f"The detector confirms {x.asset_name} is back to normal.",
                                   data={"seconds_after_work": round((now - x.finished_at).total_seconds(), 1)})
        if d.status == "executing" and all(x.state in ("done", "skipped") for x in d.execution):
            d.status, run.finished_tick, changed = "completed", rt.engine.tick, True
            self.board.log(actor="dispatch", action="plan_completed", session_id=run.session_id, subject=d.calculation_id,
                           detail="All crews have finished.", data={"steps": len(d.execution)})
        return changed
