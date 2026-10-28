"""Extras (spec 11): incident PDF, with/without intervention comparison, sensor placement, drift."""

from __future__ import annotations

import asyncio
import io
import math
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas import CompareSide, Compare, DriftIndicator, ForecastPoint, LatLng, PlacementSuggestion
from app.scenarios import BY_ID, events_for

if TYPE_CHECKING:
    from app.agents.service import AgentService
    from app.runtime import Runtime

KEY_SENSOR = {"scn-pipe-burst": "{target}-pressure", "scn-pump-failure": "{target}-pressure",
              "scn-substation-trip": "water-pump-01-pressure", "scn-cooling-fault": "{target}-supply-temp",
              "scn-stuck-sensor": "{target}", "scn-heat-wave": "elec-sub-02-load", "scn-holiday": "elec-sub-01-load"}


# ---------- PDF ----------
def incident_pdf(rt: "Runtime", incident_id: str) -> bytes:
    inc = rt.incidents[incident_id]
    ss = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"UtilityGuard incident {inc.id}")
    navy, line = colors.HexColor("#0B1630"), colors.HexColor("#C9D2E3")
    st = [Paragraph("UtilityGuard Twin, incident report", ss["Title"]),
          Paragraph(f"<b>{inc.title}</b> ({inc.id}). Severity {inc.severity}, state {inc.state}. Opened {inc.opened_at:%Y-%m-%d %H:%M} UTC. "
                    f"Networks: {', '.join(inc.networks)}. Zones: {', '.join(rt.engine.g.zone_index[z].name for z in inc.zone_ids)}.", ss["Normal"]),
          Spacer(1, 6)]

    def table(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("GRID", (0, 0), (-1, -1), 0.4, line),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")] +
                              ([("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5)] if header else [])))
        return t

    p = lambda x: Paragraph(str(x), ss["BodyText"])  # noqa: E731
    st += [Paragraph("Alerts", ss["Heading2"]),
           table([["Alert", "Severity", "Title", "Detected", "Confidence"]] +
                 [[a.id, a.severity, p(a.title), f"{a.detected_at:%H:%M:%S}", f"{a.confidence:.0%}"] for a in (rt.alerts[x] for x in inc.alert_ids)],
                 [24 * mm, 20 * mm, 80 * mm, 24 * mm, 22 * mm])]
    for fid in inc.finding_ids:
        f = rt.findings[fid]
        c, e = f.cost, f.explain
        st += [Paragraph(f"{f.agent.capitalize()} agent: {f.headline}", ss["Heading2"]), p(f.explanation), Spacer(1, 4),
               p(f"<b>Recommended action:</b> {f.recommended_action.text} (crew: {f.recommended_action.crew_type}, urgency: {f.recommended_action.urgency})"),
               Paragraph("Why this decision", ss["Heading3"])]
        st += [p(f"{i}. {r.text} <font color='#667'>({r.evidence_type})</font>") for i, r in enumerate(e.reasons, 1)]
        st += [Paragraph("Cost: act now versus delay", ss["Heading3"]),
               table([["Act now", f"Delay {c.delay_hours:g} h", "Saving", "Confidence"],
                      [f"{c.act_now:,.0f} EGP", f"{c.cost_of_delay:,.0f} EGP", f"{c.expected_saving:,.0f} EGP", f"{e.confidence:.0%}"]],
                     [42 * mm] * 4),
               Paragraph("Calculations", ss["Heading3"]),
               table([["Name", "Formula", "Inputs", "Result"]] +
                     [[p(k.name), p(k.formula), p("; ".join(f"{i.name} = {i.value:,.4g} {i.unit} ({i.source})" for i in k.inputs)),
                       f"{k.result:,.0f} {k.unit}"] for k in e.calculations], [34 * mm, 42 * mm, 72 * mm, 26 * mm]),
               Paragraph("Assumptions", ss["Heading3"]),
               table([["Assumption", "Value", "Source"]] + [[p(a.name), f"{a.value:,.4g} {a.unit}", p(a.source + (" (changed)" if a.user_changed else ""))]
                                                          for a in e.assumptions], [80 * mm, 40 * mm, 54 * mm]),
               Paragraph("Sensitivity", ss["Heading3"]),
               p("; ".join(f"{s.input} {s.change_percent:+.0f}% gives a saving of {s.new_saving:,.0f} EGP" for s in e.sensitivity)),
               Paragraph("Agent steps", ss["Heading3"])]
        st += [p(f"{i}. {s}") for i, s in enumerate(f.reasoning_steps, 1)]
    st += [Spacer(1, 8), Paragraph("All figures are computed by the UtilityGuard cost model from the listed formulas and assumptions. "
                                   "The network is synthetic and values are illustrative.", ss["Italic"])]
    doc.build(st)
    return buf.getvalue()


# ---------- compare ----------
async def compare(rt: "Runtime", svc: "AgentService", scenario_id: str) -> Compare:
    sc = BY_ID[scenario_id]
    params = {x.name: x.default for x in sc.parameters}
    target = params.get("targetAsset") or params.get("targetSensor") or ""
    sensor = KEY_SENSOR[scenario_id].format(target=target)

    def run(intervene_at: int | None) -> tuple[list[float], list, object]:
        sb = rt.clone()
        det = svc.detector.clone(sb)
        found = []
        det.on_incident.append(lambda inc, f, root: found.append(f))
        sb.tick_hooks.append(det.tick)
        for ev in events_for(scenario_id, params, "cmp"):
            sb.add_event(ev)
        series = []

        async def go():
            for i in range(60):
                if intervene_at is not None and i == intervene_at:
                    sb.engine.clear_events("cmp")
                await sb.tick_once(rt.now + timedelta(seconds=2 * (i + 1)))
                series.append(sb.latest[sensor].value or 0.0)
        asyncio.run(go())
        return series, found, sb

    (s_without, found, _), (s_with, _, _) = await asyncio.gather(asyncio.to_thread(run, None), asyncio.to_thread(run, 20))
    from app.cost import compute
    facts = found[0] if found else None
    rec = compute(facts, svc.A, "compare") if facts else None
    t_delay = svc.A["delay_hours"]
    respond = 1.0  # hours for the crew to arrive and isolate when sent now
    leak = facts.leak_m3h if facts else 0.0
    cost_without = rec.cost.cost_of_delay if rec else 0.0
    cost_with = (rec.cost.act_now + rec.cost.delay_per_hour * respond) if rec else 0.0
    # 60 ticks of simulated response are shown on a compressed clock: 1 tick = t_delay / 60 hours
    step_h = t_delay / 60

    def pts(series):
        return [ForecastPoint(timestamp=rt.now + timedelta(hours=i * step_h), predicted=round(v, 2), lower=round(v, 2), upper=round(v, 2))
                for i, v in enumerate(series)]

    return Compare(scenario_id=scenario_id,
                   without_intervention=CompareSide(label=f"No action for {t_delay:g} h", total_cost=round(cost_without),
                                                    water_lost_m3=round(leak * t_delay), outage_hours=t_delay, series=pts(s_without)),
                   with_intervention=CompareSide(label="Crew sent now", total_cost=round(cost_with), water_lost_m3=round(leak * respond),
                                                 outage_hours=respond, series=pts(s_with)),
                   saving=round(cost_without - cost_with))


# ---------- sensor placement ----------
def _km(a: LatLng, b: LatLng) -> float:
    return math.hypot((a.lat - b.lat) * 111.0, (a.lng - b.lng) * 111.0 * math.cos(math.radians(a.lat)))


def sensor_placement(rt: "Runtime", top: int = 4) -> list[PlacementSuggestion]:
    """Where a new sensor shortens the longest unmonitored stretch most.

    Leak localisation error scales with the distance between pressure sensors, so the
    improvement is the share of network length whose worst-case gap the new sensor halves."""
    g = rt.engine.g
    out = []
    total = sum(_km(a.geometry.coordinates[i], a.geometry.coordinates[i + 1])
                for a in g.topology.assets if a.geometry.type == "line" for i in range(len(a.geometry.coordinates) - 1))
    for a in g.topology.assets:
        if a.geometry.type != "line" or a.network not in ("water", "cooling"):
            continue
        pts = a.geometry.coordinates
        sensed = [s.position for s in g.topology.sensors if s.asset_id == a.id]
        for i in range(len(pts) - 1):
            seg = _km(pts[i], pts[i + 1])
            mid = LatLng(lat=(pts[i].lat + pts[i + 1].lat) / 2, lng=(pts[i].lng + pts[i + 1].lng) / 2)
            gap = min((_km(mid, s) for s in sensed), default=seg)
            score = seg * gap
            out.append((score, seg, gap, a, mid))
    out.sort(key=lambda x: -x[0])
    res = []
    for score, seg, gap, a, mid in out[:top]:
        kind = "pressure" if a.network == "water" else "temperature"
        res.append(PlacementSuggestion(position=mid, near_asset_id=a.id, network=a.network, kind=kind,
                                       detection_improvement=round(min(0.3, seg / max(total, 1e-6) * 2 + gap / 10), 3),
                                       reason=f"{seg:.2f} km segment of {a.name}; nearest sensor is {gap:.2f} km away"))
    return res


# ---------- drift ----------
def drift(rt: "Runtime", window: int = 30) -> list[DriftIndicator]:
    """Twin versus reality: mean deviation of each sensor from the shadow twin's expected value."""
    g = rt.engine.g
    out = []
    for s in g.topology.sensors:
        h = rt.history.get(s.id, [])[-window:]
        exp = rt.forecasts.get(s.id)
        if not h or exp is None:
            continue
        dev = float(np.nanmean(h)) - exp
        span = max(s.normal_range.max - s.normal_range.min, 1e-6)
        flat = len(h) >= 10 and float(np.nanstd(h[-10:])) < 1e-9
        rel = abs(dev) / span
        status = "drifting" if rel > 0.25 or flat else "watch" if rel > 0.08 else "ok"
        out.append(DriftIndicator(sensor_id=s.id, drift=round(dev, 3), unit=s.unit, status=status))
    out.sort(key=lambda d: {"drifting": 0, "watch": 1, "ok": 2}[d.status])
    return out
