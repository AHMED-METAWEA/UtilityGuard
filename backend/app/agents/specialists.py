"""Specialist agents (Water, Electricity, Cooling) as one LangGraph sub-graph per domain.

Graph: gather (tools) -> cost (deterministic) -> reason (LLM, text only) -> check (numbers) -> finalize.
Each node appends a real, logged step; those steps are what the UI shows as "thinking".
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.checker import allowed_numbers, check
from app.agents.llm import MODEL_FAST, llm
from app.agents.tools import PLAYBOOKS, impacted, read_sensors
from app.cost import Assumptions, IncidentFacts, compute
from app.schemas import AgentFinding, CalculationRecord, Incident, PredictedImpact, RecommendedAction

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger("utilityguard.agents")

DOMAIN = {
    "water": "the water network: leaks, pressure, pumps, water loss",
    "electricity": "the electricity network: substations, feeders, transformers, outages and overloads",
    "cooling": "district cooling: chillers, supply temperature, chilled water loops, cooling energy",
}

SYSTEM = """You are the {agent} agent of UtilityGuard, a digital twin of the New Administrative Capital.
You are an expert only in {domain}, and you treat it as the most important thing in the system.
You receive measured facts and a cost calculation computed by code.
Rules:
- Use ONLY numbers that appear in the data, quoted exactly. Never invent or estimate a number.
- If something is not in the data, say it is not available.
- Plain language for a business owner. Short.
Return JSON: {{"headline": str (max 12 words), "explanation": str (2 sentences), "probableCauses": [str, str],
"impact": str (1 sentence), "action": str (1 imperative sentence)}}"""


class SState(TypedDict, total=False):
    agent: str
    incident: Incident
    facts: IncidentFacts
    root: str
    steps: list[str]
    sensors: list[dict]
    impact: dict
    record: CalculationRecord
    text: dict
    fallback: bool
    removed: list[str]
    t0: float
    finding: Any


def _step(s: SState, msg: str) -> list[str]:
    log.info("[%s] %s: %s", s["agent"], s["incident"].id, msg)
    return [*s.get("steps", []), msg]


def build_specialist(rt: "Runtime", A: Assumptions):
    g = rt.engine.g

    def gather(s: SState) -> SState:
        inc = s["incident"]
        assets = [a for aid in inc.alert_ids for a in rt.alerts[aid].asset_ids] or [s["root"]]
        sensors = read_sensors(rt, list(dict.fromkeys(assets)))
        imp = impacted(rt, s["root"])
        steps = _step(s, f"Read {len(sensors)} sensors on {', '.join(g.asset_index[a].name for a in list(dict.fromkeys(assets))[:3])}")
        steps = _step({**s, "steps": steps}, f"Checked dependencies: {len(imp['zones'])} zone(s), {len(imp['pumps'])} pump(s), {len(imp['plants'])} plant(s) linked")
        return {"sensors": sensors, "impact": imp, "steps": steps}

    def cost(s: SState) -> SState:
        rec = compute(s["facts"], A, s["incident"].id)
        rt.calculations[rec.id] = rec
        steps = _step(s, f"Ran cost model {rec.id}: act now vs delay of {rec.cost.delay_hours:g} h")
        return {"record": rec, "steps": steps}

    async def reason(s: SState) -> SState:
        rec, f = s["record"], s["facts"]
        crew, urgency, action = PLAYBOOKS[f.kind]
        data = {
            "incident": s["incident"].title, "kind": f.kind, "reasons": [r.text for r in f.reasons],
            "sensors": s["sensors"][:8], "dependencies": s["impact"],
            "cost": {"actNowEGP": rec.cost.act_now, "costOfDelayEGP": rec.cost.cost_of_delay,
                     "delayHours": rec.cost.delay_hours, "savingEGP": rec.cost.expected_saving,
                     "delayPerHourEGP": rec.cost.delay_per_hour},
            "playbook": {"crew": crew, "action": action},
        }
        key = llm.key("finding", s["agent"], f.kind, s["root"])
        out, fb = await llm.json(key, SYSTEM.format(agent=s["agent"], domain=DOMAIN[s["agent"]]),
                                 json.dumps(data, default=str), model=MODEL_FAST)
        if not out:
            out = template(s)
        steps = _step(s, f"Looked up {crew} playbook and wrote recommendation ({'recorded fallback' if fb else 'live LLM'})")
        return {"text": out, "fallback": fb, "steps": steps}

    def verify(s: SState) -> SState:
        allowed = allowed_numbers(s["record"], [x for sn in s["sensors"] for x in (sn["now"], sn["min30"] or 0, sn["max30"] or 0)])
        cleaned, removed = _clean(s["text"], allowed)
        steps, fallback = s.get("steps", []), s.get("fallback", False)
        if removed and fallback:
            # a recorded answer quotes figures from an older state: use the computed wording instead of leaving holes
            cleaned, removed = _clean(template(s), allowed)
            steps = _step(s, "Recorded wording was out of date, so the computed wording is used")
        msg = "Number check passed: every figure matches the calculation" if not removed else f"Number check removed {len(removed)} unverified figure(s)"
        return {"text": cleaned, "removed": removed, "steps": _step({**s, "steps": steps}, msg)}

    def finalize(s: SState) -> SState:
        f, rec, t = s["facts"], s["record"], s["text"]
        crew, urgency, action = PLAYBOOKS[f.kind]
        elapsed = time.time() - s["t0"]
        finding = AgentFinding(
            id=f"find-{len(rt.findings) + 1:04d}", incident_id=s["incident"].id, agent=s["agent"],
            headline=t.get("headline") or s["incident"].title, explanation=t.get("explanation", ""),
            probable_causes=t.get("probableCauses") or ["Not available"],
            predicted_impact=PredictedImpact(text=t.get("impact", ""), zone_ids=list(f.zone_types)),
            recommended_action=RecommendedAction(text=t.get("action") or action, crew_type=crew, urgency=urgency),
            cost=rec.cost, reasoning_steps=[*s["steps"], f"Finding ready in {elapsed:.1f} s"], explain=rec.explanation,
            created_at=datetime.now(timezone.utc), fallback=s.get("fallback", False),
            number_check="corrected" if s.get("removed") else "passed")
        return {"finding": finding}

    sg = StateGraph(SState)
    for name, fn in [("gather", gather), ("costing", cost), ("reason", reason), ("verify", verify), ("finalize", finalize)]:
        sg.add_node(name, fn)
    sg.set_entry_point("gather")
    for a, b in [("gather", "costing"), ("costing", "reason"), ("reason", "verify"), ("verify", "finalize")]:
        sg.add_edge(a, b)
    sg.add_edge("finalize", END)
    return sg.compile()


def _clean(text: dict, allowed: set) -> tuple[dict, list]:
    cleaned: dict[str, Any] = {}
    removed: list[str] = []
    for k, v in text.items():
        if isinstance(v, str):
            cleaned[k], r = check(v, allowed)
            removed += r
        elif isinstance(v, list):
            cleaned[k] = []
            for x in v:
                t, r = check(str(x), allowed)
                cleaned[k].append(t)
                removed += r
        else:
            cleaned[k] = v
    return cleaned, removed


def template(s: SState) -> dict:
    """Deterministic wording used when the LLM and the cache are both unavailable."""
    f, rec = s["facts"], s["record"]
    crew, _, action = PLAYBOOKS[f.kind]
    first = f.reasons[0].text if f.reasons else s["incident"].title
    return {
        "headline": s["incident"].title,
        "explanation": f"{first}. Acting now costs {rec.cost.act_now:,.0f} EGP; waiting {rec.cost.delay_hours:g} h costs "
                       f"{rec.cost.cost_of_delay:,.0f} EGP.",
        "probableCauses": [r.text for r in f.reasons[:2]],
        "impact": f"Affects {', '.join(f.zone_types)}.",
        "action": action,
    }


async def run_specialist(graph, agent: str, incident: Incident, facts: IncidentFacts, root: str) -> AgentFinding:
    out = await graph.ainvoke({"agent": agent, "incident": incident, "facts": facts, "root": root, "steps": [], "t0": time.time()})
    return out["finding"]
