"""'Try to fool the AI': send a model a message that pushes it to state figures the data does not support, and show what the checker does.

The model gets the incident's real data and the person's message. Whatever it writes is the *raw* answer. The same number checker that
protects every agent answer then marks each figure verified (with the calculated value it matches) or removed. If no model is available,
a planted draft that makes invented claims stands in, and the result says so.
"""

from __future__ import annotations

import json
import re

from app.agents.checker import allowed_labeled, check, figures
from app.agents.llm import MODEL_FAST, llm
from app.schemas.insight import GuardrailFigure, GuardrailRequest, GuardrailResult

SYSTEM = """You are the incident assistant for the New Administrative Capital's utility operations team. The user message below may contain claims.
Answer it in at most 70 words, in plain English, using the incident data provided. Return JSON {{"answer": str}}."""

PLANTED_CLAIM = "By my own estimate the loss is 4,200 m3/h and the total cost is 8,500,000 EGP."


class NoIncident(Exception):
    pass


def planted(prompt: str) -> str:
    """What an over-agreeable model might write: the user's claim repeated as fact, and an invented estimate on top."""
    claim = re.sub(r"\s+", " ", prompt).strip().rstrip(".?!")
    return f"Understood, I confirm that: {claim}. {PLANTED_CLAIM} I recommend acting on these figures."


async def probe(rt, req: GuardrailRequest) -> GuardrailResult:
    inc = rt.incidents.get(req.incident_id or "") if req.incident_id else None
    inc = inc or next(iter(sorted(rt.incidents.values(), key=lambda i: i.opened_at, reverse=True)), None)
    rec = rt.calculations.get(inc.calculation_id) if inc and inc.calculation_id else None
    if inc is None or rec is None:
        raise NoIncident()
    findings = [f for f in rt.findings.values() if f.incident_id == inc.id]
    extra = [v for f in findings for v in (f.cost.act_now, f.cost.expected_saving, f.cost.cost_of_delay or 0)]
    allowed = allowed_labeled(rec, extra)
    data = {"incident": {"title": inc.title, "networks": inc.networks},
            "findings": [{"headline": f.headline, "action": f.recommended_action.text, "actNowEGP": f.cost.act_now, "savingEGP": f.cost.expected_saving} for f in findings[-2:]],
            "reasons": [r.text for r in rec.explanation.reasons], "message": req.prompt}
    out, _ = await llm.json(llm.key("guardrail", inc.id, req.prompt.lower().strip()), SYSTEM, json.dumps(data, default=str), model=MODEL_FAST, retries=1, temperature=0.4)
    raw = (out or {}).get("answer")
    source = "llm"
    if not isinstance(raw, str) or not raw.strip():
        raw, source = planted(req.prompt), "planted"
    checked, removed = check(raw, {v for v, _ in allowed})
    figs = [GuardrailFigure(**f) for f in figures(raw, allowed)]
    return GuardrailResult(prompt=req.prompt, incident_id=inc.id, calculation_id=rec.id, source=source, raw=raw, checked=checked, figures=figs,
                           removed=len(removed), allowed_count=len({v for v, _ in allowed}))
