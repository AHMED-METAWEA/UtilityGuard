"""Number checker (spec 9A guardrails): every figure in LLM text must exist in the computed data."""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas import CalculationRecord

NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s*(?:million|billion|thousand)\b|[kKmMbB](?![A-Za-z0-9]))?(?![\w])")
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def value(m: re.Match) -> float:
    """The number a match stands for: '5 million' is 5,000,000 and '36k' is 36,000, so a scale word cannot hide a figure."""
    return abs(float(m.group(1).replace(",", ""))) * SCALE.get((m.group(2) or "").strip().lower(), 1.0)
REMOVED = "[figure removed]"


Labeled = list[tuple[float, str]]


def allowed_labeled(record: CalculationRecord | None, extra: Iterable[float] = ()) -> Labeled:
    """Every figure the calculation (and the data the agent was shown) allows, each with the name of where it comes from."""
    out: Labeled = []
    if record:
        c, e = record.cost, record.explanation
        out += [(c.act_now, "cost of acting now"), (c.delay_per_hour, "cost of delay per hour"), (c.expected_saving, "expected saving"),
                (c.delay_hours or 0, "delay hours"), (c.cost_of_delay or 0, "cost of delay")]
        out += [(b.amount, f"cost item: {b.label}") for b in c.breakdown]
        for calc in e.calculations:
            out.append((calc.result, f"result: {calc.name}"))
            out += [(i.value, f"input: {i.name}") for i in calc.inputs]
        out += [(a.value, f"assumption: {a.name}") for a in e.assumptions]
        for s in e.sensitivity:
            out += [(s.new_saving, "sensitivity: saving"), (abs(s.change_percent), "sensitivity: change")]
        out.append((round(e.confidence * 100), "confidence"))
        for r in e.reasons:
            out += [(value(m), "stated in a reason") for m in NUM.finditer(r.text)]
    out += [(abs(x), "figure in the data the agent was shown") for x in extra]  # last, so a figure the record also holds is named by the record
    return out


def allowed_numbers(record: CalculationRecord | None, extra: Iterable[float] = ()) -> set[float]:
    return {v for v, _ in allowed_labeled(record, extra)}


UNIT_AFTER = re.compile(r"\s*(%|m3|m³|egp|mw|kw|kv|bar|°c|tr\b|ton)", re.I)


def _small_count(x: float, after: str) -> bool:
    """Small whole numbers are counts and ordinals ("3 agents", "step 2"), unless a unit follows: "5 m3/h" is a quantity."""
    return x <= 10 and x == int(x) and not UNIT_AFTER.match(after)


def _match(x: float, allowed: Labeled) -> tuple[float, str] | None:
    for a, label in allowed:
        if a == 0:
            continue
        # allow the rounding an LLM naturally does: 36,020 -> 36,000 or 36k, 1.43 -> 1.4
        if abs(x - a) <= max(0.011 * abs(a), 0.051) or (abs(a) >= 1000 and abs(x * 1000 - a) <= 0.011 * abs(a)):
            return a, label
    return None


def _ok(x: float, allowed: set[float], after: str = "") -> bool:
    if _small_count(x, after):
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
        raw = m.group(0)
        # ids like SS-03, P-04, R5, CL-02 and times like 14:32 are not quantities
        before = text[max(0, m.start() - 3):m.start()]
        if re.search(r"[A-Za-z]-$|[A-Za-z]$", before) or text[m.end():m.end() + 1] == ":" or before.endswith(":"):
            return raw
        x = value(m)
        if _ok(x, allowed, text[m.end():m.end() + 8]):
            return raw
        removed.append(raw)
        return REMOVED

    return NUM.sub(sub, text), removed


def figures(text: str, allowed: Labeled) -> list[dict]:
    """Every figure in a text with the verdict the checker gives it and, when it passes, the calculated value it matches."""
    out = []
    for m in NUM.finditer(text):
        before = text[max(0, m.start() - 3):m.start()]
        if re.search(r"[A-Za-z]-$|[A-Za-z]$", before) or text[m.end():m.end() + 1] == ":" or before.endswith(":"):
            continue  # asset ids and clock times
        raw = m.group(0)
        x = value(m)
        hit = _match(x, allowed)
        if hit is None and _small_count(x, text[m.end():m.end() + 8]):
            out.append({"text": raw, "value": x, "verdict": "verified", "matched": "a small count or ordinal", "matched_value": x})
        elif hit is None:
            out.append({"text": raw, "value": x, "verdict": "removed", "matched": None, "matched_value": None})
        else:
            out.append({"text": raw, "value": x, "verdict": "verified", "matched": hit[1], "matched_value": hit[0]})
    return out
