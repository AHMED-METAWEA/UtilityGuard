"""Savings advisor: opportunities that cut monthly cost, each a named formula over measured twin data and editable assumptions.

Measured inputs come from 30 days of baseline history (what normal operation looks like). Assumptions come from the
editable table. Every opportunity carries P10/P50/P90 from a Monte Carlo over its assumptions, so the owner sees a range,
not a promise. Nothing here is written by the LLM.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

import numpy as np

from app.cost import TONS_PER_MW, Assumptions, IncidentFacts, _a, _summary
from app.dashboard import History
from app.schemas import CalcInput, Opportunity, Reason, SavingRange, SavingsReport
from app.sim.engine import BG_LOSS, CAIRO
from app.sim.topology import COOLING_PLANTS, ZONE_SUBSTATION

if TYPE_CHECKING:
    from app.runtime import Runtime

RUNS, SPREAD = 600, 0.20
OFFICE_START, OFFICE_END = 8, 16  # government buildings are in use Sunday to Thursday, 08:00 to 16:00 Cairo time
SAVINGS_UNCERTAIN = {"water_price", "elec_tariff", "cooling_price", "setback_reduction", "pm_target_pressure", "pm_n1", "reset_delta_c",
                     "reset_pct_per_c", "planned_factor", "setback_invest", "pm_invest", "reset_invest", "clean_invest",
                     "crew_callout", "parts_pipe", "parts_pump", "parts_substation", "parts_chiller", "delay_hours", "late_multiplier"}


@dataclass
class Spec:
    id: str
    title: str
    network: str
    where: str
    summary: str
    fn: Callable[[Assumptions], float]  # EGP per month for a given set of assumptions
    keys: list[str]
    invest_key: str | None
    formula: Callable[[Assumptions], str]
    inputs: list[CalcInput]
    reasons: list[Reason]
    confidence: float


def _mean(xs) -> float:
    xs = list(xs)
    return float(sum(xs) / len(xs)) if xs else 0.0


def _failure_facts(rt: "Runtime", asset) -> IncidentFacts | None:
    g = rt.engine.g
    zt = lambda z: g.zone_index[z].type  # noqa: E731
    if asset.kind == "pump":
        return IncidentFacts(kind="pump_failure", zone_types={asset.zone_id: zt(asset.zone_id)})
    if asset.kind == "pipe":
        return IncidentFacts(kind="leak", zone_types={asset.zone_id: zt(asset.zone_id)}, leak_m3h=100.0)
    if asset.kind == "plant":
        served = COOLING_PLANTS[asset.id][2]
        deficit = rt.engine.metrics.get(f"plant_delivered:{asset.id}", 10.0) * 0.5 * TONS_PER_MW
        return IncidentFacts(kind="cooling_fault", zone_types={z: zt(z) for z in served[:2]}, cooling_deficit_tons=deficit)
    if asset.kind == "substation":
        zones = [z for z, s in ZONE_SUBSTATION.items() if s == asset.id]
        return IncidentFacts(kind="substation_trip", zone_types={z: zt(z) for z in zones},
                             unserved_mw=0.45 * sum(rt.engine.zone_demand[z][1] for z in zones))
    return None


def _specs(rt: "Runtime", rows: list[tuple[datetime, dict]], A: Assumptions) -> list[Spec]:
    g = rt.engine.g
    out: list[Spec] = []
    days = len(rows) / 24

    # 1. After-hours cooling setback in the government zones
    gov = [z.id for z in g.topology.zones if z.type == "government"]
    e_off = e_all = 0.0
    for t, r in rows:
        loc = t.astimezone(CAIRO)
        c = sum(r[f"demand:cooling:{z}"] for z in gov) * TONS_PER_MW
        e_all += c
        if loc.weekday() in (4, 5) or loc.hour < OFFICE_START or loc.hour >= OFFICE_END:
            e_off += c
    e_off_m = e_off * 30 / days
    out.append(Spec(
        "opp-setback", "After-hours cooling setback, government buildings", "cooling", "Government, Presidential and Justice districts",
        "Ministries run at office hours only, but cooling is delivered at the daytime setpoint all night and on Friday and Saturday. Raising the setpoint while the buildings are empty cuts that cooling.",
        lambda S: e_off_m * S["setback_reduction"] * S["cooling_price"], ["setback_reduction", "cooling_price"], "setback_invest",
        lambda S: f"{e_off_m:,.0f} ton-h × {S['setback_reduction']:.0%} × {S['cooling_price']:g} EGP/ton-h",
        [_a("E_offhours", e_off_m, "ton-h/month", "measured")],
        [Reason(text=f"{e_off_m:,.0f} ton-h of cooling a month is delivered outside office hours to the government zones ({e_off / e_all:.0%} of their cooling).", evidence_type="measured", evidence_ref="twin:demand:cooling"),
         Reason(text="Government buildings are used Sunday to Thursday, 08:00 to 16:00. Friday, Saturday and nights are off-hours.", evidence_type="rule", evidence_ref="rule:office-hours"),
         Reason(text=f"A setback cuts the off-hours cooling by {A['setback_reduction']:.0%}. This is an assumption you can change.", evidence_type="assumed", evidence_ref="assumption:setback_reduction")], 0.82))

    # 2. Night pressure management (FAVAD)
    night = [r for t, r in rows if t.astimezone(CAIRO).hour < 6]
    zones = [z.id for z in g.topology.zones]
    pumps = list(g.zone_pump.values())
    l0 = _mean(sum(r[f"demand:water:{z}"] for z in zones) * BG_LOSS / (1 + BG_LOSS) for r in night)
    p0 = _mean(_mean(r[f"{p}-pressure"] for p in pumps) for r in night)
    out.append(Spec(
        "opp-night-pressure", "Night pressure management", "water", "All districts",
        "Leakage grows with pressure. At night demand is low and pressure is high, so background leakage is at its worst. Reducing night pressure cuts it.",
        lambda S: max(0.0, l0 * (1 - (S["pm_target_pressure"] / p0) ** S["pm_n1"])) * 6 * 30 * S["water_price"],
        ["pm_target_pressure", "pm_n1", "water_price"], "pm_invest",
        lambda S: f"{l0:,.0f} m3/h × (1 − ({S['pm_target_pressure']:g} ÷ {p0:.2f})^{S['pm_n1']:g}) × 6 h × 30 days × {S['water_price']:g} EGP/m3",
        [_a("L0", l0, "m3/h", "measured"), _a("P0", p0, "bar", "measured")],
        [Reason(text=f"Background leakage between 00:00 and 06:00 is about {l0:,.0f} m3/h (water produced minus water billed).", evidence_type="measured", evidence_ref="twin:production-billed"),
         Reason(text=f"Pump outlets hold {p0:.2f} bar at night.", evidence_type="measured", evidence_ref="twin:pump-pressure"),
         Reason(text="Leakage scales with pressure as L1 = L0 × (P1 ÷ P0)^N1 (FAVAD). N1 and the target pressure are assumptions you can change.", evidence_type="rule", evidence_ref="rule:favad")], 0.7))

    # 3. Supply water temperature reset on mild days
    e_kwh = sum(r["kpi:cooling_elec_mw"] for _, r in rows) * 1000 * 30 / days
    mild = _mean(1.0 if r["kpi:ambient"] < 35 else 0.0 for _, r in rows)
    out.append(Spec(
        "opp-supply-reset", "Raise chilled-water supply temperature on mild days", "cooling", "DCP-01, DCP-02",
        "When the weather is mild the plants cool more than needed. A warmer supply setpoint lets the chillers work less.",
        lambda S: e_kwh * mild * S["reset_pct_per_c"] * S["reset_delta_c"] * S["elec_tariff"], ["reset_pct_per_c", "reset_delta_c", "elec_tariff"], "reset_invest",
        lambda S: f"{e_kwh:,.0f} kWh × {mild:.0%} mild hours × {S['reset_pct_per_c']:.1%} per °C × {S['reset_delta_c']:g} °C × {S['elec_tariff']:g} EGP/kWh",
        [_a("E_chillers", e_kwh, "kWh/month", "measured"), _a("mild_share", mild, "fraction", "measured")],
        [Reason(text=f"The chillers use {e_kwh:,.0f} kWh a month.", evidence_type="measured", evidence_ref="twin:chiller-power"),
         Reason(text=f"{mild:.0%} of hours are below 35 °C, when a warmer supply setpoint is safe.", evidence_type="measured", evidence_ref="twin:ambient"),
         Reason(text=f"Each °C of raise saves {A['reset_pct_per_c']:.1%} of chiller energy. This is an assumption you can change.", evidence_type="assumed", evidence_ref="assumption:reset_pct_per_c")], 0.7))

    # 4. Condenser cleaning at the plant with the worst efficiency
    plants = list(COOLING_PLANTS)
    stats = {}
    for p in plants:
        e = sum(r[f"kpi:plant_elec:{p}"] for _, r in rows)
        d = sum(r[f"kpi:plant_delivered:{p}"] for _, r in rows)
        stats[p] = (e * 1000 / (d * TONS_PER_MW) if d > 0 else 0.0, d * TONS_PER_MW * 30 / days)
    worst = max(plants, key=lambda p: stats[p][0])
    best = min(plants, key=lambda p: stats[p][0])
    k_w, trh = stats[worst]
    k_b = stats[best][0]
    name = {a.id: a.name for a in g.topology.assets}[worst]
    if k_w - k_b > 0.01:
        out.append(Spec(
            f"opp-clean-{worst}", f"Condenser cleaning at {name}", "cooling", name,
            f"{name} uses more electricity per ton of cooling than the other plant. Fouled condensers are the usual cause, and cleaning restores the efficiency.",
            lambda S: (k_w - k_b) * trh * S["elec_tariff"], ["elec_tariff"], "clean_invest",
            lambda S: f"({k_w:.3f} − {k_b:.3f}) kW/TR × {trh:,.0f} TR-h × {S['elec_tariff']:g} EGP/kWh",
            [_a("kW_per_TR_worst", k_w, "kW/TR", "measured"), _a("kW_per_TR_best", k_b, "kW/TR", "measured"), _a("TR_hours", trh, "TR-h/month", "measured")],
            [Reason(text=f"{name} runs at {k_w:.3f} kW/TR against {k_b:.3f} at the better plant.", evidence_type="measured", evidence_ref="twin:plant-efficiency"),
             Reason(text=f"It delivers {trh:,.0f} TR-h a month, so the gap costs real energy.", evidence_type="measured", evidence_ref="twin:plant-delivered"),
             Reason(text="The better plant is the clean reference. Load mix differences are not corrected for.", evidence_type="rule", evidence_ref="rule:reference-plant")], 0.75))

    # 5. Planned maintenance for the assets most likely to fail
    cands = []
    for a in g.topology.assets:
        st = rt.asset_status.get(a.id)
        if not st or st.failure_probability is None:
            continue
        f = _failure_facts(rt, a)
        if not f:
            continue
        n = _summary(f, A)
        cands.append((st.failure_probability * n["cost_of_delay"] - n["repair_now"] * A["planned_factor"], a, st.failure_probability, f))
    for net_gain, a, p, f in sorted(cands, key=lambda c: -c[0])[:3]:
        if net_gain <= 0:
            continue
        n0 = _summary(f, A)
        out.append(Spec(
            f"opp-pm-{a.id}", f"Planned maintenance for {a.name}", a.network, a.name,
            f"The failure model gives {a.name} a {p:.0%} chance of failing in the next 30 days. Fixing it on a planned date costs less than an emergency repair with the outage.",
            lambda S, a=a, p=p, f=f: p * _summary(f, S)["cost_of_delay"] - _summary(f, S)["repair_now"] * S["planned_factor"],
            ["planned_factor", "crew_callout", "delay_hours", "late_multiplier"], None,
            lambda S, a=a, p=p, f=f: f"{p:.0%} × {_summary(f, S)['cost_of_delay']:,.0f} EGP unplanned failure − {_summary(f, S)['repair_now'] * S['planned_factor']:,.0f} EGP planned repair",
            [_a("P_fail_30d", p, "probability", "forecast"), _a("C_unplanned", n0["cost_of_delay"], "EGP", "rule"), _a("C_planned", n0["repair_now"] * A["planned_factor"], "EGP", "rule")],
            [Reason(text=f"The failure model rates {a.name} at {p:.0%} risk over 30 days.", evidence_type="forecast", evidence_ref=f"ml:failure:{a.id}"),
             Reason(text=f"An unplanned failure costs {n0['cost_of_delay']:,.0f} EGP including losses and a late repair.", evidence_type="rule", evidence_ref="rule:cost-of-delay"),
             Reason(text=f"A planned repair costs {A['planned_factor']:.0%} of an emergency repair. This is an assumption you can change.", evidence_type="assumed", evidence_ref="assumption:planned_factor")], 0.6))
    return out


def build_savings(rt: "Runtime", hist: History, A: Assumptions) -> SavingsReport:
    rows = hist.hourly(rt.now, 30 * 24)
    specs = _specs(rt, rows, A)
    rng = np.random.default_rng(zlib.crc32(b"savings") ^ rt.seed)
    samples = {s.id: [] for s in specs}
    for _ in range(RUNS):
        S = A.sampled(rng, SPREAD, SAVINGS_UNCERTAIN)
        for s in specs:
            samples[s.id].append(s.fn(S))
    opps: list[Opportunity] = []
    for s in specs:
        p10, p50, p90 = (round(float(x)) for x in np.percentile(samples[s.id], [10, 50, 90]))
        base = max(0.0, s.fn(A))
        inv = A[s.invest_key] if s.invest_key else 0.0
        opps.append(Opportunity(
            id=s.id, title=s.title, network=s.network, where=s.where, summary=s.summary, monthly_saving=round(base),
            saving_range=SavingRange(p10=max(0, p10), p50=max(0, p50), p90=max(0, p90), runs=RUNS), investment=round(inv),
            payback_days=round(inv / (base / 30), 1) if base > 0 and inv > 0 else 0.0, formula=s.formula(A) + f" = {base:,.0f} EGP/month",
            inputs=s.inputs, reasons=s.reasons, assumptions=[A.item(k) for k in s.keys + ([s.invest_key] if s.invest_key else [])],
            confidence=s.confidence))
    opps.sort(key=lambda o: -o.monthly_saving)
    totals = np.sum([samples[s.id] for s in specs], axis=0) if specs else np.zeros(1)
    t10, t50, t90 = (round(float(x)) for x in np.percentile(totals, [10, 50, 90]))
    return SavingsReport(generated_at=datetime.now(timezone.utc), total_monthly=round(sum(o.monthly_saving for o in opps)),
                         total_investment=round(sum(o.investment for o in opps)), total_range=SavingRange(p10=t10, p50=t50, p90=t90, runs=RUNS),
                         opportunities=opps)

