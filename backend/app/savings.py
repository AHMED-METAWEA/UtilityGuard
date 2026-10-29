"""Savings advisor: opportunities that cut monthly cost, each a named formula over measured twin data and editable assumptions.

Measured inputs come from 30 days of baseline history (what normal operation looks like). Assumptions come from the
editable table. Every opportunity carries P10/P50/P90 from a Monte Carlo over its assumptions, so the owner sees a range,
not a promise. Nothing here is written by the LLM.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable

import numpy as np

from app.cost import TONS_PER_MW, Assumptions, IncidentFacts, _a, _ai, _compute, _summary, assumption_keys
from app.dashboard import History
from app.schemas import CalcInput, Calculation, Opportunity, Reason, SavingRange, SavingsReport
from app.sim.engine import CAIRO
from app.sim.topology import COOLING_PLANTS, ZONE_SUBSTATION

if TYPE_CHECKING:
    from app.runtime import Runtime

RUNS, SPREAD = 600, 0.20
OFFICE_START, OFFICE_END = 8, 16  # government buildings are in use Sunday to Thursday, 08:00 to 16:00 Cairo time


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
    cost_facts: Callable[[Assumptions], IncidentFacts] | None = None


def _mean(xs) -> float:
    xs = list(xs)
    return float(sum(xs) / len(xs)) if xs else 0.0


def _failure_facts(rt: "Runtime", asset, A: Assumptions) -> IncidentFacts | None:
    g = rt.engine.g
    proxy = dict(input_source="assumed", input_basis="Future failure planning proxy from the registered maintenance_* assumptions and current twin demand; verify against the asset contingency study. See the opportunity inputs and nested incident calculations.")
    zt = lambda z: g.zone_index[z].type  # noqa: E731
    if asset.kind == "pump":
        return IncidentFacts(kind="pump_failure", zone_types={asset.zone_id: zt(asset.zone_id)}, **proxy)
    if asset.kind == "pipe":
        return IncidentFacts(kind="leak", zone_types={asset.zone_id: zt(asset.zone_id)}, leak_m3h=A["maintenance_leak_m3h"], **proxy)
    if asset.kind == "plant":
        served = COOLING_PLANTS[asset.id][2]
        deficit = rt.engine.metrics.get(f"plant_delivered:{asset.id}", A["maintenance_cooling_fallback_mw"]) * A["maintenance_cooling_fraction"] * TONS_PER_MW
        return IncidentFacts(kind="cooling_fault", zone_types={z: zt(z) for z in served[:2]}, cooling_deficit_tons=deficit, **proxy)
    if asset.kind == "substation":
        zones = [z for z, s in ZONE_SUBSTATION.items() if s == asset.id]
        return IncidentFacts(kind="substation_trip", zone_types={z: zt(z) for z in zones},
                             unserved_mw=A["maintenance_unserved_fraction"] * sum(rt.engine.zone_demand[z][1] for z in zones), **proxy)
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
        [Reason(text=f"{e_off_m:,.0f} ton-h of cooling a month is delivered outside office hours to the government zones ({e_off / e_all if e_all else 0:.0%} of their cooling).", evidence_type="measured", evidence_ref="twin:demand:cooling"),
         Reason(text="Government buildings are used Sunday to Thursday, 08:00 to 16:00. Friday, Saturday and nights are off-hours.", evidence_type="rule", evidence_ref="rule:office-hours"),
         Reason(text=f"A setback cuts the off-hours cooling by {A['setback_reduction']:.0%}. This is an assumption you can change.", evidence_type="assumed", evidence_ref="assumption:setback_reduction")], 0.82))

    # 2. Night pressure management (FAVAD)
    night = [r for t, r in rows if t.astimezone(CAIRO).hour < 6]
    zones = [z.id for z in g.topology.zones]
    pumps = list(g.zone_pump.values())
    q_night = _mean(sum(r[f"demand:water:{z}"] for z in zones) for r in night)
    leakage = lambda S: q_night * S["background_loss_ratio"] / (1 + S["background_loss_ratio"])
    l0 = leakage(A)
    p0 = _mean(_mean(r[f"{p}-pressure"] for p in pumps) for r in night)
    out.append(Spec(
        "opp-night-pressure", "Night pressure management", "water", "All districts",
        "Leakage grows with pressure. At night demand is low and pressure is high, so background leakage is at its worst. Reducing night pressure cuts it.",
        lambda S: max(0.0, leakage(S) * (1 - (S["pm_target_pressure"] / p0) ** S["pm_n1"])) * 6 * 30 * S["water_price"],
        ["background_loss_ratio", "pm_target_pressure", "pm_n1", "water_price"], "pm_invest",
        lambda S: f"{leakage(S):,.4f} m3/h × (1 − ({S['pm_target_pressure']:g} ÷ {p0:.2f})^{S['pm_n1']:g}) × 6 h × 30 days × {S['water_price']:g} EGP/m3",
        [_a("Q_night", q_night, "m3/h", "measured"), _a("L0", l0, "m3/h", "rule", "Q_night × background_loss_ratio / (1 + background_loss_ratio); synthetic production includes the assumed leakage ratio."), _a("P0", p0, "bar", "measured"), _a("night_hours", 6, "h/day", "rule", "Planning pressure-control window 00:00–06:00 Cairo time; verify against the pressure-management operating procedure."), _a("days_per_month", 30, "days", "rule", "Fixed 30-day appraisal month, matching the baseline history window.")],
        [Reason(text=f"Background leakage between 00:00 and 06:00 is about {l0:,.0f} m3/h (estimated from the planning background-loss ratio).", evidence_type="assumed", evidence_ref="assumption:background_loss_ratio"),
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
        f = _failure_facts(rt, a, A)
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
            lambda S, a=a, p=p: p * _summary(_failure_facts(rt, a, S), S)["cost_of_delay"] - _summary(_failure_facts(rt, a, S), S)["repair_now"] * S["planned_factor"],
            ["planned_factor"] + assumption_keys(f, A) + maintenance_keys(a), None,
            lambda S, a=a, p=p: f"{p:g} × {_summary(_failure_facts(rt, a, S), S)['cost_of_delay']:g} EGP unplanned failure − {_summary(_failure_facts(rt, a, S), S)['repair_now'] * S['planned_factor']:g} EGP planned repair",
            [_a("P_fail_30d", p, "probability", "forecast"), _a("C_unplanned", n0["cost_of_delay"], "EGP", "rule"), _a("C_planned", n0["repair_now"] * A["planned_factor"], "EGP", "rule")],
            [Reason(text=f"The failure model rates {a.name} at {p:.0%} risk over 30 days.", evidence_type="forecast", evidence_ref=f"ml:failure:{a.id}"),
             Reason(text=f"An unplanned failure costs {n0['cost_of_delay']:,.0f} EGP including losses and a late repair.", evidence_type="rule", evidence_ref="rule:cost-of-delay"),
             Reason(text=f"A planned repair costs {A['planned_factor']:.0%} of an emergency repair. This is an assumption you can change.", evidence_type="assumed", evidence_ref="assumption:planned_factor")], 0.6, lambda S, a=a: _failure_facts(rt, a, S)))
    for spec in out:
        spec.inputs += [
            _a("history_hours", len(rows), "h", "rule", "Hourly samples from the synthetic baseline history, without injected incidents; operational use requires metered baseline validation."),
            _a("days_per_month", 30, "days", "rule", "Fixed 30-day appraisal month; hourly history is scaled by 30 / (history_hours / 24)."),
        ]
        if spec.id == "opp-setback":
            spec.inputs += [
                _a("office_start", OFFICE_START, "hour", "assumed", "Planning Sunday–Thursday office schedule starts at 08:00 Cairo time; verify against district occupancy/BMS schedules."),
                _a("office_end", OFFICE_END, "hour", "assumed", "Planning office schedule ends at 16:00; Friday/Saturday and other hours are off-hours. Verify against district occupancy/BMS schedules."),
                _a("tons_per_mw", TONS_PER_MW, "TR/MW thermal", "rule", "Engineering unit conversion rounded to 284.3 refrigeration tons per MW thermal (one TR is approximately 3.517 kW)."),
            ]
        if spec.id == "opp-supply-reset":
            spec.inputs += [_a("mild_threshold", 35, "°C", "assumed", "Planning reset eligibility threshold; verify against OEM performance and comfort commissioning data.")]
    return out


def maintenance_keys(asset) -> list[str]:
    return {"pipe": ["maintenance_leak_m3h"], "pump": [], "substation": ["maintenance_unserved_fraction"],
            "plant": ["maintenance_cooling_fraction", "maintenance_cooling_fallback_mw"]}[asset.kind]


def financials(monthly: float, investment: float, rate: float) -> tuple[float | None, float]:
    """Simple payback and NPV of 36 equal month-end net savings, effective annual discount rate."""
    monthly_rate = (1 + rate) ** (1 / 12) - 1
    npv = sum(monthly / (1 + monthly_rate) ** month for month in range(1, 37)) - investment
    return (investment / monthly if monthly > 0 else None), npv


def _monthly_formula(s: Spec) -> str:
    if s.id == "opp-setback":
        return "E_offhours × setback_reduction × cooling_price"
    if s.id == "opp-night-pressure":
        return "max(0, Q_night × background_loss_ratio / (1 + background_loss_ratio) × (1 − (pm_target_pressure / P0)^pm_n1)) × night_hours × days_per_month × water_price"
    if s.id == "opp-supply-reset":
        return "E_chillers × mild_share × reset_pct_per_c × reset_delta_c × elec_tariff"
    if s.id.startswith("opp-clean-"):
        return "(kW_per_TR_worst − kW_per_TR_best) × TR_hours × elec_tariff"
    return "P_fail_30d × C_unplanned − C_planned"


def _opportunity(s: Spec, A: Assumptions, saving_range: SavingRange) -> Opportunity:
    base = max(0.0, s.fn(A))
    inv = A[s.invest_key] if s.invest_key else 0.0
    payback, npv = financials(base, inv, A["discount_rate"])
    keys = list(dict.fromkeys(s.keys + ([s.invest_key] if s.invest_key else []) + ["discount_rate"]))
    inputs = s.inputs + [_ai(A, k) for k in keys]
    calcs = []
    if s.cost_facts:
        calcs += _compute(s.cost_facts(A), A, s.id, s.id).explanation.calculations
    calcs += [
        Calculation(name="Monthly saving", formula="max(0, " + _monthly_formula(s) + ")", inputs=inputs, result=round(base), unit="EGP/month"),
        Calculation(name="Upfront investment", formula=s.invest_key or "0 (planned repair is recurring monthly expenditure)",
                    inputs=[_ai(A, s.invest_key)] if s.invest_key else [_a("upfront", 0, "EGP", "rule", "Maintenance expenditure is already subtracted in the monthly expected net benefit.")], result=inv),
        Calculation(name="Simple payback", formula="investment / monthly_net_saving",
                    inputs=[_a("investment", inv, "EGP", "rule"), _a("monthly_net_saving", base, "EGP/month", "rule")],
                    result=round(payback, 4) if payback is not None else 0, unit="months"),
        Calculation(name="Three-year NPV", formula="sum(monthly_net_saving / (1 + discount_rate)^(month/12), month=1..36) − investment",
                    inputs=[_a("monthly_net_saving", base, "EGP/month", "rule"), _a("investment", inv, "EGP", "rule"),
                            _ai(A, "discount_rate"), _a("months", 36, "months", "rule", "Three-year appraisal horizon, 12 months per year; constant month-end savings, no inflation, tax or terminal value.")], result=round(npv, 2)),
    ]
    if payback is None:
        calcs = [c for c in calcs if c.name != "Simple payback"]
    return Opportunity(
        id=s.id, title=s.title, network=s.network, where=s.where, summary=s.summary, monthly_saving=round(base),
        saving_range=saving_range, investment=round(inv), payback_days=round(payback * 30, 1) if payback is not None else None,
        payback_months=round(payback, 4) if payback is not None else None, npv3_years=round(npv, 2),
        formula=s.formula(A) + f" = {base:,.0f} EGP/month", calculations=calcs,
        inputs=inputs, reasons=s.reasons, assumptions=[A.item(k) for k in keys], confidence=s.confidence)


def build_savings(rt: "Runtime", hist: History, A: Assumptions) -> SavingsReport:
    rows = hist.hourly(rt.now, 30 * 24)
    specs = _specs(rt, rows, A)
    rng = np.random.default_rng(zlib.crc32(b"savings") ^ rt.seed)
    samples = {s.id: [] for s in specs}
    keys = sorted({k for s in specs for k in s.keys})
    for _ in range(RUNS):
        S = A.sampled(rng, SPREAD, keys)
        for s in specs:
            samples[s.id].append(max(0.0, s.fn(S)))
    opps: list[Opportunity] = []
    for s in specs:
        p10, p50, p90 = (round(float(x)) for x in np.percentile(samples[s.id], [10, 50, 90]))
        opps.append(_opportunity(s, A, SavingRange(p10=p10, p50=p50, p90=p90, runs=RUNS)))
    opps.sort(key=lambda o: -o.monthly_saving)
    totals = np.sum([samples[s.id] for s in specs], axis=0) if specs else np.zeros(1)
    t10, t50, t90 = (round(float(x)) for x in np.percentile(totals, [10, 50, 90]))
    return SavingsReport(generated_at=rt.now, total_monthly=sum(o.monthly_saving for o in opps),
                         total_investment=sum(o.investment for o in opps), total_range=SavingRange(p10=t10, p50=t50, p90=t90, runs=RUNS),
                         opportunities=opps)
