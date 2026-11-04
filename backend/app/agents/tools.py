"""Deterministic tools the agents call. They return facts and numbers; the LLM never makes numbers."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import TYPE_CHECKING

from app.cost import Assumptions, IncidentFacts, compute
from app.schemas import CalculationRecord
from app.sim.engine import Event
from app.sim.topology import COOLING_PLANTS, ZONE_SUBSTATION

if TYPE_CHECKING:
    from app.runtime import Runtime

PLAYBOOKS = {
    "leak": ("pipe repair", "immediate", "Isolate the damaged segment with the nearest valves and send a pipe repair crew"),
    "pump_failure": ("mechanical", "immediate", "Switch to the standby pump and send a mechanical crew to inspect bearings and impeller"),
    "substation_trip": ("electrical", "immediate", "Send the electrical crew to inspect and re-close the breaker, and keep critical loads on the backup substation"),
    "cooling_fault": ("HVAC", "within_hours", "Start the standby chiller and send an HVAC crew to the plant"),
    "energy_waste": ("operations", "within_hours", "Pre-cool buildings off-peak and shift flexible load to other substations"),
    "sensor_fault": ("instrumentation", "scheduled", "Mark the sensor as suspect, use its neighbours, and schedule recalibration"),
}
# What usually lies behind each kind of fault. The data cannot always say which one it is, so these are hypotheses for the crew to confirm, never findings: a cause is where the
# fault comes from, not what it does (pumps stopping, load moving to a backup substation, pressure falling are consequences and never go in this list).
CAUSES = {
    "leak": ["A joint or section of the main has ruptured: pressure fell while more water goes in than comes out", "Third-party damage or ground movement at the section (to be confirmed on site)"],
    "pump_failure": ["Wear of the bearings or the impeller (to be confirmed on site)", "Blocked suction or cavitation at the pump (to be confirmed on site)"],
    "substation_trip": ["A protection trip on the substation, for example overcurrent or an earth fault (to be confirmed by the crew)", "Loss of the upstream supply or a planned cut by the grid operator (to be confirmed with the operator)"],
    "cooling_fault": ["A chiller has tripped or lost capacity while its substation is energised (to be confirmed on site)", "Fouling or low refrigerant reducing its output (to be confirmed on site)"],
    "energy_waste": ["Cooling load concentrated on this substation at peak hours", "Flexible load not shifted to the other substations"],
    "sensor_fault": ["The sensor is drifting, stuck or failing, so its reading is not the truth (its neighbours disagree)"],
}
# The same playbooks and hypotheses in Arabic: same meaning, same caution ("to be confirmed"), nothing added.
PLAYBOOK_ACTION_AR = {
    "leak": "اعزل الجزء التالف بأقرب محابس وأرسل فريق إصلاح مواسير",
    "pump_failure": "حوّل إلى المضخة الاحتياطية وأرسل فريقًا ميكانيكيًا لفحص المحامل والريشة",
    "substation_trip": "أرسل فريق الكهرباء لفحص القاطع وإعادة إغلاقه، وأبقِ الأحمال الحرجة على المحطة الاحتياطية",
    "cooling_fault": "شغّل المبرّد الاحتياطي وأرسل فريق تكييف إلى المحطة",
    "energy_waste": "برّد المباني مسبقًا خارج ساعات الذروة وانقل الحمل المرن إلى محطات أخرى",
    "sensor_fault": "ضع علامة اشتباه على الحساس واعتمد على قراءات جيرانه وحدّد موعدًا لإعادة معايرته",
}
CAUSES_AR = {
    "leak": ["انفجار في وصلة أو في جزء من الماسورة الرئيسية: الضغط هبط بينما يدخل ماء أكثر مما يخرج", "ضرر من طرف ثالث أو حركة أرض عند الجزء (يؤكَّد في الموقع)"],
    "pump_failure": ["تآكل المحامل أو الريشة (يؤكَّد في الموقع)", "انسداد في السحب أو تكهّف عند المضخة (يؤكَّد في الموقع)"],
    "substation_trip": ["فصل حماية على المحطة، مثل زيادة تيار أو عطل أرضي (يؤكّده الفريق)", "فقدان التغذية من المنبع أو قطع مخطط من مشغّل الشبكة (يؤكَّد مع المشغّل)"],
    "cooling_fault": ["فصل أحد المبرّدات أو فقد جزء من قدرته بينما محطته تعمل بالكهرباء (يؤكَّد في الموقع)", "ترسبات أو نقص في غاز التبريد يقلل الإنتاج (يؤكَّد في الموقع)"],
    "energy_waste": ["حمل التبريد متركّز على هذه المحطة في ساعات الذروة", "الحمل المرن لم يُنقل إلى المحطات الأخرى"],
    "sensor_fault": ["الحساس منحرف أو عالق أو معطّل، فقراءته ليست الحقيقة (جيرانه تخالفه)"],
}
URGENCY_TEXT_AR = {"immediate": "فوري", "within_hours": "خلال ساعات", "scheduled": "مجدول"}
AGENT_FOR_KIND = {"leak": "water", "pump_failure": "water", "substation_trip": "electricity",
                  "cooling_fault": "cooling", "energy_waste": "electricity", "sensor_fault": "water"}


def read_sensors(rt: "Runtime", asset_ids: list[str]) -> list[dict]:
    g = rt.engine.g
    out = []
    for s in g.topology.sensors:
        if s.asset_id in asset_ids and s.id in rt.latest:
            h = [x for x in rt.history.get(s.id, [])[-30:] if x == x]
            out.append({"sensor": s.id, "kind": s.kind, "unit": s.unit, "now": round(rt.latest[s.id].value or 0, 2),
                        "min30": round(min(h), 2) if h else None, "max30": round(max(h), 2) if h else None,
                        "normal": [s.normal_range.min, s.normal_range.max], "quality": rt.latest[s.id].quality})
    return out


def impacted(rt: "Runtime", root: str) -> dict:
    """Cross-network dependants of an asset, from the topology links."""
    g = rt.engine.g
    zones = [z for z, s in ZONE_SUBSTATION.items() if s == root] or [g.asset_index[root].zone_id]
    return {
        "zones": zones,
        "pumps": [g.zone_pump[z] for z in zones] if root.startswith("elec-sub") else [],
        "plants": [p for p, (_, home, _) in COOLING_PLANTS.items() if home in zones] if root.startswith("elec-sub") else [],
        "cooled_zones": COOLING_PLANTS[root][2] if root in COOLING_PLANTS else [],
    }


ASSET_PATTERNS = [
    (re.compile(r"\b(?:ss|substation)[\s-]*0?(\d)\b", re.I), "elec-sub-0{}"),
    (re.compile(r"\b(?:p|pump)[\s-]*0?(\d)\b", re.I), "water-pump-0{}"),
    (re.compile(r"\b(?:dcp|plant|chiller)[\s-]*0?(\d)\b", re.I), "cool-plant-0{}"),
]


def find_asset(text: str, rt: "Runtime") -> str | None:
    for pat, fmt in ASSET_PATTERNS:
        m = pat.search(text)
        if m and fmt.format(m.group(1)) in rt.engine.g.asset_index:
            return fmt.format(m.group(1))
    return None


async def what_if(rt: "Runtime", asset_id: str, A: Assumptions) -> tuple[IncidentFacts | None, CalculationRecord | None, dict]:
    """Run the scenario on a sandbox copy of the twin and return the detected facts and the cost record."""
    kind = {"substation": "substation_trip", "pump": "pump_failure", "plant": "cooling_fault", "pipe": "pipe_burst"}[
        rt.engine.g.asset_index[asset_id].kind]

    def run() -> tuple[IncidentFacts | None, dict]:
        sb = rt.clone()
        det = rt.detector.clone(sb)
        found: list = []
        det.on_incident.append(lambda inc, f, root: found.append((f, root)))
        sb.tick_hooks.append(det.tick)
        sb.engine.add_event(Event(kind=kind, target=asset_id, severity=0.7, run_id="what-if"))

        async def go():
            for i in range(35):
                await sb.tick_once(rt.now + timedelta(seconds=2 * (i + 1)))
        asyncio.run(go())
        before = {s: rt.latest[s].value for s in rt.latest}
        after = {s: sb.latest[s].value for s in sb.latest}
        deltas = {s: round(after[s] - before[s], 2) for s in after if before.get(s) is not None and after[s] is not None}
        facts = next((f for f, r in found if r == asset_id), found[0][0] if found else None)
        return facts, deltas

    facts, deltas = await asyncio.to_thread(run)
    record = compute(facts, A, "what-if") if facts else None
    return facts, record, deltas
