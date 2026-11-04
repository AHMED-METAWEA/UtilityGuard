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
from app.agents.figures import TOKEN, FigureSet, for_record, for_sensors
from app.agents.llm import MODEL_FAST, llm
from app.agents.tools import CAUSES, CAUSES_AR, PLAYBOOK_ACTION_AR, PLAYBOOKS, impacted, read_sensors
from app.cost import Assumptions, IncidentFacts, compute
from app.i18n import CREW_AR, ZONE_AR, ar_hours, asset_ar
from app.schemas import AgentFinding, CalculationRecord, FindingText, Incident, PredictedImpact, RecommendedAction

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger("caregrid.agents")

DOMAIN = {
    "water": "the water network: leaks, pressure, pumps, water loss",
    "electricity": "the electricity network: substations, feeders, transformers, outages and overloads",
    "cooling": "district cooling: chillers, supply temperature, chilled water loops, cooling energy",
}

SYSTEM = """You are the {agent} agent of Care Grid, a digital twin of the New Administrative Capital.
You are an expert only in {domain}, and you treat it as the most important thing in the system.
You receive measured facts and a cost calculation computed by code.
Rules:
- Never type a digit for a cost, a duration or a sensor reading. The data holds a table of `figures`: write the figure's token, for example {{{{act_now}}}}.
  The token already carries its unit and is replaced by code with the calculated value, so do not add a unit after it. Use only tokens from the table.
- Asset names such as SS-02 and counts below ten may be written as they are. Any other number you type must appear in the data, quoted exactly. Never invent or estimate a number.
- If something is not in the data, say it is not available.
- Plain language for a business owner. Short.
- `probableCauses` are where the fault comes from, never what it does. Do not list a consequence (pumps stopping, load moving to a backup substation, load shed, pressure falling, a flow
  imbalance) or an observation restated as a cause. Choose from `usualCauses` and say which the data supports; if the data does not show it, say the cause is not determined from the data and
  has to be confirmed on site. Never state a cause as certain when the data does not show it.
Return JSON: {{"headline": str (max 12 words), "explanation": str (2 sentences), "probableCauses": [str, str],
"impact": str (1 sentence), "action": str (1 imperative sentence),
"ar": {{"headline": str, "explanation": str, "probableCauses": [str, str], "impact": str, "action": str}}}}
The "ar" object is the same answer written in Arabic (Modern Standard Arabic, plain words a business owner understands): same meaning, same figure tokens, the same caution about
causes. Do not type a digit or a unit for a figure there either: the token carries its unit. Asset codes such as SS-02 and P-03 stay in Latin letters.
In "ar", use these words: pipe or main = ماسورة (the main = الخط الرئيسي), burst = كسر, leak = تسريب, pump = مضخة, substation = محطة كهرباء, chiller = مبرّد, cooling plant = محطة التبريد,
sensor = حساس. Name a district only as `zonesAr` gives it. "probableCauses" in "ar" are the items of `usualCausesAr` that match the ones you chose in English, word for word, and
"action" in "ar" follows `playbook.actionAr` (the same steps, the same crew)."""


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
    text_ar: dict
    steps_ar: list[str]
    fallback: bool
    removed: list[str]
    figs: FigureSet
    t0: float
    finding: Any


def _step(s: SState, msg: str) -> list[str]:
    """Appends one step to the English trail. The Arabic trail (`steps_ar`) is kept beside it with `_step_ar`."""
    log.info("[%s] %s: %s", s["agent"], s["incident"].id, msg)
    return [*s.get("steps", []), msg]


def _step_ar(s: SState, msg: str) -> list[str]:
    return [*s.get("steps_ar", []), msg]


def build_specialist(rt: "Runtime", A: Assumptions):
    g = rt.engine.g
    seq = [0]
    rt.reset_hooks.append(lambda: seq.__setitem__(0, 0))

    def next_finding_id() -> str:
        """Findings are numbered as they are made. Counting the stored ones (`len(findings) + 1`) gave two findings finished in the same instant the same id, and the second replaced the first."""
        seq[0] = max(seq[0], len(rt.findings)) + 1
        return f"find-{seq[0]:04d}"

    def gather(s: SState) -> SState:
        inc = s["incident"]
        assets = [a for aid in inc.alert_ids for a in rt.alerts[aid].asset_ids] or [s["root"]]
        sensors = read_sensors(rt, list(dict.fromkeys(assets)))
        imp = impacted(rt, s["root"])
        names = [g.asset_index[a].name for a in list(dict.fromkeys(assets))[:3]]
        steps = _step(s, f"Read {len(sensors)} sensors on {', '.join(names)}")
        steps = _step({**s, "steps": steps}, f"Checked dependencies: {len(imp['zones'])} zone(s), {len(imp['pumps'])} pump(s), {len(imp['plants'])} plant(s) linked")
        sar = _step_ar(s, f"قرأتُ حساسات {'، '.join(asset_ar(n) for n in names)} (العدد: {len(sensors)})")
        sar = _step_ar({**s, "steps_ar": sar}, f"فحصتُ الاعتماديات: المناطق {len(imp['zones'])}، المضخات {len(imp['pumps'])}، محطات التبريد {len(imp['plants'])}")
        return {"sensors": sensors, "impact": imp, "steps": steps, "steps_ar": sar}

    def cost(s: SState) -> SState:
        rec = compute(s["facts"], A, s["incident"].id)
        rt.calculations[rec.id] = rec
        steps = _step(s, f"Ran cost model {rec.id}: act now vs delay of {rec.cost.delay_hours:g} h")
        sar = _step_ar(s, f"شغّلتُ نموذج التكلفة {rec.id}: التصرف الآن مقابل التأخير {ar_hours(rec.cost.delay_hours)}")
        return {"record": rec, "steps": steps, "steps_ar": sar}

    async def reason(s: SState) -> SState:
        rec, f = s["record"], s["facts"]
        crew, urgency, action = PLAYBOOKS[f.kind]
        figs = for_record(rec).merge(for_sensors(s["sensors"][:8])).named({z.id: z.name for z in g.topology.zones})
        data = {
            "incident": s["incident"].title, "kind": f.kind, "reasons": [r.text for r in f.reasons],
            "sensors": [{"sensor": x["sensor"], "kind": x["kind"], "unit": x["unit"], "normal": x["normal"], "quality": x["quality"]} for x in s["sensors"][:8]],
            "dependencies": s["impact"], "figures": figs.table(),
            "playbook": {"crew": crew, "action": action, "actionAr": PLAYBOOK_ACTION_AR[f.kind]}, "usualCauses": CAUSES[f.kind], "usualCausesAr": CAUSES_AR[f.kind],
            "zonesAr": {z: ZONE_AR.get(z, z) for z in f.zone_types},
        }
        key = llm.key("finding4", s["agent"], f.kind, s["root"])  # 4: the Arabic version follows a glossary and the Arabic causes, so answers recorded under the first Arabic wording are not replayed
        out, fb = await llm.json(key, SYSTEM.format(agent=s["agent"], domain=DOMAIN[s["agent"]]),
                                 json.dumps(data, default=str, ensure_ascii=False), model=MODEL_FAST)
        if not out:
            out = template(s)
        out = dict(out)
        ar = out.pop("ar", None)
        if not _valid_ar(ar):
            ar = template_ar(s)  # the model gave no usable Arabic: the computed wording stands in, and the finding says which it is
        steps = _step(s, f"Looked up {crew} playbook and wrote recommendation ({'recorded fallback' if fb else 'live LLM'})")
        sar = _step_ar(s, f"راجعتُ خطة فريق {CREW_AR.get(crew, crew)} وكتبتُ التوصية ({'إجابة مسجّلة' if fb else 'نموذج لغوي مباشر'})")
        return {"text": out, "text_ar": ar, "fallback": fb, "steps": steps, "steps_ar": sar, "figs": figs}

    def verify(s: SState) -> SState:
        allowed = allowed_numbers(s["record"], [x for sn in s["sensors"] for x in (sn["now"], sn["min30"] or 0, sn["max30"] or 0, *sn["normal"])])
        figs = s.get("figs") or FigureSet()
        rendered, unknown = figs.render_any(s["text"])  # cited figures go in first; a token that is not in the table is removed
        cleaned, removed = _clean(rendered, allowed)
        removed = [*unknown, *removed]
        steps, fallback = s.get("steps", []), s.get("fallback", False)
        steps_ar = s.get("steps_ar", [])
        if removed and fallback:
            # a recorded answer quotes figures from an older state: use the computed wording instead of leaving holes
            cleaned, removed = _clean(template(s), allowed)
            steps = _step(s, "Recorded wording was out of date, so the computed wording is used")
            steps_ar = _step_ar(s, "الصياغة المسجّلة قديمة، فاستُخدمت الصياغة المحسوبة")
        # the Arabic wording is held to the same rule: figures by token, anything typed as digits must be in the calculation
        rendered_ar, unknown_ar = figs.render_any(s.get("text_ar") or template_ar(s), ar=True)
        cleaned_ar, removed_ar = _clean(rendered_ar, allowed)
        removed_ar = [*unknown_ar, *removed_ar]
        if removed_ar:  # a figure that cannot be verified is not removed from Arabic prose: the computed Arabic sentence replaces the whole text
            cleaned_ar, _ = _clean(template_ar(s), allowed)
            steps_ar = _step_ar({**s, "steps_ar": steps_ar}, f"فحص الأرقام حذف {len(removed_ar)} رقمًا غير مؤكَّد من النص العربي، فاستُخدمت الصياغة المحسوبة")
        cited = len(TOKEN.findall(json.dumps(s["text"], ensure_ascii=False)))
        msg = ("Number check passed: every figure matches the calculation" + (f" ({cited} cited by token)" if cited else "")
               if not removed else f"Number check removed {len(removed)} unverified figure(s)")
        msg_ar = ("فحص الأرقام نجح: كل رقم يطابق الحساب" + (f" ({cited} مذكور برمز)" if cited else "") if not removed else f"فحص الأرقام حذف {len(removed)} رقمًا غير مؤكَّد")
        return {"text": cleaned, "text_ar": cleaned_ar, "removed": removed, "steps": _step({**s, "steps": steps}, msg), "steps_ar": _step_ar({**s, "steps_ar": steps_ar}, msg_ar)}

    def finalize(s: SState) -> SState:
        f, rec, t = s["facts"], s["record"], s["text"]
        crew, urgency, action = PLAYBOOKS[f.kind]
        elapsed = time.time() - s["t0"]
        finding = AgentFinding(
            id=next_finding_id(), incident_id=s["incident"].id, agent=s["agent"],
            headline=t.get("headline") or s["incident"].title, explanation=t.get("explanation", ""),
            probable_causes=t.get("probableCauses") or ["Not available"],
            predicted_impact=PredictedImpact(text=t.get("impact", ""), zone_ids=list(f.zone_types)),
            recommended_action=RecommendedAction(text=t.get("action") or action, crew_type=crew, urgency=urgency),
            cost=rec.cost, reasoning_steps=[*s["steps"], f"Finding ready in {elapsed:.1f} s"], explain=rec.explanation,
            created_at=datetime.now(timezone.utc), fallback=s.get("fallback", False),
            number_check="corrected" if s.get("removed") else "passed",
            ar=_finding_ar(s, crew, action, elapsed))
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
        "probableCauses": CAUSES[f.kind][:2],
        "impact": f"Affects {', '.join(f.zone_types)}.",
        "action": action,
    }


def _valid_ar(ar) -> bool:
    return (isinstance(ar, dict) and all(isinstance(ar.get(k), str) and ar[k].strip() for k in ("headline", "explanation", "impact", "action"))
            and isinstance(ar.get("probableCauses"), list) and bool(ar["probableCauses"]) and all(isinstance(x, str) for x in ar["probableCauses"]))


def template_ar(s: SState) -> dict:
    """The deterministic wording in Arabic: used when the model gave no Arabic, or its Arabic could not be verified."""
    f, rec, inc = s["facts"], s["record"], s["incident"]
    first = next((r.text_ar for r in f.reasons if r.text_ar), None) or inc.title_ar or inc.title
    zones = "، ".join(ZONE_AR.get(z, z) for z in f.zone_types) or "—"
    return {
        "headline": inc.title_ar or inc.title,
        "explanation": f"{first}. التصرف الآن يكلّف {rec.cost.act_now:,.0f} جنيه؛ والانتظار {ar_hours(rec.cost.delay_hours)} يكلّف {rec.cost.cost_of_delay:,.0f} جنيه.",
        "probableCauses": CAUSES_AR[f.kind][:2],
        "impact": f"يؤثر على: {zones}.",
        "action": PLAYBOOK_ACTION_AR[f.kind],
    }


def _finding_ar(s: SState, crew: str, action: str, elapsed: float) -> FindingText:
    t = s.get("text_ar") or template_ar(s)
    return FindingText(headline=t.get("headline") or s["incident"].title_ar or s["incident"].title, explanation=t.get("explanation", ""),
                       probable_causes=t.get("probableCauses") or ["غير متاح"], impact=t.get("impact", ""), action=t.get("action") or PLAYBOOK_ACTION_AR[s["facts"].kind],
                       reasoning_steps=[*s.get("steps_ar", []), f"النتيجة جاهزة خلال {elapsed:.1f} ثانية"])


async def run_specialist(graph, agent: str, incident: Incident, facts: IncidentFacts, root: str) -> AgentFinding:
    out = await graph.ainvoke({"agent": agent, "incident": incident, "facts": facts, "root": root, "steps": [], "t0": time.time()})
    return out["finding"]
