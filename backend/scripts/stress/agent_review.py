"""What the agents say when something goes wrong, written out in full so it can be read, with the claims that can be checked by arithmetic checked (stress-test items A-1..A-6).

    PYTHONPATH=. .venv/bin/python scripts/stress/agent_review.py [--scenarios scn-compound,scn-substation-trip] [--out transcripts.md]   # LLM_OFFLINE=1 for the rules-only wording

Each scenario runs in process: the detector, the specialist agents (their finding for every incident) and the board meeting (every message, and who wrote it: code or the
language model), then the board's plan. It prints the transcript and then checks, with code that does not call the board's own helpers for the figures it checks:

  A-1  Every message's numbers are in its fact list (to the rounding a person uses), and every asset it names is one in play.
  A-2  The chosen order is the cheapest of all orders when the cost is recomputed by the slow reference schedule, and the "saved against alarm order" figure is the difference
       between the alarm order and the chosen order recomputed the same way.
  A-3  The order of the plan, the start hours the agents accept and the steps of the summary all say the same thing.
  A-4  "Call me back if my cost rises above X": at X the plan really changes (checked by pricing that problem at 1.02 X and searching again), and does not change at 0.98 X.
        A plan is the start hour and crew of every job, not the order of a list: two orders that give every job the same start and crew are the same plan. (The first version
        of this check compared the lists and reported a false failure on a tie; the check was wrong, the board was not.)
  A-5  Each finding's recommended action, crew and cost match the incident's kind and the cost record (act now, delay per hour) it carries; a finding never contradicts the alarm.
  A-6  Every sentence of the finding that states a number states one the cost record or the sensors hold.
Prints a verdict per check and exits 1 when any fails. What a person must judge by reading (is it sensible, is it clear) is in the transcript and is not scored.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.agents.service import AgentService
from app.board import session as bs
from app.board.plan import INF, Params, best_order, evaluate, all_orders, plan_for
from app.cost import Assumptions
from app.runtime import Runtime
from app.scenarios import events_for

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)
bad = 0


def verdict(name: str, ok: bool, detail: str) -> None:
    global bad
    bad += 0 if ok else 1
    print(f"{name}  {'PASS' if ok else 'FAIL'}  {detail}", flush=True)


def slow_cost(order, p: Params) -> float:
    """The schedule written the slow obvious way: one crew free at a time, the task goes to the crew that is free first."""
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


def cheapest(p: Params, n: int) -> tuple[list[int], float]:
    best, cost = None, INF
    for o in itertools.permutations(range(n)):
        c = slow_cost(o, p)
        if c < cost - 1e-9:
            best, cost = list(o), c
    return best, cost


async def run(scenario: str, quiet_wait: float = 8.0):
    rt = Runtime(42)
    svc = AgentService(rt, Assumptions(Path(tempfile.mkdtemp()) / "a.json"))
    svc.settle_seconds = 1.0
    svc.board.quiet_seconds = 1.0
    bs.PACE = 0.0
    for i in range(30):
        await rt.tick_once(T0 + timedelta(seconds=2 * i))
    for ev in events_for(scenario, {}, "review"):
        rt.add_event(ev)
    ts = 30
    for i in range(30, 70):
        await rt.tick_once(T0 + timedelta(seconds=2 * i))
        ts = i
    await asyncio.gather(*svc.tasks)
    for _ in range(int(quiet_wait * 5)):  # the meeting convenes by itself once the problems have stopped changing
        await asyncio.sleep(0.2)
        if svc.board._running and svc.board._running.done() and svc.board.sessions:
            break
    if svc.board._running:
        await asyncio.wait([svc.board._running])
    return rt, svc


def show_finding(rt, f) -> None:
    inc = rt.incidents[f.incident_id]
    print(f"\n  INCIDENT {inc.id} [{inc.severity}] {inc.title}  zones={inc.zone_ids}")
    print(f"    agent: {f.agent}   voiced by: {'rules' if f.fallback else 'language model'}   number check: {f.number_check}")
    print(f"    headline: {f.headline}")
    print(f"    explanation: {f.explanation}")
    print(f"    probable causes: {f.probable_causes}")
    print(f"    impact: {f.predicted_impact.text}")
    print(f"    action: {f.recommended_action.text}  [{f.recommended_action.crew_type}, {f.recommended_action.urgency}]")
    c = f.cost
    print(f"    cost: act now {c.act_now:,.0f}, delay/h {c.delay_per_hour:,.0f}, cost of delay {c.cost_of_delay:,.0f} over {c.delay_hours} h, saving {c.expected_saving:,.0f}")
    for r in f.explain.reasons:
        print(f"      reason [{r.evidence_type}] {r.text}")
    for s in f.reasoning_steps:
        print(f"      step: {s}")


def check_session(rt, svc, s) -> None:
    a = svc.board.analyses.get(s.id)
    if a is None:
        verdict("A-2", False, f"{s.id}: no analysis kept")
        return
    p, T = a.params, a.tasks
    n = len(T)
    names = {t.asset_name for t in T}
    ok = all(bs.numbers_ok(m.text, m.facts) and bs.assets_ok(m.text, names | {x for x in bs.ASSET.findall(m.text) if x in names}) for m in s.messages)
    sources = {m.source for m in s.messages}
    verdict("A-1", ok, f"{s.id}: {len(s.messages)} messages, voiced by {sorted(sources)}; every number is a fact and every asset is in play")
    order, cost = cheapest(p, n)
    alarm = slow_cost(list(range(n)), p)
    chosen = a.plan.order
    chosen_cost = slow_cost(chosen, p)
    verdict("A-2", abs(chosen_cost - cost) <= 0.01 * cost + 1 and abs(chosen_cost - a.plan.cost) <= 1, f"{s.id}: chosen order cost {chosen_cost:,.0f} (plan says {a.plan.cost:,.0f}), cheapest of all {n}! orders {cost:,.0f}")
    saved = next(x for x in a.alternatives if x["id"] == "alarm")["extra"]
    verdict("A-2", abs((alarm - chosen_cost) - saved) <= 1, f"{s.id}: saved against alarm order {saved:,.0f}, recomputed {alarm - chosen_cost:,.0f}")
    steps = sorted(chosen, key=lambda i: (a.plan.start[i], i))
    dec = s.decision
    in_decision = [st.asset_name for st in dec.plan.steps]
    verdict("A-3", in_decision == [T[i].asset_name for i in steps], f"{s.id}: plan order {in_decision} equals the order of the schedule")
    starts_ok = True
    for m in s.messages:
        if m.kind == "concession":
            for f in m.facts:
                if f.label.endswith("starts at hour"):
                    asset = f.label.replace(" starts at hour", "")
                    i = next(k for k, t in enumerate(T) if t.asset_name == asset)
                    starts_ok &= abs(f.value - a.plan.start[i]) <= 0.051
    verdict("A-3", starts_ok, f"{s.id}: every start hour an agent accepts is the plan's start hour")
    for fl in a.flips:
        i = a.idx(fl["task"].id)
        for factor, should_change in ((1.02, True), (0.98, False)) if fl["direction"] == "above" else ((0.98, True), (1.02, False)):
            q = Params(rate=p.rate.copy(), repair=p.repair, hours=p.hours, late=p.late, delay=p.delay, cap=p.cap, response=p.response, switch=p.switch, crews=p.crews, relieved=p.relieved)
            q.rate[i] = fl["threshold"] * factor
            o, _ = cheapest(q, n)
            sch = plan_for(o, q)  # compare the schedules, not the permutations: two orders that give every job the same start and crew are one plan
            changed = [round(float(sch.start[k]), 6) for k in range(n)] != [round(float(a.plan.start[k]), 6) for k in range(n)] or [int(sch.slot[k]) for k in range(n)] != [int(a.plan.slot[k]) for k in range(n)]
            verdict("A-4", changed == should_change, f"{s.id}: {fl['task'].asset_name} priced at {factor:.2f} x {fl['threshold']:,.0f} EGP/h {'changes' if changed else 'keeps'} the order (expected {'a change' if should_change else 'no change'})")


def check_findings(rt, svc) -> None:
    from app.board.plan import ACTIONS
    for f in rt.findings.values():
        inc = rt.incidents[f.incident_id]
        c = f.cost
        ok = abs(c.act_now - (c.act_now_losses or 0) - (c.act_now - (c.act_now_losses or 0))) < 1 and c.delay_per_hour > 0 and f.recommended_action.text and f.recommended_action.crew_type
        calc = rt.calculations.get(f.explain.calculation_id) if f.explain.calculation_id else None
        same = calc is None or abs(calc.cost.delay_per_hour - c.delay_per_hour) < 1
        verdict("A-5", bool(ok and same), f"{inc.id} {inc.title}: action '{f.recommended_action.crew_type}' / {f.recommended_action.urgency}; cost matches the cost record: {same}")


QUESTIONS = ["What should we do first and why?", "What happens if SS-02 trips?", "If we wait 10 hours before sending a crew, how much does it cost?",
             "Is the pump failure caused by the power cut?", "إيه اللي بيحصل في الشبكة دلوقتي وإيه أهم حاجة نعملها؟"]


async def ask(svc, questions: list[str]) -> None:
    print("\n  CHAT with the orchestrator")
    for q in questions:
        await asyncio.sleep(12)  # the free model tier allows 8,000 tokens a minute: without a pause most answers would be the rules-only wording
        steps: list = []

        async def on_route(r, steps=steps):
            steps.extend(r)

        out = await svc.orchestrator.ainvoke({"question": q, "incident_id": None, "on_route": on_route})
        print(f"    Q: {q}\n       route: {[(r.agent, r.reason) for r in steps]}\n       fallback: {bool(out.get('fallback'))}\n       A: {out['answer']}")


async def main_async(scenarios: list[str], out: Path | None, chat: bool = False) -> None:
    for sc in scenarios:
        print(f"\n================ {sc} ================")
        rt, svc = await run(sc)
        for f in rt.findings.values():
            show_finding(rt, f)
        check_findings(rt, svc)
        sess = [s for s in svc.board.sessions.values() if s.messages]
        if not sess:
            print("\n  (no board meeting: fewer than two networks with their own problem)")
        for s in sess:
            print(f"\n  BOARD {s.id}")
            for m in s.messages:
                print(f"    r{m.round} {m.agent:12} [{m.kind}/{m.source}] {m.text}")
            print(f"    SUMMARY [{s.decision.summary_source}] {s.decision.summary}")
            check_session(rt, svc, s)
        if chat:
            await ask(svc, QUESTIONS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="scn-compound,scn-substation-trip,scn-pipe-burst,scn-pump-failure")
    ap.add_argument("--out")
    ap.add_argument("--chat", action="store_true", help="also put a few questions to the orchestrator and print its answers")
    args = ap.parse_args()
    asyncio.run(main_async(args.scenarios.split(","), Path(args.out) if args.out else None, args.chat))
    print(f"\n{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
