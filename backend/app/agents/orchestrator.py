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
from app.agents.figures import FigureSet, for_findings, for_record, item_key, money, slug
from app.agents.llm import MODEL_FAST, MODEL_ORCH, llm
from app.agents.specialists import DOMAIN
from app.agents.tools import AGENT_FOR_KIND, find_asset, impacted, what_if
from app.cost import Assumptions, _summary, policy_kind
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
Answer the orchestrator about your domain in at most 2 sentences. A fall in a substation's load is not load shed: the facts say how much is shed, and the rest moves to the
backup substation. Say only what the data shows; if the question assumes a failure the data does not show, say so. Never type a cost: write its token from `figures`, for example {{{{find_0001_act_now}}}};
the token carries its unit and is replaced by code with the calculated value. Any other number you type must be in the data, quoted exactly.
Return JSON: {{"view": str}}"""

SYNTH = """You are the orchestrator of UtilityGuard, advising the business owner of the New Administrative Capital utilities.
Combine the specialists' views into one clear answer: the consequence, a ranked action plan, and the money (act now vs delay, saving).
Rules: never type a cost. The data holds a table of `figures`: write the token of each money amount or duration, for example {{act_now}}; the token already
carries its unit ("18,400 EGP") and is replaced by code with the calculated value, so do not add a unit after it. Any other number you type must be in the data,
quoted exactly; never estimate. If data is missing, say so.
Several incidents may be open. Each figure belongs to the incident it is listed with: never quote one incident's cost as the cost of another or of the whole, and rank the plan by the
cost of an hour of delay, highest first (`situation.ranked`). When `horizon` is given the owner asked what waiting that long costs: answer with its figures, one per incident and the total.
If `cost` is "not available" use only the figures of `situation` and `horizon`. "Saving" is money saved by acting now instead of waiting; never call it a loss. If the question assumes something that is not open or not in the data (for example a pump failure when none is open), say that first.
Say what the data shows about a cause and no more; never state a cause as certain when the data does not show it.
Answer in the language of the question (Arabic if the owner wrote in Arabic); keep asset names and the figure tokens as given.
Style: max 100 words, plain sentences, no markdown, no emoji, no quotes around numbers. Refer to assets by their short names (SS-02, P-04, DCP-01).
Mention only the 3 or 4 most important effects, then a numbered action plan (1. 2. 3.), then act now vs delay and the saving.
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
    situation: dict


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



HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b|ساعة|ساعه|ساعات)", re.I)
WAITING = re.compile(r"wait|delay|late|postpone|hold off|نستنى|نستني|ننتظر|استنى|استني|تأخير|تاخير|نأجل|نأخر|نتأخر|نتاخر", re.I)
SHORT_NAME = re.compile(r"\b[A-Z]{1,4}-\d{1,2}\b")


def situation(rt: "Runtime", A: Assumptions, question: str, asset: str | None, whatif_asset: str | None) -> dict:
    """What is open right now, ranked by what an hour of delay costs, as figures the answer cites by token; the cost of waiting as long as the question asks, by the cost model's own
    formula; the assets an answer may name; and a signature of the situation so that a recorded answer is only replayed for the situation it was recorded in."""
    g = rt.engine.g
    det = getattr(rt, "detector", None)  # a bare runtime (a test, a sandbox) has no detector: the situation is then only what the findings say
    open_ids = {i.id for i in rt.incidents.values() if i.state != "resolved"}
    latest: dict[str, Any] = {}
    for f in rt.findings.values():
        if f.incident_id in open_ids:
            latest[f.incident_id] = f
    ranked = sorted(latest.values(), key=lambda f: (-f.cost.delay_per_hour, f.id))
    fs = for_findings(ranked)
    total = sum(f.cost.delay_per_hour for f in ranked)
    fs.add("open_delay_per_hour", total, money(total), "cost of each hour of delay with all the open incidents together")
    horizon = None
    m, w = HOURS.search(question), WAITING.search(question)
    if m and w and ranked and 0 < float(m.group(1)) <= 240:
        h = float(m.group(1))
        rows, whole, saved = [], 0.0, 0.0
        for f in ranked:
            got = det.facts_for(f.incident_id) if det else None
            if not got:
                continue
            facts = got[0]
            n = _summary(facts, A.with_override(f"delay_hours_{policy_kind(facts)}", h))
            whole += n["cost_of_delay"]
            saved += n["cost_of_delay"] - n["act_now"]
            fs.add(f"{slug(f.id)}_wait_h", n["cost_of_delay"], money(n["cost_of_delay"]), f"{f.agent} finding {f.id}: cost of waiting {h:g} h (the cost model's formula with the delay set to {h:g} h)")
            fs.add(f"{slug(f.id)}_wait_h_saving", n["cost_of_delay"] - n["act_now"], money(n["cost_of_delay"] - n["act_now"]),
                   f"{f.agent} finding {f.id}: saving of acting now instead of waiting {h:g} h")
            rows.append({"incident": rt.incidents[f.incident_id].title, "cost": "{{" + f"{slug(f.id)}_wait_h" + "}}", "savingByActingNow": "{{" + f"{slug(f.id)}_wait_h_saving" + "}}"})
        fs.add("wait_h_total", whole, money(whole), f"cost of waiting {h:g} h with all the open incidents together")
        fs.add("wait_h_saving_total", saved, money(saved), f"saving of acting now on all the open incidents instead of waiting {h:g} h")
        horizon = {"hours": h, "perIncident": rows, "total": "{{wait_h_total}}", "savingTotal": "{{wait_h_saving_total}}",
                   "note": "cost of delay = cost of an hour x hours + the repair at its later price, the formula of the cost model, with the delay set to the hours asked"}
    names: set[str] = set()
    ids = [x for i in rt.incidents.values() if i.id in open_ids for a in i.alert_ids if a in rt.alerts for x in rt.alerts[a].asset_ids]
    for a in [*ids, asset, whatif_asset]:
        if isinstance(a, str) and a in g.asset_index:
            names.add(g.asset_index[a].name)
            if a.startswith("elec-sub"):  # what the substation powers is in play too
                imp = impacted(rt, a)
                names |= {g.asset_index[x].name for x in [*imp["pumps"], *imp["plants"]] if x in g.asset_index}
    for n in list(names):
        names |= set(SHORT_NAME.findall(n))
    names |= set(SHORT_NAME.findall(question.upper()))  # what the owner typed is in play
    sig = "|".join(sorted(f"{i.id}:{det.state.incident_kind.get(i.id)}" for i in rt.incidents.values() if i.id in open_ids)) if det else "|".join(sorted(open_ids))
    return {"ranked": ranked, "figures": fs, "horizon": horizon, "names": names, "signature": sig, "total": total}


def play_names(s: dict) -> set[str]:
    """The assets an answer may name: those of the situation, and those the simulated what-if moved (its sensors and its facts)."""
    w = s.get("whatif") or {}
    texts = [x["what"] for net in w.get("changes", {}).values() for x in net] + list(w.get("facts", []))
    return set(s["situation"]["names"]) | {t for x in texts for t in SHORT_NAME.findall(x)}


def names_ok(text: str, names: set[str]) -> bool:
    """An answer may name only assets that are open, affected by what was asked, or in the question: a recorded answer about another situation names others."""
    return all(any(n == t or n.endswith((" " + t, "-" + t)) for n in names) for t in SHORT_NAME.findall(text))  # "GOV-01" is part of the name "WP-GOV-01"


def build_orchestrator(rt: "Runtime", A: Assumptions):
    g = rt.engine.g
    zone_names = {z.id: z.name for z in g.topology.zones}

    async def route(s: OState) -> OState:
        q = s["question"]
        asset = find_asset(q, rt)
        data, fb = await llm.json(llm.key("route", q.lower().strip()), ROUTER, q, model=MODEL_ORCH)
        try:
            # the reason is shown to the operator, and routing has no record to cite, so no figure in it can be supported
            steps = [RouteStep(agent=r["agent"], reason=check(str(r["reason"]), set())[0]) for r in data["route"] if r["agent"] in DOMAIN]
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
                    "from": round(before, 1), "to": max(0.0, round(before + d, 1)) + 0.0 if sn.kind in ("flow", "pressure", "load", "voltage") else round(before + d, 1),
                    "change": round(d, 1), "unit": sn.unit})
            summary = {n: v[:4] for n, v in by_net.items()}
            return {"record": rec, "situation": situation(rt, A, s["question"], asset, asset),
                    "whatif": {"asset": g.asset_index[asset].name, "simulatedSeconds": 70, "facts": [r.text for r in facts.reasons] if facts else [], "changes": summary,
                              "note": "A fall in a substation's load is load that stopped or moved to the backup substation. Only the amount in `facts` ('... of non-priority load is shed') is not served."}}
        sit = situation(rt, A, s["question"], asset, None)
        chosen = rt.incidents.get(s.get("incident_id") or "")
        if chosen is None and len(sit["ranked"]) == 1:
            chosen = rt.incidents.get(sit["ranked"][0].incident_id)
        # one incident (named, or the only one open): its record answers the question. Several open: no single record can, so the answer is built from every incident's own figures
        rec = rt.calculations.get(chosen.calculation_id) if chosen and chosen.calculation_id else None
        return {"record": rec, "whatif": {}, "situation": sit}

    async def consult(s: OState) -> OState:
        views = {}
        open_incs = [i for i in rt.incidents.values() if i.state != "resolved"]
        for step in s["route"]:
            a = step.agent
            mine = [f for f in rt.findings.values() if f.agent == a and f.incident_id in {i.id for i in open_incs}][-2:]
            fs = for_findings(mine).named(zone_names)
            facts_of = [(i, rt.detector.facts_for(i.id)) for i in open_incs if a in i.networks] if getattr(rt, "detector", None) else [(i, None) for i in open_incs if a in i.networks]
            data = {
                "question": s["question"],
                "whatIf": {"asset": s["whatif"].get("asset"), "changes": s["whatif"].get("changes", {}).get(a, []), "facts": s["whatif"].get("facts", []),
                           "note": s["whatif"].get("note")} if s["whatif"] else None,
                "openIncidents": [{"title": i.title, "networks": i.networks, "zones": [zone_names.get(z, z) for z in i.zone_ids],
                                   "whatTheDataShows": [r.text for r in got[0].reasons] if got else []} for i, got in facts_of],
                "findings": [{"headline": f.headline, "action": f.recommended_action.text, "actNow": "{{" + f"{f.id.replace('-', '_')}_act_now" + "}}",
                              "saving": "{{" + f"{f.id.replace('-', '_')}_saving" + "}}"} for f in mine],
                "figures": fs.table(),
            }
            out, fb = await llm.json(llm.key("consult2", a, s["question"].lower().strip(), s["situation"]["signature"]),
                                     CONSULT.format(agent=a, domain=DOMAIN[a]), json.dumps(data, default=str), model=MODEL_FAST)
            rec = s.get("record")
            known = [x[k] for net in (s["whatif"] or {}).get("changes", {}).values() for x in net for k in ("from", "to", "change")]
            known += [v for f in rt.findings.values() for v in (f.cost.expected_saving, f.cost.act_now, f.cost.cost_of_delay or 0)]
            raw = (out or {}).get("view")
            if isinstance(raw, str):
                raw, unknown = fs.render(raw)
            else:
                unknown = []
            view, removed = check(raw, allowed_numbers(rec, known)) if raw else ("", [])
            removed = [*unknown, *removed]
            # a specialist view with a figure we cannot trace to the data is replaced by the computed wording
            views[a] = view if raw and not removed and names_ok(view, play_names(s)) else _template_view(a, data)
            s = {**s, "fallback": s.get("fallback") or fb or bool(removed)}
        return {"views": views, "fallback": s.get("fallback", False)}

    async def synthesize(s: OState) -> OState:
        rec, sit = s.get("record"), s["situation"]
        fs = for_record(rec).merge(sit["figures"]) if rec else sit["figures"]
        fs = fs.named(zone_names)
        if sit["horizon"]:
            rec = None  # one incident's record is priced at its own delay horizon: next to the cost of waiting as long as the owner asked it would give a saving that belongs to another horizon
        tok = lambda key: "{{" + key + "}}"  # noqa: E731
        ranked = [{"incident": rt.incidents[f.incident_id].title, "agent": f.agent, "action": f.recommended_action.text, "urgency": f.recommended_action.urgency,
                   "delayPerHour": tok(f"{slug(f.id)}_delay_per_hour"), "actNow": tok(f"{slug(f.id)}_act_now"), "saving": tok(f"{slug(f.id)}_saving"),
                   "costOfDelay": tok(f"{slug(f.id)}_cost_of_delay"), "delayHours": tok(f"{slug(f.id)}_delay_hours")} for f in sit["ranked"]]
        data = {"question": s["question"], "specialists": s["views"], "whatIf": s["whatif"] or None,
                "cost": {"actNow": tok("act_now"), "costOfDelay": tok("cost_of_delay"), "delayHours": tok("delay_hours"), "saving": tok("expected_saving"),
                         "breakdown": [{"item": b.label, "amount": tok(item_key(i, b.label))} for i, b in enumerate(rec.cost.breakdown, 1)]} if rec else "not available",
                "situation": {"openIncidents": len(ranked), "ranked": ranked, "allTogetherPerHour": tok("open_delay_per_hour")},
                "horizon": sit["horizon"], "figures": fs.table()}
        out, fb = await llm.json(llm.key("synth2", s["question"].lower().strip(), str(sorted(s["views"])), sit["signature"]), SYNTH,
                                 json.dumps(data, default=str), model=MODEL_ORCH)
        answer = (out or {}).get("answer") or _template_answer(s, rec)
        answer, unknown = fs.render(answer) if isinstance(answer, str) else (answer, [])
        extra = [x[k] for net in (s["whatif"] or {}).get("changes", {}).values() for x in net for k in ("from", "to", "change")]
        extra += [f.cost.expected_saving for f in rt.findings.values()] + [f.cost.act_now for f in rt.findings.values()] + sit["figures"].values()
        extra += [float(n.replace(",", "")) for v in s["views"].values() for n in re.findall(r"\d[\d,]*\.?\d*", v)]
        allowed = allowed_numbers(rec, extra)
        cleaned, removed = check(answer, allowed)
        removed = [*unknown, *removed]
        stray = not names_ok(cleaned, play_names(s))  # an asset that is not in play: the answer is about another situation (a recorded one) or invents one
        if removed or stray:  # the wording quoted a figure that is not in the data, or named an asset that is not in play: use the computed wording
            cleaned, _ = check(_template_answer(s, rec), allowed)
            removed = removed or ["asset not in play"]
        return {"answer": cleaned, "removed": removed, "fallback": s.get("fallback") or fb or bool(removed)}

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
    """The answer written by code when the language model is out of reach or its wording could not be verified. It says what the question asked, from the same figures."""
    sit, fs = s["situation"], s["situation"]["figures"]
    h = sit["horizon"]
    if h:
        lines = [f"{r['incident']}: {fs.items[r['cost'].strip('{}')].display}" for r in h["perIncident"]]
        return (f"Waiting {h['hours']:g} h before sending a crew costs, by the cost model's formula (an hour of delay times the hours, plus the repair at its later price): "
                + "; ".join(lines) + f". Together: {fs.items['wait_h_total'].display}. Acting now instead would save {fs.items['wait_h_saving_total'].display} of that.")
    views = " ".join(v.rstrip(".") + "." for v in s["views"].values())
    if s.get("whatif") and rec:
        return f"{views} Acting now costs {rec.cost.act_now:,.0f} EGP versus {rec.cost.cost_of_delay:,.0f} EGP if delayed {rec.cost.delay_hours:g} h, a saving of {rec.cost.expected_saving:,.0f} EGP."
    if sit["ranked"]:
        first = (f"{len(sit['ranked'])} problems are open. " if len(sit["ranked"]) > 1 else "") + "In order, the most expensive hour first. "
        steps = " ".join(f"{k}. {f.recommended_action.text.rstrip('.')} ({f.headline}): {f.cost.delay_per_hour:,.0f} EGP an hour open, {f.cost.act_now:,.0f} EGP to act now, saving {f.cost.expected_saving:,.0f} EGP."
                         for k, f in enumerate(sit["ranked"], 1))
        tail = f" All together they cost {money(sit['total'])} for each hour they stay open." if len(sit["ranked"]) > 1 else ""
        return first + steps + tail
    return views or "No incident is open: every network is within its normal range."
