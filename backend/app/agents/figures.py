"""Figures an agent cites by token instead of typing.

The number checker removes a wrong figure after the model has written it. This goes one step earlier: the model is given a table of the figures
the calculation allows, each with a token, and writes `{{act_now}}` where the figure belongs. The figure is put in afterwards, by code, from the
same record the dashboard shows, so a cited figure cannot be wrong and cannot be out of date when an answer recorded earlier is replayed after a
price change. Anything the model types as digits anyway still goes through the checker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agents.checker import NUM, REMOVED
from app.i18n import unit_ar
from app.schemas import CalculationRecord

TOKEN = re.compile(r"\{{1,2}\s*([A-Za-z][A-Za-z0-9_]*)\s*\}{1,2}")  # models drop or add a brace, so one or two on each side are read the same
DOUBLE_UNIT = re.compile(r"\b(EGP|MW|kV|bar|m3/h)(\s+)\1\b")  # a model that adds a unit after a token that already carries one
DOUBLE_UNIT_AR = re.compile(r"(جنيه|ميجاوات|كيلوفولت|بار|م³/ساعة)(\s+)\1")  # the same in Arabic


MONEY_AFTER = re.compile(r"[\s\u00a0\u202f]*(?:EGP|LE\b|pounds?\b|جنيه|ج\.م)", re.I)
MONEY_BEFORE = re.compile(r"(?:EGP|جنيه)[\s\u00a0\u202f]*$", re.I)


def typed_money(obj: Any) -> tuple[Any, list[str]]:
    """Remove every amount of money the model typed instead of citing its token, in a string or in the strings of a JSON answer.

    The checker passes a typed figure when the amount is somewhere in the data, but not that it is the amount it is said to be: the cost of acting
    now typed as "the saving" would pass. Every cost has a token tied to its label, so a typed cost is removed even when the amount exists.
    Run it on the model's own text, before the tokens are put in: a cited figure is not typed."""
    if isinstance(obj, list):
        pairs = [typed_money(x) for x in obj]
        return [x for x, _ in pairs], [r for _, rs in pairs for r in rs]
    if isinstance(obj, dict):
        pairs = {k: typed_money(v) for k, v in obj.items()}
        return {k: x for k, (x, _) in pairs.items()}, [r for _, rs in pairs.values() for r in rs]
    if not isinstance(obj, str):
        return obj, []
    tokens = [m.span() for m in TOKEN.finditer(obj)]
    removed: list[str] = []
    out, pos = [], 0
    for m in NUM.finditer(obj):
        if any(a <= m.start() < b for a, b in tokens):
            continue
        if MONEY_AFTER.match(obj, m.end()) or MONEY_BEFORE.search(obj[max(0, m.start() - 8):m.start()]):
            removed.append(m.group(0))
            out += [obj[pos:m.start()], REMOVED]
            pos = m.end()
    return "".join(out) + obj[pos:], removed


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass(frozen=True)
class Figure:
    key: str
    value: float
    display: str
    label: str


class FigureSet:
    def __init__(self) -> None:
        self.items: dict[str, Figure] = {}

    def add(self, key: str, value: float | None, display: str, label: str) -> None:
        if value is None or key in self.items:
            return
        self.items[key] = Figure(key, float(value), display, label)

    def named(self, zone_names: dict[str, str]) -> "FigureSet":
        """The same figures with a zone id in a label replaced by the district's name ("Energy not served, zone-cult" -> "... Arts and Culture City"), so a model reads a district
        and does not guess what `cult` or `pres` stands for."""
        out = FigureSet()
        for f in self.items.values():
            label = re.sub(r"zone-[a-z0-9-]+", lambda m: zone_names.get(m.group(0), m.group(0)), f.label)
            out.items[f.key] = Figure(f.key, f.value, f.display, label)
        return out

    def merge(self, other: "FigureSet") -> "FigureSet":
        for f in other.items.values():
            self.add(f.key, f.value, f.display, f.label)
        return self

    def values(self) -> list[float]:
        return [f.value for f in self.items.values()]

    def table(self) -> list[dict[str, str]]:
        """What the model sees: the token to write, what it means, and how it will read. The value itself is not repeated as a bare number."""
        return [{"token": "{{" + f.key + "}}", "means": f.label, "reads": f.display} for f in self.items.values()]

    def render(self, text: str, ar: bool = False) -> tuple[str, list[str]]:
        """Put the figures in. A token that is not in the table is removed, and so is the markup around it. In Arabic only the unit changes (22,508 EGP -> 22,508 جنيه)."""
        unknown: list[str] = []

        def sub(m: re.Match) -> str:
            f = self.items.get(m.group(1)) or self.items.get(m.group(1).lower())
            if f is None:
                unknown.append(m.group(0))
                return REMOVED
            return unit_ar(f.display) if ar else f.display

        out = TOKEN.sub(sub, text)
        return (DOUBLE_UNIT_AR if ar else DOUBLE_UNIT).sub(r"\1", out), unknown

    def render_any(self, obj: Any, ar: bool = False) -> tuple[Any, list[str]]:
        """The same for the strings inside a model's JSON answer (a dict of strings and lists of strings)."""
        unknown: list[str] = []
        if isinstance(obj, str):
            return self.render(obj, ar)
        if isinstance(obj, list):
            out = []
            for x in obj:
                y, u = self.render_any(x, ar)
                out.append(y)
                unknown += u
            return out, unknown
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                out[k], u = self.render_any(v, ar)
                unknown += u
            return out, unknown
        return obj, unknown


def money(v: float) -> str:
    return f"{v:,.0f} EGP"


def item_key(i: int, label: str, prefix: str = "") -> str:
    return f"{prefix}item_{i}_{slug(label)[:24]}"


def for_record(rec: CalculationRecord | None, prefix: str = "") -> FigureSet:
    fs = FigureSet()
    if rec is None:
        return fs
    c = rec.cost
    p = prefix
    fs.add(f"{p}act_now", c.act_now, money(c.act_now), "cost of acting now (repair now plus the losses until the crew arrives)")
    fs.add(f"{p}delay_per_hour", c.delay_per_hour, money(c.delay_per_hour), "extra cost for each hour of delay")
    fs.add(f"{p}expected_saving", c.expected_saving, money(c.expected_saving), "expected saving of acting now instead of waiting")
    if c.cost_of_delay is not None:
        fs.add(f"{p}cost_of_delay", c.cost_of_delay, money(c.cost_of_delay), "total cost of waiting")
    if c.delay_hours is not None:
        fs.add(f"{p}delay_hours", c.delay_hours, f"{c.delay_hours:g} h", "the delay that is compared with acting now")
    if c.response_hours is not None:
        fs.add(f"{p}response_hours", c.response_hours, f"{c.response_hours:g} h", "hours until a crew can arrive")
    for i, b in enumerate(c.breakdown, 1):
        fs.add(item_key(i, b.label, p), b.amount, money(b.amount), f"cost item: {b.label}")
    return fs


def for_sensors(sensors: list[dict]) -> FigureSet:
    fs = FigureSet()
    for s in sensors:
        base, unit = slug(s["sensor"]), s.get("unit") or ""
        for suffix, field, what in (("now", "now", "reading now"), ("min30", "min30", "lowest reading in the last 30 samples"),
                                    ("max30", "max30", "highest reading in the last 30 samples")):
            v = s.get(field)
            fs.add(f"{base}_{suffix}", v, f"{v:g} {unit}".strip() if v is not None else "", f"{s['sensor']}: {what}")
    return fs


def for_findings(findings: list) -> FigureSet:
    fs = FigureSet()
    for f in findings:
        k = slug(f.id)
        fs.add(f"{k}_act_now", f.cost.act_now, money(f.cost.act_now), f"{f.agent} finding {f.id}: cost of acting now")
        fs.add(f"{k}_saving", f.cost.expected_saving, money(f.cost.expected_saving), f"{f.agent} finding {f.id}: expected saving")
        if getattr(f.cost, "delay_per_hour", None) is not None:
            fs.add(f"{k}_delay_per_hour", f.cost.delay_per_hour, money(f.cost.delay_per_hour), f"{f.agent} finding {f.id}: cost of each hour it stays open")
        if getattr(f.cost, "cost_of_delay", None) is not None:
            fs.add(f"{k}_cost_of_delay", f.cost.cost_of_delay, money(f.cost.cost_of_delay), f"{f.agent} finding {f.id}: total cost of waiting its own delay horizon")
        if getattr(f.cost, "delay_hours", None) is not None:
            fs.add(f"{k}_delay_hours", f.cost.delay_hours, f"{f.cost.delay_hours:g} h", f"{f.agent} finding {f.id}: the delay horizon the cost model uses for this kind of fault")
    return fs
