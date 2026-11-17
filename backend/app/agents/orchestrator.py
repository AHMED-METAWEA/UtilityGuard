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
from app.agents.figures import FigureSet, for_findings, for_record, item_key, money, slug, typed_money
from app.agents.llm import MODEL_FAST, MODEL_ORCH, llm
from app.agents.specialists import DOMAIN
from app.agents.tools import AGENT_FOR_KIND, find_asset, impacted, what_if
from app.cost import Assumptions, _summary, policy_kind
from app.i18n import NETWORK_AR, SENSOR_AR, ZONE_AR, ar_hours, asset_ar, unit_ar
from app.schemas import CalculationRecord, RouteStep

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger("caregrid.orchestrator")

ROUTER = """You are the orchestrator of Care Grid, a digital twin with three specialist agents:
water (leaks, pressure, pumps), electricity (substations, feeders, outages, load), cooling (district cooling plants and loops).
Cross-network links: substations power water pumps and cooling plants; heat raises cooling load which raises electrical load. The links in this district:
@@LINKS@@
Pick only the specialists the owner's question needs, in a sensible order, each with a short reason: the network the question is about, and a network the links
above show is affected. Do not add an agent to be safe: an agent whose network the question does not touch makes the answer slower and longer.
@@LANG@@
Return JSON: {"route": [{"agent": "water"|"electricity"|"cooling", "reason": str}]}"""

CONSULT = """You are the {agent} agent of Care Grid. Expert only in {domain}.
Answer the orchestrator about your domain in at most 2 sentences. A fall in a substation's load is not load shed: the facts say how much is shed, and the rest moves to the
backup substation. Say only what the data shows; if the question assumes a failure the data does not show, say so. Never type a cost: write its token from `figures`, for example {{{{find_0001_act_now}}}};
the token carries its unit and is replaced by code with the calculated value. A cost typed in digits is removed even when it is right. Any other number you type must be in the data, quoted exactly.
@@LANG@@
Return JSON: {{"view": str}}"""

SYNTH = """You are the orchestrator of Care Grid, advising the business owner of the New Administrative Capital utilities.
Combine the specialists' views into one clear answer: the consequence, a ranked action plan, and the money (act now vs delay, saving).
Rules: never type a cost. The data holds a table of `figures`: write the token of each money amount or duration, for example {{act_now}}; the token already
carries its unit ("18,400 EGP") and is replaced by code with the calculated value, so do not add a unit after it. A cost typed in digits is removed even when it is right. Any other number you type must be in the data,
quoted exactly; never estimate. If data is missing, say so.
Several incidents may be open. Each figure belongs to the incident it is listed with: never quote one incident's cost as the cost of another or of the whole, and rank the plan by the
cost of an hour of delay, highest first (`situation.ranked`). When `horizon` is given the owner asked what waiting that long costs: answer with its figures, one per incident and the total.
If `cost` is "not available" use only the figures of `situation` and `horizon`. "Saving" is money saved by acting now instead of waiting; never call it a loss. If the question assumes something that is not open or not in the data (for example a pump failure when none is open), say that first.
Say what the data shows about a cause and no more; never state a cause as certain when the data does not show it.
@@LANG@@ Keep asset names and the figure tokens as given.
Style: max 100 words, plain sentences, no markdown, no emoji, no quotes around numbers. Refer to assets by their short names (SS-02, P-04, DCP-01).
Mention only the 3 or 4 most important effects, then a numbered action plan (1. 2. 3.), then act now vs delay and the saving.
Return JSON: {"answer": str}"""

LANG_RULE = {"en": "Write in English.",
             "ar": "Write in Arabic (Modern Standard Arabic, plain words an owner understands). Asset codes such as SS-02 and P-04 stay in Latin letters; never type a digit or a unit for a figure that has a token. "
                   "Use these words: pipe or main = ماسورة (the main = الخط الرئيسي), burst = كسر, leak = تسريب, pump = مضخة, substation = محطة كهرباء, chiller = مبرّد, cooling plant = محطة التبريد, "
                   "sensor = حساس, and name the districts only as: " + "; ".join(f"{k} = {v}" for k, v in ZONE_AR.items()) + "."}


def say(prompt: str, lang: str) -> str:
    """The prompt with the language the owner chose for the agents."""
    return prompt.replace("@@LANG@@", LANG_RULE[lang])


KEYWORDS = {
    "water": r"water|pipe|leak|pump|pressure|reservoir|burst|\bp-?\d|مياه|ماسور|مواسير|تسريب|تسرب|مضخ|طلمب|ضغط|خزان|محابس",
    "electricity": r"electric|power|substation|\bss-?\d|feeder|transformer|load|outage|trip|voltage|grid|megawatt|كهرب|محول|مغذي|حمل|انقطاع|فصل|جهد|ميجاوات",
    "cooling": r"cool|chiller|dcp|plant|temperature|heat|hvac|loop|تبريد|مبرد|مبرّد|تكييف|حرار|الحر\b|نبرد",
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
    lang: str
    situation: dict


def topology_route(rt: "Runtime", asset: str | None, ar: bool = False) -> list[RouteStep]:
    """The agents a named asset needs, from the network links: its own network's, and for a substation, the agents of the pumps and plants it powers."""
    if not asset:
        return []
    nm = lambda a: asset_ar(rt.engine.g.asset_index[a].name) if ar else rt.engine.g.asset_index[a].name  # noqa: E731
    if not asset.startswith("elec-sub"):
        own = "water" if asset.startswith("water-") else "cooling" if asset.startswith("cool-") else None
        return [RouteStep(agent=own, reason=f"السؤال عن {nm(asset)}" if ar else f"The question is about {nm(asset)}")] if own else []
    imp = impacted(rt, asset)
    route = [RouteStep(agent="electricity", reason=f"فصل {nm(asset)} وانتقال الحمل" if ar else f"{nm(asset)} trip and load transfer")]
    if imp["pumps"]:
        route.append(RouteStep(agent="water", reason=f"{nm(asset)} تغذّي {len(imp['pumps'])} مضخة" if ar else f"{nm(asset)} powers {len(imp['pumps'])} pumps"))
    if imp["plants"]:
        route.append(RouteStep(agent="cooling", reason=f"{nm(asset)} تغذّي {'، '.join(nm(p) for p in imp['plants'])}" if ar else f"{nm(asset)} powers {', '.join(nm(p) for p in imp['plants'])}"))
    return route


def with_floor(route: list[RouteStep], floor: list[RouteStep]) -> list[RouteStep]:
    """The model's route, with every agent the network links require added after it: the model may add and order agents, never drop a required one."""
    have = {r.agent for r in route}
    return [*route, *(r for r in floor if r.agent not in have)]


def rule_route(q: str, rt: "Runtime", asset: str | None, ar: bool = False) -> list[RouteStep]:
    ql = q.lower()
    route = [RouteStep(agent=a, reason=f"السؤال يذكر مصطلحات {NETWORK_AR[a]}" if ar else f"Question mentions {a} terms") for a, pat in KEYWORDS.items() if re.search(pat, ql)]
    if asset and asset.startswith("elec-sub"):
        route = topology_route(rt, asset, ar)
    if not route:
        open_kinds = {rt.detector.state.incident_kind.get(i.id) for i in rt.incidents.values() if i.state != "resolved"}
        route = [RouteStep(agent=AGENT_FOR_KIND[k], reason="لديه حادثة مفتوحة" if ar else "Has an open incident") for k in open_kinds if k] or \
                [RouteStep(agent=a, reason="فحص عام للحالة" if ar else "General status check") for a in ("water", "electricity", "cooling")]
    seen, out = set(), []
    for r in route:
        if r.agent not in seen:
            seen.add(r.agent)
            out.append(r)
    return out



HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b|ساعة|ساعه|ساعات)", re.I)
WAITING = re.compile(r"wait|delay|late|postpone|hold off|نستنى|نستني|ننتظر|استنى|استني|تأخير|تاخير|نأجل|نأخر|نتأخر|نتاخر", re.I)
SHORT_NAME = re.compile(r"\b[A-Z]{1,4}-\d{1,2}\b")


def _title(inc, ar: bool) -> str:
    return (inc.title_ar or inc.title) if ar else inc.title


def situation(rt: "Runtime", A: Assumptions, question: str, asset: str | None, whatif_asset: str | None, ar: bool = False) -> dict:
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
            rows.append({"incident": _title(rt.incidents[f.incident_id], ar), "cost": "{{" + f"{slug(f.id)}_wait_h" + "}}", "savingByActingNow": "{{" + f"{slug(f.id)}_wait_h_saving" + "}}"})
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


def links(rt: "Runtime") -> str:
    """Which pumps and cooling plants each substation powers, from the topology, for the router."""
    names = lambda ids: ", ".join(rt.engine.g.asset_index[a].name for a in ids) or "nothing else"  # noqa: E731
    subs = [a for a in rt.engine.g.asset_index if a.startswith("elec-sub")]
    return "\n".join(f"{rt.engine.g.asset_index[sub].name} powers {names(impacted(rt, sub)['pumps'] + impacted(rt, sub)['plants'])}" for sub in sorted(subs))


async def model_route(q: str, lang: str = "en", rt: "Runtime | None" = None) -> tuple[list[RouteStep], bool]:
    """The route the language model picks, and whether it came from the recorded fallback. Empty when the model gives nothing usable."""
    prompt = say(ROUTER, lang).replace("@@LINKS@@", links(rt) if rt else "not given")
    data, fb = await llm.json(llm.key("route3", lang, q.lower().strip()), prompt, q, model=MODEL_ORCH)
    try:
        # the reason is shown to the operator, and routing has no record to cite, so no figure in it can be supported
        steps = [RouteStep(agent=r["agent"], reason=check(str(r["reason"]), set())[0]) for r in data["route"] if r["agent"] in DOMAIN]
    except Exception:  # noqa: BLE001
        steps = []
    seen: set[str] = set()
    return [r for r in steps if not (r.agent in seen or seen.add(r.agent))], fb


def build_orchestrator(rt: "Runtime", A: Assumptions):
    g = rt.engine.g
    zone_names = {z.id: z.name for z in g.topology.zones}

    async def route(s: OState) -> OState:
        q, lang = s["question"], s.get("lang", "en")
        asset = find_asset(q, rt)
        steps, fb = await model_route(q, lang, rt)
        steps = with_floor(steps, topology_route(rt, asset, lang == "ar")) if steps else rule_route(q, rt, asset, lang == "ar")
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
                suffix = sid.removeprefix(sn.asset_id + '-').replace('-', ' ')
                by_net.setdefault(sn.network, []).append({
                    "what": f"{g.asset_index[sn.asset_id].name} {suffix}",
                    "whatAr": f"{SENSOR_AR.get(suffix, suffix)} {asset_ar(g.asset_index[sn.asset_id].name)}",
                    "from": round(before, 1), "to": max(0.0, round(before + d, 1)) + 0.0 if sn.kind in ("flow", "pressure", "load", "voltage") else round(before + d, 1),
                    "change": round(d, 1), "unit": sn.unit})
            summary = {n: v[:4] for n, v in by_net.items()}
            return {"record": rec, "situation": situation(rt, A, s["question"], asset, asset, s.get("lang") == "ar"),
                    "whatif": {"asset": g.asset_index[asset].name, "simulatedSeconds": 70, "facts": [r.text for r in facts.reasons] if facts else [], "facts_ar": [(r.text_ar or r.text) for r in facts.reasons] if facts else [], "changes": summary,
                              "note": "A fall in a substation's load is load that stopped or moved to the backup substation. Only the amount in `facts` ('... of non-priority load is shed') is not served."}}
        sit = situation(rt, A, s["question"], asset, None, s.get("lang") == "ar")
        chosen = rt.incidents.get(s.get("incident_id") or "")
        if chosen is None and len(sit["ranked"]) == 1:
            chosen = rt.incidents.get(sit["ranked"][0].incident_id)
        # one incident (named, or the only one open): its record answers the question. Several open: no single record can, so the answer is built from every incident's own figures
        rec = rt.calculations.get(chosen.calculation_id) if chosen and chosen.calculation_id else None
        return {"record": rec, "whatif": {}, "situation": sit}

    async def consult(s: OState) -> OState:
        views = {}
        lang = s.get("lang", "en")
        ar = lang == "ar"
        open_incs = [i for i in rt.incidents.values() if i.state != "resolved"]
        for step in s["route"]:
            a = step.agent
            mine = [f for f in rt.findings.values() if f.agent == a and f.incident_id in {i.id for i in open_incs}][-2:]
            fs = for_findings(mine).named(zone_names)
            facts_of = [(i, rt.detector.facts_for(i.id)) for i in open_incs if a in i.networks] if getattr(rt, "detector", None) else [(i, None) for i in open_incs if a in i.networks]
            data = {
                "question": s["question"],
                "whatIf": {"asset": s["whatif"].get("asset"), "changes": s["whatif"].get("changes", {}).get(a, []), "facts": s["whatif"].get("facts_ar" if ar else "facts", []),
                           "note": s["whatif"].get("note")} if s["whatif"] else None,
                "openIncidents": [{"title": _title(i, ar), "networks": i.networks, "zones": [zone_names.get(z, z) for z in i.zone_ids],
                                   "whatTheDataShows": [(r.text_ar or r.text) if ar else r.text for r in got[0].reasons] if got else []} for i, got in facts_of],
                "findings": [{"headline": (f.ar.headline if ar and f.ar else f.headline), "action": (f.ar.action if ar and f.ar else f.recommended_action.text), "actNow": "{{" + f"{f.id.replace('-', '_')}_act_now" + "}}",
                              "saving": "{{" + f"{f.id.replace('-', '_')}_saving" + "}}"} for f in mine],
                "figures": fs.table(),
            }
            out, fb = await llm.json(llm.key("consult3", lang, a, s["question"].lower().strip(), s["situation"]["signature"]),
                                     say(CONSULT, lang).format(agent=a, domain=DOMAIN[a]), json.dumps(data, default=str, ensure_ascii=False), model=MODEL_FAST)
            rec = s.get("record")
            known = [x[k] for net in (s["whatif"] or {}).get("changes", {}).values() for x in net for k in ("from", "to", "change")]
            known += [v for f in rt.findings.values() for v in (f.cost.expected_saving, f.cost.act_now, f.cost.cost_of_delay or 0)]
            raw = (out or {}).get("view")
            if isinstance(raw, str):
                raw, typed = typed_money(raw)
                raw, unknown = fs.render(raw, ar)
                unknown = [*typed, *unknown]
            else:
                unknown = []
            view, removed = check(raw, allowed_numbers(rec, known)) if raw else ("", [])
            removed = [*unknown, *removed]
            # a specialist view with a figure we cannot trace to the data is replaced by the computed wording
            views[a] = view if raw and not removed and names_ok(view, play_names(s)) else _template_view(a, data, ar)
            s = {**s, "fallback": s.get("fallback") or fb or bool(removed)}
        return {"views": views, "fallback": s.get("fallback", False)}

    async def synthesize(s: OState) -> OState:
        rec, sit = s.get("record"), s["situation"]
        lang = s.get("lang", "en")
        ar = lang == "ar"
        fs = for_record(rec).merge(sit["figures"]) if rec else sit["figures"]
        fs = fs.named(zone_names)
        if sit["horizon"]:
            rec = None  # one incident's record is priced at its own delay horizon: next to the cost of waiting as long as the owner asked it would give a saving that belongs to another horizon
        tok = lambda key: "{{" + key + "}}"  # noqa: E731
        ranked = [{"incident": _title(rt.incidents[f.incident_id], ar), "agent": f.agent, "action": (f.ar.action if ar and f.ar else f.recommended_action.text), "urgency": f.recommended_action.urgency,
                   "delayPerHour": tok(f"{slug(f.id)}_delay_per_hour"), "actNow": tok(f"{slug(f.id)}_act_now"), "saving": tok(f"{slug(f.id)}_saving"),
                   "costOfDelay": tok(f"{slug(f.id)}_cost_of_delay"), "delayHours": tok(f"{slug(f.id)}_delay_hours")} for f in sit["ranked"]]
        data = {"question": s["question"], "specialists": s["views"], "whatIf": s["whatif"] or None,
                "cost": {"actNow": tok("act_now"), "costOfDelay": tok("cost_of_delay"), "delayHours": tok("delay_hours"), "saving": tok("expected_saving"),
                         "breakdown": [{"item": b.label, "amount": tok(item_key(i, b.label))} for i, b in enumerate(rec.cost.breakdown, 1)]} if rec else "not available",
                "situation": {"openIncidents": len(ranked), "ranked": ranked, "allTogetherPerHour": tok("open_delay_per_hour")},
                "horizon": sit["horizon"], "figures": fs.table()}
        out, fb = await llm.json(llm.key("synth3", lang, s["question"].lower().strip(), str(sorted(s["views"])), sit["signature"]), say(SYNTH, lang),
                                 json.dumps(data, default=str, ensure_ascii=False), model=MODEL_ORCH)
        answer, typed = typed_money((out or {}).get("answer"))
        answer = answer or _template_answer(s, rec, ar)
        answer, unknown = fs.render(answer, ar) if isinstance(answer, str) else (answer, [])
        unknown = [*typed, *unknown]
        extra = [x[k] for net in (s["whatif"] or {}).get("changes", {}).values() for x in net for k in ("from", "to", "change")]
        extra += [f.cost.expected_saving for f in rt.findings.values()] + [f.cost.act_now for f in rt.findings.values()] + sit["figures"].values()
        if sit["horizon"]:
            extra.append(sit["horizon"]["hours"])  # the hours the owner asked about are a figure of the question, and the answer repeats them
        extra += [float(n.replace(",", "")) for v in s["views"].values() for n in re.findall(r"\d[\d,]*\.?\d*", v)]
        allowed = allowed_numbers(rec, extra)
        cleaned, removed = check(answer, allowed)
        removed = [*unknown, *removed]
        stray = not names_ok(cleaned, play_names(s))  # an asset that is not in play: the answer is about another situation (a recorded one) or invents one
        if removed or stray:  # the wording quoted a figure that is not in the data, or named an asset that is not in play: use the computed wording
            cleaned, _ = check(_template_answer(s, rec, ar), allowed)
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


def _template_view(agent: str, data: dict, ar: bool = False) -> str:
    if data.get("whatIf") and data["whatIf"]["changes"]:
        c = data["whatIf"]["changes"][0]
        if ar:
            return f"في المحاكاة، {c.get('whatAr', c['what'])} يتغير من {c['from']} إلى {c['to']} {unit_ar(c['unit'])}."
        return f"In the simulation, {c['what']} goes from {c['from']} to {c['to']} {c['unit']}."
    if data["findings"]:
        f = data["findings"][-1]
        return f"{f['headline']}. المطلوب: {f['action']}." if ar else f"{f['headline']}. Recommended: {f['action']}."
    if data["openIncidents"]:
        return f"حادثة مفتوحة: {data['openIncidents'][0]['title']}." if ar else f"Open incident: {data['openIncidents'][0]['title']}."
    return f"شبكة {NETWORK_AR[agent]} تعمل ضمن نطاقها الطبيعي." if ar else f"The {agent} network is operating within its normal range."


def _template_answer(s: OState, rec: CalculationRecord | None, ar: bool = False) -> str:
    """The answer written by code when the language model is out of reach or its wording could not be verified. It says what the question asked, from the same figures."""
    sit, fs = s["situation"], s["situation"]["figures"]
    h = sit["horizon"]
    show = (lambda k: unit_ar(fs.items[k].display)) if ar else (lambda k: fs.items[k].display)
    if h:
        lines = [f"{r['incident']}: {show(r['cost'].strip('{}'))}" for r in h["perIncident"]]
        if ar:
            return (f"الانتظار {ar_hours(h['hours'])} قبل إرسال طاقم يكلّف، بصيغة نموذج التكلفة (تكلفة ساعة التأخير × عدد الساعات + الإصلاح بسعره المتأخر): "
                    + "؛ ".join(lines) + f". المجموع: {show('wait_h_total')}. التصرف الآن بدل الانتظار يوفّر {show('wait_h_saving_total')} من ذلك.")
        return (f"Waiting {h['hours']:g} h before sending a crew costs, by the cost model's formula (an hour of delay times the hours, plus the repair at its later price): "
                + "; ".join(lines) + f". Together: {show('wait_h_total')}. Acting now instead would save {show('wait_h_saving_total')} of that.")
    views = " ".join(v.rstrip(".") + "." for v in s["views"].values())
    if s.get("whatif") and rec:
        if ar:
            return f"{views} التصرف الآن يكلّف {rec.cost.act_now:,.0f} جنيه مقابل {rec.cost.cost_of_delay:,.0f} جنيه لو تأخر {ar_hours(rec.cost.delay_hours)}، بتوفير {rec.cost.expected_saving:,.0f} جنيه."
        return f"{views} Acting now costs {rec.cost.act_now:,.0f} EGP versus {rec.cost.cost_of_delay:,.0f} EGP if delayed {rec.cost.delay_hours:g} h, a saving of {rec.cost.expected_saving:,.0f} EGP."
    if sit["ranked"]:
        n = len(sit["ranked"])
        if ar:
            first = (("مشكلتان مفتوحتان. " if n == 2 else f"{n} مشكلات مفتوحة. ") if n > 1 else "") + "بالترتيب، الأغلى في الساعة أولًا. "
            steps = " ".join(f"{k}. {(f.ar.action if f.ar else f.recommended_action.text).rstrip('.')} ({f.ar.headline if f.ar else f.headline}): "
                             f"{f.cost.delay_per_hour:,.0f} جنيه في كل ساعة تبقى مفتوحة، و{f.cost.act_now:,.0f} جنيه للتصرف الآن، بتوفير {f.cost.expected_saving:,.0f} جنيه."
                             for k, f in enumerate(sit["ranked"], 1))
            tail = f" مجتمعةً تكلّف {unit_ar(money(sit['total']))} عن كل ساعة تبقى مفتوحة." if n > 1 else ""
            return first + steps + tail
        first = (f"{n} problems are open. " if n > 1 else "") + "In order, the most expensive hour first. "
        steps = " ".join(f"{k}. {f.recommended_action.text.rstrip('.')} ({f.headline}): {f.cost.delay_per_hour:,.0f} EGP an hour open, {f.cost.act_now:,.0f} EGP to act now, saving {f.cost.expected_saving:,.0f} EGP."
                         for k, f in enumerate(sit["ranked"], 1))
        tail = f" All together they cost {money(sit['total'])} for each hour they stay open." if n > 1 else ""
        return first + steps + tail
    return views or ("لا توجد حوادث مفتوحة: كل الشبكات ضمن نطاقها الطبيعي." if ar else "No incident is open: every network is within its normal range.")
