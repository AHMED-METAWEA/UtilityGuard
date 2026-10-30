"""The early warning has to speak before the failure alarm, stay quiet when nothing is wrong, and not blame a pump for somebody else's fault."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.cost import Assumptions
from app.detection import Detector
from app.ml.earlywarn import CONSECUTIVE, EXCESS
from app.ml.service import MLService
from app.runtime import Runtime
from app.scenarios import events_for
from app.why import Why

T0 = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
A = Assumptions(path=Path("/nonexistent/assumptions.json"))


class World:
    def __init__(self, scenario: str | None, ticks: int, params: dict | None = None):
        self.rt = Runtime(42)
        self.det = Detector(self.rt)
        self.rt.detector = self.det
        self.mls = MLService(self.rt, self.det)
        self.mls.warnings.A = A
        self.rt.tick_hooks.append(self.det.tick)
        self.seen: list[tuple[int, str, str, str]] = []  # (tick, warning id, state, asset status)

        async def go():
            for i in range(60):
                await self.rt.tick_once(T0 + timedelta(seconds=2 * i))
            for ev in events_for(scenario, params or {}, "run-w") if scenario else []:
                self.rt.add_event(ev)
            for i in range(60, 60 + ticks):
                await self.rt.tick_once(T0 + timedelta(seconds=2 * i))
                for w in self.mls.warnings.listing():
                    st = self.rt.asset_status[w.asset_id].status
                    if not self.seen or self.seen[-1][1:3] != (w.id, w.state):
                        self.seen.append((i, w.id, w.state, st))

        asyncio.run(go())


@pytest.fixture(scope="module")
def wear():
    return World("scn-pump-wear", 360)


def test_a_quiet_twin_raises_nothing():
    w = World(None, 400)
    assert w.mls.warnings.listing() == []


def test_a_wearing_pump_is_flagged_while_it_still_runs_and_before_the_failure_alarm(wear):
    ws = wear.mls.warnings.listing()
    assert len(ws) == 1
    w = ws[0]
    first = next(s for s in wear.seen if s[1] == w.id)
    assert first[2] == "active" and first[3] in ("normal", "warning")  # raised while the pump was still delivering
    assert w.state == "failed" and w.lead_time_seconds and w.lead_time_seconds >= 120  # at least two minutes before the failure alarm
    alarm = next(a for a in wear.rt.alerts.values() if w.asset_id in a.asset_ids and a.title.endswith("failing"))
    assert w.created_at < alarm.detected_at


def test_the_warning_prices_the_choice_with_the_cost_model(wear):
    w = wear.mls.warnings.listing()[0]
    c = w.cost
    assert 0 < c.planned < c.unplanned and c.break_even == pytest.approx(c.planned / c.unplanned, abs=1e-4)
    assert c.expected_saving == pytest.approx(c.risk * c.unplanned - c.planned, abs=0.05)
    assert (c.verdict == "maintain_now") == (c.expected_saving > 0)
    assert w.excess >= EXCESS - 0.05 and len(w.series) >= CONSECUTIVE and w.signals[1].value > w.signals[1].peer_value


def test_every_figure_in_the_warning_text_is_one_of_its_numbers(wear):
    import re
    w = wear.mls.warnings.listing()[0]
    c = w.cost
    allowed = {round(c.planned), round(c.unplanned), round(w.risk * 100), round(w.peer_risk * 100), round(c.break_even * 100, 1), round(w.lead_time_seconds / 60, 1),
               *(round(s.value, 2) for s in w.signals), *(round(s.peer_value, 2) for s in w.signals)}
    for n in re.findall(r"\d[\d,]*\.?\d*", w.text.replace(w.asset_name, "")):
        assert round(float(n.replace(",", "")), 2) in allowed or float(n.replace(",", "")) in allowed, n


def test_why_explains_the_warning_and_the_risk_of_the_asset(wear):
    why = Why(wear.rt, A, wear.mls, lambda: [], lambda: None)
    w = wear.mls.warnings.listing()[0]
    rec = why.record(f"warning:{w.id}")
    assert rec.formula and rec.steps and any(i.key == "planned_factor" for i in rec.inputs) and rec.value == w.cost.expected_saving
    risk = why.record(f"asset-risk:{w.asset_id}")
    assert risk.evidence == "model" and len(risk.inputs) == 6 and any("synthetic" in n for n in risk.notes)


def test_a_pump_working_harder_because_a_main_burst_is_not_blamed():
    w = World("scn-pipe-burst", 400)
    assert w.mls.warnings.listing() == []


def test_a_pump_coming_back_after_a_power_cut_is_given_time_to_settle():
    w = World("scn-load-shedding", 400)
    assert w.mls.warnings.listing() == []
