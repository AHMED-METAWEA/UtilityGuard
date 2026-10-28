"""Number checker (spec 9A guardrails): every figure in LLM text must exist in the computed data."""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas import CalculationRecord

NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")
REMOVED = "[figure removed]"


def allowed_numbers(record: CalculationRecord | None, extra: Iterable[float] = ()) -> set[float]:
    vals: set[float] = {abs(x) for x in extra}
    if record:
        c, e = record.cost, record.explanation
        vals |= {c.act_now, c.delay_per_hour, c.expected_saving, c.delay_hours or 0, c.cost_of_delay or 0}
        vals |= {b.amount for b in c.breakdown}
        for calc in e.calculations:
            vals.add(calc.result)
            vals |= {i.value for i in calc.inputs}
        vals |= {a.value for a in e.assumptions}
        for s in e.sensitivity:
            vals |= {s.new_saving, abs(s.change_percent)}
        vals |= {round(e.confidence * 100)}
        for r in e.reasons:
            vals |= {float(m.replace(",", "")) for m in NUM.findall(r.text)}
    return vals


def _ok(x: float, allowed: set[float]) -> bool:
    if x <= 10 and x == int(x):  # small counts and ordinals ("3 agents", "step 2")
        return True
    for a in allowed:
        if a == 0:
            continue
        # allow the rounding an LLM naturally does: 36,020 -> 36,000 or 36k, 1.43 -> 1.4
        if abs(x - a) <= max(0.011 * abs(a), 0.051):
            return True
        if abs(a) >= 1000 and abs(x * 1000 - a) <= 0.011 * abs(a):
            return True
    return False


def check(text: str, allowed: set[float]) -> tuple[str, list[str]]:
    """Return text with unverifiable figures removed, and the list of removed figures."""
    removed: list[str] = []

    def sub(m: re.Match) -> str:
        raw = m.group(1)
        # ids like SS-03, P-04, R5, CL-02 and times like 14:32 are not quantities
        before = text[max(0, m.start() - 3):m.start()]
        if re.search(r"[A-Za-z]-$|[A-Za-z]$", before) or text[m.end():m.end() + 1] == ":" or before.endswith(":"):
            return raw
        x = abs(float(raw.replace(",", "")))
        if _ok(x, allowed):
            return raw
        removed.append(raw)
        return REMOVED

    return NUM.sub(sub, text), removed
