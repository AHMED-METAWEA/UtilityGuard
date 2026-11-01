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

from app.agents.checker import REMOVED
from app.schemas import CalculationRecord

TOKEN = re.compile(r"\{{1,2}\s*([A-Za-z][A-Za-z0-9_]*)\s*\}{1,2}")  # models drop or add a brace, so one or two on each side are read the same
DOUBLE_UNIT = re.compile(r"\b(EGP|MW|kV|bar|m3/h)(\s+)\1\b")  # a model that adds a unit after a token that already carries one


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

    def merge(self, other: "FigureSet") -> "FigureSet":
        for f in other.items.values():
            self.add(f.key, f.value, f.display, f.label)
        return self

    def values(self) -> list[float]:
        return [f.value for f in self.items.values()]

    def table(self) -> list[dict[str, str]]:
        """What the model sees: the token to write, what it means, and how it will read. The value itself is not repeated as a bare number."""
        return [{"token": "{{" + f.key + "}}", "means": f.label, "reads": f.display} for f in self.items.values()]

    def render(self, text: str) -> tuple[str, list[str]]:
        """Put the figures in. A token that is not in the table is removed, and so is the markup around it."""
        unknown: list[str] = []

        def sub(m: re.Match) -> str:
            f = self.items.get(m.group(1)) or self.items.get(m.group(1).lower())
            if f is None:
                unknown.append(m.group(0))
                return REMOVED
            return f.display

        out = TOKEN.sub(sub, text)
        return DOUBLE_UNIT.sub(r"\1", out), unknown

    def render_any(self, obj: Any) -> tuple[Any, list[str]]:
        """The same for the strings inside a model's JSON answer (a dict of strings and lists of strings)."""
        unknown: list[str] = []
        if isinstance(obj, str):
            return self.render(obj)
        if isinstance(obj, list):
            out = []
            for x in obj:
                y, u = self.render_any(x)
                out.append(y)
                unknown += u
            return out, unknown
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                out[k], u = self.render_any(v)
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
        fs.add(f"{slug(f.id)}_act_now", f.cost.act_now, money(f.cost.act_now), f"{f.agent} finding {f.id}: cost of acting now")
        fs.add(f"{slug(f.id)}_saving", f.cost.expected_saving, money(f.cost.expected_saving), f"{f.agent} finding {f.id}: expected saving")
    return fs
