"""Everything the board knows before anyone speaks: the plan, what each alternative costs, what would change it, the risks.

`analyse` is pure given the tasks and the assumptions. The meeting in `session.py` only voices what is computed here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from app.board.plan import (
    MAX_TASKS, Params, Plan, Robustness, Task, all_orders, best_order, evaluate, flips, params_for, plan_for, robustness, smith_order,
)
from app.cost import Assumptions
from app.schemas import CalcInput, Calculation, CalculationRecord, Cost, CostItem, Explanation, Reason
from app.schemas.board import (
    BoardAlternative, BoardDecision, BoardFlip, BoardPlan, BoardRisk, BoardStep, BoardTask,
)

AGENTS = ("water", "electricity", "cooling")
NAMES = {"water": "Water", "electricity": "Electricity", "cooling": "Cooling"}


def money(v: float) -> str:
    return f"{round(v):,}"


def hours(v: float) -> str:
    return f"{v:.1f}"


@dataclass
class Analysis:
    session_id: str
    tasks: list[Task]  # scheduled by the board, in alarm order
    omitted: int  # open problems beyond what the board schedules
    params: Params
    plan: Plan
    task_cost: np.ndarray  # chosen plan, EGP per task
    robust: Robustness
    alternatives: list[dict]
    flips: list[dict]
    risks: list[dict]
    extra_crew_saving: float | None
    conflict: bool
    own_first: dict  # agent -> Plan
    waiting_price: dict  # agent -> EGP more than if it went first
    others_pay: dict  # agent -> EGP the others pay if this agent goes first
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def idx(self, task_id: str) -> int:
        return next(i for i, t in enumerate(self.tasks) if t.id == task_id)

    def agent_tasks(self, agent: str) -> list[int]:
        return [i for i, t in enumerate(self.tasks) if t.agent == agent]

    def primary(self, agent: str) -> int | None:
        mine = self.agent_tasks(agent)
        return smith_order(self.tasks, mine)[0] if mine else None


def task_costs(plan: Plan, p: Params) -> np.ndarray:
    loss = p.rate * plan.end
    repair = np.where(plan.dropped, 0.0, p.repair * plan.mult)
    return loss + repair


def order_names(tasks: list[Task], order: list[int]) -> list[str]:
    return [tasks[i].id for i in order]


def analyse(all_tasks: list[Task], A: Assumptions, session_id: str, overload: list[tuple[str, float]] | None = None) -> Analysis:
    ranked = sorted(all_tasks, key=lambda t: -t.rate)[:MAX_TASKS]
    omitted = len(all_tasks) - len(ranked)
    tasks = sorted(ranked, key=lambda t: (t.detected_at, t.id))
    n = len(tasks)
    p = params_for(tasks, A)
    perms = all_orders(n)
    order, _ = best_order(p, n, perms)
    chosen = plan_for(order, p)
    costs = evaluate(perms, p)
    tc = task_costs(chosen, p)
    rob = robustness(tasks, A, order, session_id)

    alarm = list(range(n))
    alts = [("alarm", "Work in the order the alarms arrived", alarm)]
    own_first, waiting_price, others_pay = {}, {}, {}
    for a in AGENTS:
        mine = [i for i, t in enumerate(tasks) if t.agent == a]
        if not mine:
            continue
        rest = [i for i in order if i not in mine]
        first = smith_order(tasks, mine) + rest
        own_first[a] = plan_for(first, p)
        mine_chosen, mine_first = tc[mine].sum(), task_costs(own_first[a], p)[mine].sum()
        waiting_price[a] = float(mine_chosen - mine_first)
        others_pay[a] = float(sum(task_costs(own_first[a], p)[i] - tc[i] for i in range(n) if i not in mine))
        alts.append((f"{a}_first", f"{NAMES[a]} agent's work first", first))
    worst = [int(x) for x in perms[int(np.argmax(costs))]]
    alts.append(("worst", "The most expensive order", worst))
    alternatives = []
    for aid, name, o in alts:
        c = plan_for(o, p).cost
        alternatives.append(dict(id=aid, name=name, order=order_names(tasks, o), cost=c, extra=c - chosen.cost))

    fl = flips(tasks, p, chosen, perms)[:3]

    conflict = bool(np.any(chosen.start > p.response + 1e-9) or chosen.dropped.any())
    saving = None
    if p.crews < n and n > 1:
        more = dataclasses.replace(p, crews=p.crews + 1)
        _, c2 = best_order(more, n, perms)
        saving = chosen.cost - c2 if chosen.cost - c2 > 0.5 else None

    risks: list[dict] = []
    for name, pct in (overload or [])[:2]:
        risks.append(dict(kind="overload", text=f"{name} is running at {round(pct)}% of its normal maximum, so another trip is possible while crews are busy."))
    for i in order:
        t = tasks[i]
        if chosen.start[i] > p.response + 1e-9 and not chosen.dropped[i]:
            extra = p.rate[i] * (chosen.start[i] - p.response) + p.repair[i] * (chosen.mult[i] - 1)
            risks.append(dict(kind="queue", text=f"{t.asset_name} waits {hours(chosen.start[i])} h for a crew, which costs {money(extra)} EGP more than starting at once."))
    if omitted:
        risks.append(dict(kind="queue", text=f"{omitted} further open problem{'s' if omitted > 1 else ''} with a smaller hourly cost {'are' if omitted > 1 else 'is'} not scheduled by this board."))
    risks.append(dict(kind="tail", text=f"In one price scenario in ten the plan costs {money(rob.p90)} EGP or more."))

    return Analysis(session_id=session_id, tasks=tasks, omitted=omitted, params=p, plan=chosen, task_cost=tc, robust=rob, alternatives=alternatives,
                    flips=fl, risks=risks, extra_crew_saving=saving, conflict=conflict, own_first=own_first, waiting_price=waiting_price, others_pay=others_pay)


# ---------- schema objects ----------

def board_tasks(a: Analysis) -> list[BoardTask]:
    return [BoardTask(id=t.id, agent=t.agent, asset_id=t.asset_id, asset_name=t.asset_name, kind=t.kind, action=t.action, crew=t.crew,
                      hours_to_fix=float(a.params.hours[i]), hourly_cost=round(float(a.params.rate[i]), 2), repair_cost=round(float(a.params.repair[i]), 2),
                      return_per_crew_hour=round(float(a.params.rate[i] / a.params.hours[i]), 2), relieved_by=list(t.relieved_by))
            for i, t in enumerate(a.tasks)]


def board_steps(a: Analysis) -> list[BoardStep]:
    steps = []
    for k, i in enumerate(a.plan.order):
        t = a.tasks[i]
        steps.append(BoardStep(order=k + 1, task_id=t.id, agent=t.agent, asset_name=t.asset_name, action=t.action, crew_slot=int(a.plan.slot[i]) + 1,
                               start_hour=round(float(a.plan.start[i]), 2), end_hour=round(float(a.plan.end[i]), 2), hourly_cost=round(float(a.params.rate[i]), 2),
                               loss_until_fixed=round(float(a.params.rate[i] * a.plan.end[i]), 2),
                               repair_cost=round(float(0 if a.plan.dropped[i] else a.params.repair[i] * a.plan.mult[i]), 2),
                               repair_multiplier=round(float(a.plan.mult[i]), 3), dropped=bool(a.plan.dropped[i]),
                               relief_hour=round(float(a.plan.end[i]), 2) if a.plan.dropped[i] else None))
    steps.sort(key=lambda s: (s.start_hour, s.order))
    for k, s in enumerate(steps):
        s.order = k + 1
    return steps


def decision_object(a: Analysis, summary: str, summary_source: str, calculation_id: str | None) -> BoardDecision:
    incident_ids = list(dict.fromkeys(t.incident_id for t in a.tasks))
    return BoardDecision(
        session_id=a.session_id, incident_ids=incident_ids, created_at=a.created_at, crews=a.params.crews, tasks=board_tasks(a),
        plan=BoardPlan(steps=board_steps(a), expected_cost=round(a.plan.cost, 2), p10=round(a.robust.p10, 2), p50=round(a.robust.p50, 2), p90=round(a.robust.p90, 2),
                       win_share=round(a.robust.win_share, 4), regret_mean=round(a.robust.regret_mean, 2), regret_p90=round(a.robust.regret_p90, 2),
                       hourly_bleed=round(float(a.params.rate.sum()), 2), runs=a.robust.runs),
        alternatives=[BoardAlternative(id=x["id"], name=x["name"], order=x["order"], expected_cost=round(x["cost"], 2), extra_cost=round(x["extra"], 2)) for x in a.alternatives],
        flips=[BoardFlip(task_id=f["task"].id, asset_name=f["task"].asset_name, current=round(f["current"], 2), threshold=round(f["threshold"], 2), direction=f["direction"]) for f in a.flips],
        risks=[BoardRisk(**r) for r in a.risks], extra_crew_saving=None if a.extra_crew_saving is None else round(a.extra_crew_saving, 2),
        conflict=a.conflict, summary=summary, summary_source=summary_source, calculation_id=calculation_id)


# ---------- provenance: the same decision as a calculation record ----------

def _a(name, value, unit, source="rule", key=None, basis=""):
    return CalcInput(name=name, value=float(value), unit=unit, source=source, key=key, basis=basis)


def calculation_record(a: Analysis, A: Assumptions, calc_id: str) -> CalculationRecord:
    p, tasks = a.params, a.tasks
    calcs: list[Calculation] = []
    breakdown: list[CostItem] = []
    for i, t in enumerate(tasks):
        k = a.plan.order.index(i)
        calcs.append(Calculation(name=f"Hourly cost, {t.asset_name}", formula="C_loss + C_disruption + C_secondary, from the cost model",
                                 inputs=[_a("C_delay", p.rate[i], "EGP/h", "rule", basis="Cost model record built from the facts the detector measured on this alert.")],
                                 result=round(float(p.rate[i]), 2), unit="EGP/h"))
        calcs.append(Calculation(name=f"Return per crew hour, {t.asset_name}", formula="C_delay / fix_hours",
                                 inputs=[_a("C_delay", p.rate[i], "EGP/h"), _a("fix_hours", p.hours[i], "h", "assumed", f"fix_hours_{t.policy}")],
                                 result=round(float(p.rate[i] / p.hours[i]), 2), unit="EGP/h"))
        calcs.append(Calculation(name=f"Finished at hour, {t.asset_name}",
                                 formula="start + fix_hours, where start is the first free crew (response_hours for the first jobs, then crew_switch_hours after each job)",
                                 inputs=[_a("start", a.plan.start[i], "h"), _a("fix_hours", p.hours[i], "h", "assumed", f"fix_hours_{t.policy}"),
                                         _a("response_hours", p.response, "h", "assumed", "response_hours"), _a("crew_switch_hours", p.switch, "h", "assumed", "crew_switch_hours"),
                                         _a("crews_available", p.crews, "crews", "assumed", "crews_available")],
                                 result=round(float(a.plan.end[i]), 2), unit="h"))
        calcs.append(Calculation(name=f"Loss until fixed, {t.asset_name}", formula="C_delay × finished_at_hour",
                                 inputs=[_a("C_delay", p.rate[i], "EGP/h"), _a("finished_at", a.plan.end[i], "h")], result=round(float(p.rate[i] * a.plan.end[i]), 2)))
        wait = max(0.0, float(a.plan.start[i]) - p.response)
        calcs.append(Calculation(name=f"Repair cost, {t.asset_name}", formula="repair_now × min(cap, 1 + (late − 1) × wait / delay_hours)",
                                 inputs=[_a("repair_now", p.repair[i], "EGP"), _a("wait", wait, "h"), _a("late", p.late[i], "×", "assumed", f"late_multiplier_{t.policy}"),
                                         _a("delay_hours", p.delay[i], "h", "assumed", f"delay_hours_{t.policy}"), _a("cap", p.cap, "×", "assumed", "late_multiplier_cap")],
                                 result=round(float(0 if a.plan.dropped[i] else p.repair[i] * a.plan.mult[i]), 2)))
        breakdown.append(CostItem(label=f"{t.asset_name}: loss until fixed", group=f"loss:{t.id}", amount=round(float(p.rate[i] * a.plan.end[i]), 2),
                                  cause=f"Step {k + 1} of the chosen order"))
        breakdown.append(CostItem(label=f"{t.asset_name}: repair", group=f"repair:{t.id}", amount=round(float(0 if a.plan.dropped[i] else p.repair[i] * a.plan.mult[i]), 2),
                                  cause="Repair, with the late-repair multiplier for the time it waits"))
    calcs.append(Calculation(name="Total cost of the chosen order", formula="sum of loss until fixed and repair over all problems",
                             inputs=[_a("total", a.plan.cost, "EGP")], result=round(a.plan.cost, 2)))
    alarm = next(x for x in a.alternatives if x["id"] == "alarm")
    calcs.append(Calculation(name="Total cost working in alarm order", formula="the same formula, orders as the alarms arrived",
                             inputs=[_a("total", alarm["cost"], "EGP")], result=round(alarm["cost"], 2)))
    keys = ["crews_available", "crew_switch_hours", "response_hours", "late_multiplier_cap"] + [f"fix_hours_{t.policy}" for t in tasks] \
        + [f"late_multiplier_{t.policy}" for t in tasks] + [f"delay_hours_{t.policy}" for t in tasks]
    reasons = [Reason(text=f"{len(tasks)} open problems, {p.crews} crews, {money(float(p.rate.sum()))} EGP per hour while they stay open", evidence_type="measured", evidence_ref=tasks[0].id),
               Reason(text="Every order was tried, and the cheapest was chosen", evidence_type="rule", evidence_ref="rule:exhaustive-search"),
               Reason(text=f"Cheapest in {round(a.robust.win_share * 100)}% of {a.robust.runs} price scenarios", evidence_type="rule", evidence_ref="rule:monte-carlo")]
    explanation = Explanation(reasons=reasons, calculations=calcs, assumptions=[A.item(k) for k in dict.fromkeys(keys)], sensitivity=[], confidence=min(0.99, 0.5 + a.robust.win_share / 2),
                              calculation_id=calc_id)
    cost = Cost(act_now=round(a.plan.cost, 2), delay_per_hour=round(float(p.rate.sum()), 2), expected_saving=round(alarm["cost"] - a.plan.cost, 2),
                response_hours=p.response, cost_of_delay=round(alarm["cost"], 2), breakdown=breakdown, explanation=explanation)
    return CalculationRecord(id=calc_id, incident_id=tasks[0].incident_id, created_at=a.created_at, cost=cost, explanation=explanation)
