"""Orchestrator: the supervisor graph the business owner talks to (spec 8.2).

Graph: route -> (what_if) -> consult specialists in order -> synthesize -> check.
Routing is decided by the LLM, with a rule-based router as fallback; the routing
reasons are streamed to the UI. All figures come from computed records.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.checker import allowed_numbers, check
from app.agents.llm import MODEL_FAST, MODEL_ORCH, llm
from app.agents.specialists import DOMAIN
from app.agents.tools import AGENT_FOR_KIND, find_asset, impacted, what_if
from app.cost import Assumptions
from app.schemas import CalculationRecord, RouteStep

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger("utilityguard.orchestrator")

ROUTER = """You are the orchestrator of UtilityGuard, a digital twin with three specialist agents:
water (leaks, pressure, pumps), electricity (substations, feeders, outages, load), cooling (district cooling plants and loops).
Cross-network links: substations power water pumps and cooling plants; heat raises cooling load which raises electrical load.
Pick which specialists must be consulted for the owner's question, in a sensible order, each with a short reason.
Return JSON: {"route": [{"agent": "water"|"electricity"|"cooling", "reason": str}]}"""

CONSULT = """You are the {agent} agent of UtilityGuard. Expert only in {domain}.
Answer the orchestrator about your domain in at most 2 sentences, using ONLY numbers present in the data.
Return JSON: {{"view": str}}"""

SYNTH = """You are the orchestrator of UtilityGuard, advising the business owner of the New Administrative Capital utilities.
Combine the specialists' views into one clear answer: the consequence, a ranked action plan, and the money (act now vs delay, saving).
Rules: use ONLY numbers that appear in the data; never estimate. If data is missing, say so.
Style: max 100 words, plain sentences, no markdown, no emoji, no quotes around numbers. Refer to assets by their short names (SS-02, P-04, DCP-01).
Write money as "18,400 EGP". Mention only the 3 or 4 most important effects, then a numbered action plan (1. 2. 3.), then act now vs delay and the saving.
Return JSON: {"answer": str}"""

KEYWORDS = {
    "water": r"water|pipe|leak|pump|pressure|reservoir|burst|\bp-?\d",
    "electricity": r"electric|power|substation|\bss-?\d|feeder|transformer|load|outage|trip|voltage|grid",
    "cooling": r"cool|chiller|dcp|plant|temperature|heat|hvac|loop",
}


class OState(TypedDict, total=False):
    question: str
    incident_id: str | None
    route: list[RouteStep]
    asset: str | None
    record: CalculationRecord | None
    whatif: dict
    views: dict[str, str]
    answer: str
    fallback: bool
    removed: list[str]
    extra_numbers: list[float]
    on_route: Any


def rule_route(q: str, rt: "Runtime", asset: str | None) -> list[RouteStep]:
    ql = q.lower()
    route = [RouteStep(agent=a, reason=f"Question mentions {a} terms") for a, pat in KEYWORDS.items() if re.search(pat, ql)]
    if asset and asset.startswith("elec-sub"):
        imp = impacted(rt, asset)
        route = [RouteStep(agent="electricity", reason=f"{rt.engine.g.asset_index[asset].name} trip and load transfer")]
        if imp["pumps"]:
            route.append(RouteStep(agent="water", reason=f"{rt.engine.g.asset_index[asset].name} powers {len(imp['pumps'])} pumps"))
        if imp["plants"]:
            route.append(RouteStep(agent="cooling", reason=f"{rt.engine.g.asset_index[asset].name} powers {', '.join(rt.engine.g.asset_index[p].name for p in imp['plants'])}"))
    if not route:
        open_kinds = {rt.detector.state.incident_kind.get(i.id) for i in rt.incidents.values() if i.state != "resolved"}
        route = [RouteStep(agent=AGENT_FOR_KIND[k], reason="Has an open incident") for k in open_kinds if k] or \
                [RouteStep(agent=a, reason="General status check") for a in ("water", "electricity", "cooling")]
    seen, out = set(), []
    for r in route:
        if r.agent not in seen:
            seen.add(r.agent)
            out.append(r)
    return out


def build_orchestrator(rt: "Runtime", A: Assumptions):
    g = rt.engine.g

    async def route(s: OState) -> OState:
        q = s["question"]
        asset = find_asset(q, rt)
        data, fb = await llm.json(llm.key("route", q.lower().strip()), ROUTER, q, model=MODEL_ORCH)
        try:
            steps = [RouteStep(agent=r["agent"], reason=r["reason"]) for r in data["route"] if r["agent"] in DOMAIN]
        except Exception:  # noqa: BLE001
            steps = []
        steps = steps or rule_route(q, rt, asset)
        log.info("route %s -> %s", q, [r.agent for r in steps])
        if s.get("on_route"):
            await s["on_route"](steps)
        return {"route": steps, "asset": asset, "fallback": fb}

    async def simulate(s: OState) -> OState:
        q, asset = s["question"].lower(), s.get("asset")
        if asset and re.search(r"what if|what happens|if .*(trip|fail|stop|burst|down)|trips|fails", q):
            facts, rec, deltas = await what_if(rt, asset, A)
            if rec:
                rec = rec.model_copy(update={"id": f"calc-wi-{len(rt.calculations) + 1:04d}"})
                rec.explanation.calculation_id = rec.id
                rt.calculations[rec.id] = rec
            top = sorted(((k, v) for k, v in deltas.items() if abs(v) > 0.05), key=lambda kv: -abs(kv[1]) / max(
                g.sensor_index[kv[0]].normal_range.max - g.sensor_index[kv[0]].normal_range.min, 1e-6))
            by_net: dict[str, list] = {}
            for sid, d in top:
                sn = g.sensor_index[sid]
                before = rt.latest[sid].value or 0.0
                by_net.setdefault(sn.network, []).append({
                    "what": f"{g.asset_index[sn.asset_id].name} {sid.removeprefix(sn.asset_id + '-').replace('-', ' ')}",
                    "from": round(before, 1), "to": round(before + d, 1), "change": round(d, 1), "unit": sn.unit})
            summary = {n: v[:4] for n, v in by_net.items()}
            return {"record": rec, "whatif": {"asset": g.asset_index[asset].name, "simulatedSeconds": 70,
                                              "facts": [r.text for r in facts.reasons] if facts else [], "changes": summary}}
        inc = rt.incidents.get(s.get("incident_id") or "") or next(
            (i for i in sorted(rt.incidents.values(), key=lambda i: i.opened_at, reverse=True) if i.state != "resolved"), None)
        rec = rt.calculations.get(inc.calculation_id) if inc and inc.calculation_id else None
        return {"record": rec, "whatif": {}}

    async def consult(s: OState) -> OState:
        views = {}
        open_incs = [i for i in rt.incidents.values() if i.state != "resolved"]
        for step in s["route"]:
            a = step.agent
            data = {
                "question": s["question"],
                "whatIf": {"asset": s["whatif"].get("asset"), "changes": s["whatif"].get("changes", {}).get(a, [])} if s["whatif"] else None,
                "openIncidents": [{"title": i.title, "networks": i.networks} for i in open_incs if a in i.networks],
                "findings": [{"headline": f.headline, "action": f.recommended_action.text, "actNow": f.cost.act_now,
                              "saving": f.cost.expected_saving} for f in rt.findings.values() if f.agent == a][-2:],
            }
            out, fb = await llm.json(llm.key("consult", a, s["question"].lower().strip()),
                                     CONSULT.format(agent=a, domain=DOMAIN[a]), json.dumps(data, default=str), model=MODEL_FAST)
            views[a] = (out or {}).get("view") or _template_view(a, data)
            s = {**s, "fallback": s.get("fallback") or fb}
        return {"views": views, "fallback": s.get("fallback", False)}

    async def synthesize(s: OState) -> OState:
        rec = s.get("record")
        data = {"question": s["question"], "specialists": s["views"], "whatIf": s["whatif"] or None,
                "cost": {"actNowEGP": rec.cost.act_now, "costOfDelayEGP": rec.cost.cost_of_delay, "delayHours": rec.cost.delay_hours,
                         "savingEGP": rec.cost.expected_saving, "breakdown": [b.dump() for b in rec.cost.breakdown]} if rec else "not available",
                "openFindings": [{"agent": f.agent, "action": f.recommended_action.text, "urgency": f.recommended_action.urgency,
                                  "saving": f.cost.expected_saving, "actNow": f.cost.act_now} for f in rt.findings.values()
                                 if rt.incidents.get(f.incident_id) and rt.incidents[f.incident_id].state != "resolved"]}
        out, fb = await llm.json(llm.key("synth", s["question"].lower().strip(), str(sorted(s["views"]))), SYNTH,
                                 json.dumps(data, default=str), model=MODEL_ORCH)
        answer = (out or {}).get("answer") or _template_answer(s, rec)
        extra = [x[k] for net in (s["whatif"] or {}).get("changes", {}).values() for x in net for k in ("from", "to", "change")]
        extra += [f.cost.expected_saving for f in rt.findings.values()] + [f.cost.act_now for f in rt.findings.values()]
        extra += [float(n.replace(",", "")) for v in s["views"].values() for n in re.findall(r"\d[\d,]*\.?\d*", v)]
        cleaned, removed = check(answer, allowed_numbers(rec, extra))
        return {"answer": cleaned, "removed": removed, "fallback": s.get("fallback") or fb}

    sg = StateGraph(OState)
    for name, fn in [("router", route), ("simulate", simulate), ("consult", consult), ("synthesize", synthesize)]:
        sg.add_node(name, fn)
    sg.set_entry_point("router")
    sg.add_edge("router", "simulate")
    sg.add_edge("simulate", "consult")
    sg.add_edge("consult", "synthesize")
    sg.add_edge("synthesize", END)
    return sg.compile()


def _template_view(agent: str, data: dict) -> str:
    if data.get("whatIf") and data["whatIf"]["changes"]:
        c = data["whatIf"]["changes"][0]
        return f"In the simulation, {c['what']} goes from {c['from']} to {c['to']} {c['unit']}."
    if data["findings"]:
        f = data["findings"][-1]
        return f"{f['headline']}. Recommended: {f['action']}."
    if data["openIncidents"]:
        return f"Open incident: {data['openIncidents'][0]['title']}."
    return f"The {agent} network is operating within its normal range."


def _template_answer(s: OState, rec: CalculationRecord | None) -> str:
    parts = [f"{a.capitalize()}: {v}" for a, v in s["views"].items()]
    if rec:
        parts.append(f"Acting now costs {rec.cost.act_now:,.0f} EGP versus {rec.cost.cost_of_delay:,.0f} EGP if delayed "
                     f"{rec.cost.delay_hours:g} h, a saving of {rec.cost.expected_saving:,.0f} EGP.")
    return " ".join(parts)
