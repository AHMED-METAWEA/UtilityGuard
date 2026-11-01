"""Number checker (spec 9A guardrails): every figure in LLM text must exist in the computed data."""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas import CalculationRecord

# \d also matches Arabic-Indic and fullwidth digits; the Arabic thousands (U+066C) and decimal (U+066B) separators are accepted next to , and . and the Arabic scale words
# (thousand, million, billion) count like the English ones. Arabic letters may touch a figure ("و٥٠٠": the conjunction is written glued to it); Latin letters may not (asset ids).
NUM = re.compile(r"(?<![\d_A-Za-z.,\u066b\u066c])(\d{1,3}(?:[,\u066c]\d{3})+(?:[.\u066b]\d+)?|\d+(?:[.\u066b]\d+)?)(\s*(?:million|billion|thousand|\u0645\u0644\u064a\u0648\u0646|\u0645\u0644\u0627\u064a\u064a\u0646|\u0645\u0644\u064a\u0627\u0631|\u0645\u0644\u064a\u0627\u0631\u0627\u062a|\u0623\u0644\u0641|\u0622\u0644\u0627\u0641)\b|[kKmMbB](?![A-Za-z0-9]))?(?![A-Za-z0-9_])")
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9,
         "\u0623\u0644\u0641": 1e3, "\u0622\u0644\u0627\u0641": 1e3, "\u0645\u0644\u064a\u0648\u0646": 1e6, "\u0645\u0644\u0627\u064a\u064a\u0646": 1e6,
         "\u0645\u0644\u064a\u0627\u0631": 1e9, "\u0645\u0644\u064a\u0627\u0631\u0627\u062a": 1e9}


def value(m: re.Match) -> float:
    """The number a match stands for: '5 million' is 5,000,000 and '36k' is 36,000, so a scale word cannot hide a figure."""
    digits = m.group(1).replace(",", "").replace("\u066c", "").replace("\u066b", ".")
    return abs(float(digits)) * SCALE.get((m.group(2) or "").strip().lower(), 1.0)


_UNITS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), 1)}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_BIG = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_WORD = "|".join([*_UNITS, *_TENS, *_BIG, "a", "an", "and"])
WORDNUM = re.compile(rf"\b(?:(?:{_WORD})\b[ \t-]*)+", re.I)


def _words_value(raw: str) -> float | None:
    """'fifty thousand' is 50,000 and 'a million' is 1,000,000. Only a run that contains a scale word counts: 'one of the pumps' is not a figure."""
    toks = [t for t in re.split(r"[ \t-]+", raw.lower().strip()) if t]
    while toks and toks[0] == "and":
        toks.pop(0)
    while toks and toks[-1] in ("and", "a", "an"):
        toks.pop()
    if not toks or not any(t in _BIG for t in toks) or all(t in ("a", "an", "and") for t in toks):
        return None
    total, cur = 0.0, 0.0
    for t in toks:
        if t in ("a", "an"):
            cur = cur or 1
        elif t in _UNITS:
            cur += _UNITS[t]
        elif t in _TENS:
            cur += _TENS[t]
        elif t == "hundred":
            cur = (cur or 1) * 100
        elif t in _BIG:
            total += (cur or 1) * _BIG[t]
            cur = 0
    return total + cur


REMOVED = "[figure removed]"


def _quantities(text: str):
    """Every figure in a text, as (start, end, raw, value, what follows it): digits and spelled-out numbers, in reading order."""
    spans = []
    for m in NUM.finditer(text):
        before = text[max(0, m.start() - 3):m.start()]
        if re.search(r"[A-Za-z]-$|[A-Za-z]$", before) or text[m.end():m.end() + 1] == ":" or before.endswith(":"):
            continue  # asset ids like SS-03 or R5 and clock times
        spans.append((m.start(), m.end(), m.group(0), value(m), text[m.end():m.end() + 8]))
    for m in WORDNUM.finditer(text):
        v = _words_value(m.group(0))
        if v is not None:
            raw = m.group(0).rstrip(" \t-")
            spans.append((m.start(), m.start() + len(raw), raw, v, text[m.start() + len(raw):m.start() + len(raw) + 8]))
    return sorted(spans)


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
    out, pos = [], 0
    for start, end, raw, x, after in _quantities(text):
        if start < pos:
            continue
        out.append(text[pos:start])
        if _ok(x, allowed, after):
            out.append(raw)
        else:
            removed.append(raw)
            out.append(REMOVED)
        pos = end
    out.append(text[pos:])
    return "".join(out), removed


def figures(text: str, allowed: Labeled) -> list[dict]:
    """Every figure in a text with the verdict the checker gives it and, when it passes, the calculated value it matches."""
    out = []
    for _, _, raw, x, after in _quantities(text):
        hit = _match(x, allowed)
        if hit is None and _small_count(x, after):
            out.append({"text": raw, "value": x, "verdict": "verified", "matched": "a small count or ordinal", "matched_value": x})
        elif hit is None:
            out.append({"text": raw, "value": x, "verdict": "removed", "matched": None, "matched_value": None})
        else:
            out.append({"text": raw, "value": x, "verdict": "verified", "matched": hit[1], "matched_value": hit[0]})
    return out
