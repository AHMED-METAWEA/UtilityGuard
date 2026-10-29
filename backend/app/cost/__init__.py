"""Deterministic cost and explanation engine.

    C_delay     = C_loss + C_disruption + C_secondary             (EGP/h)
    ActNow      = C_repair_now + C_delay × t_response
    CostOfDelay = C_delay × t_delay + C_repair_now × late_multiplier
    Saving      = CostOfDelay − ActNow
Each consequence has one owner. VoLL owns customer economic outage harm;
disruption owns utility notification/support only; secondary owns extra purchases.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.schemas import (
    Assumption, CalcInput, Calculation, CalculationRecord, Cost, CostItem, Explanation, Reason, SavingRange, Sensitivity, Tornado,
)
from app.cost.references import NOT_FOUND, REFERENCES, evidence
from app.sim.topology import ZONE_CUSTOMERS

DATA = Path(__file__).resolve().parents[2] / "data"
ASSUMPTIONS_FILE = DATA / "assumptions.json"
REVIEWED = "2026-10-29"


def _default(name, value, unit, low, high, basis, status="planning") -> dict:
    return dict(name=name, value=value, unit=unit, low=round(low, 12), high=round(high, 12), basis=basis, status=status, reviewed=REVIEWED)


DEFAULTS = {
    "water_price": _default("Water production cost", 8.0, "EGP/m3", 6, 10,
        "Illustrative marginal treatment and delivery cost, not a verified Egyptian tariff. Verify against the utility's water production cost ledger and supply contract."),
    "elec_tariff": _default("Electricity tariff", 1.6, "EGP/kWh", 1.2, 2.0,
        "Illustrative purchased-energy rate; Egyptian tariffs cannot be verified offline. Verify against the current electricity supply contract and invoices."),
    "cooling_price": _default("Cooling energy cost", 1.5, "EGP/ton-h", 1.1, 1.9,
        "Illustrative marginal cooling delivery cost. Verify against the district cooling operating cost ledger, avoiding retail revenue and energy-cost overlap."),
    "crew_callout": _default("Crew call-out cost", 3400.0, "EGP/dispatch", 2500, 4300,
        "Budget allowance for transport and emergency labour per dispatch. Verify against crew timesheets and the maintenance contractor rate card."),
    "parts_pipe": _default("Pipe repair parts", 15000.0, "EGP/repair", 10000, 20000,
        "Budget for one district-main joint/section repair. Verify against the pipe bill of quantities and recent supplier quotations."),
    "parts_pump": _default("Pump repair parts", 42000.0, "EGP/repair", 30000, 54000,
        "Budget for a pump mechanical/electrical repair. Verify against the pump asset bill of materials and maintenance work orders."),
    "parts_substation": _default("Substation restore and inspection", 26000.0, "EGP/repair", 18000, 34000,
        "Budget for reset, inspection and minor switchgear work, not transformer replacement. Verify against substation restoration work orders."),
    "parts_chiller": _default("Chiller repair parts", 58000.0, "EGP/repair", 40000, 76000,
        "Budget for a minor chiller component repair. Verify against OEM service quotations and plant work orders."),
    "parts_sensor": _default("Sensor recalibration or replacement", 2400.0, "EGP/repair", 1600, 3200,
        "Budget for a field sensor replacement or calibration. Verify against the instrument register and calibration contractor rate card."),
    "response_hours": _default("Crew response time when sent now", 1.0, "h", 0.5, 1.5,
        "One hour is a district dispatch-to-isolation planning allowance. Verify against dispatch logs and the emergency response SLA."),
    "isolate_loss_reduction": _default("Ongoing loss cut after pipe isolation", 0.85, "fraction", 0.7, 0.95,
        "Planning allowance: isolation cuts lost-water cost only by 85% after crew arrival until the existing deferred horizon; disruption and secondary purchases continue unchanged. Repair uses planned_factor without damage escalation, plus one extra isolation call-out. Verify against valve isolation plans, post-isolation flows and planned work orders."),
    "late_multiplier_cap": _default("Maximum deferred repair multiplier", 3.0, "×", 2.0, 4.0,
        "Linear escalation m(w) = min(cap, 1 + (late_multiplier_kind - 1) * w / delay_hours_kind) is a planning choice, with a default cap of three times repair cost. Waiting includes losses to w with no additional response hours. This is not a fitted damage-growth law; verify against matched early and deferred repair work orders."),
    "false_dispatch": _default("Cost of a crew sent on a false alarm", 3400.0, "EGP/dispatch", 2500, 4300,
        "One unnecessary visit uses the same budget as an emergency crew dispatch. Verify against false-alarm work orders and contractor invoices."),
    "false_dispatch_interval": _default("Interval between false dispatches", 4.0, "h/dispatch", 2, 6,
        "One false dispatch per four unresolved sensor-fault hours is an illustrative frequency, not observed evidence. Verify against alarm and dispatch history."),
    "setback_reduction": _default("Off-hours cooling cut by a setback", 0.35, "fraction", 0.25, 0.45,
        "A 35% off-hours reduction is a commissioning target. Verify with occupancy schedules, BMS trends and a setback measurement-and-verification trial."),
    "setback_invest": _default("Setback commissioning in the building systems", 25000.0, "EGP", 18000, 32000,
        "Budget for BMS scheduling and commissioning. Verify against the controls contractor scope and quotation."),
    "pm_target_pressure": _default("Night pressure target after pressure management", 3.4, "bar", 3.0, 3.8,
        "Trial target chosen below normal night pressure while retaining service head. Verify against the hydraulic model and minimum customer pressure SLA."),
    "pm_n1": _default("Leakage-pressure exponent N1 (FAVAD)", 1.0, "×", 0.5, 1.5,
        "The FAVAD power-law approximation uses a midpoint exponent of one. Calibrate the 0.5–1.5 benchmark range with district pressure-step tests and the leakage study.", "benchmark"),
    "pm_invest": _default("Pressure-reducing valves and controllers", 140000.0, "EGP", 100000, 180000,
        "Budget for pressure-control hardware and installation. Verify against the valve schedule and supplier installation quotations."),
    "reset_delta_c": _default("Supply water temperature raise on mild days", 1.0, "°C", 0.5, 1.5,
        "One degree is a trial control reset. Verify against OEM operating limits and building comfort commissioning records."),
    "reset_pct_per_c": _default("Chiller energy saved per °C of raise", 0.025, "fraction/°C", 0.02, 0.03,
        "A 2–3% energy reduction per degree is an illustrative engineering benchmark, not a physical law. Verify against OEM performance curves and a metered reset trial.", "benchmark"),
    "reset_invest": _default("Controls change for temperature reset", 10000.0, "EGP", 7000, 13000,
        "Budget for controls programming and commissioning. Verify against the BMS change quotation."),
    "clean_invest": _default("Condenser cleaning, per plant", 250000.0, "EGP", 180000, 320000,
        "Budget for one plant cleaning intervention. Verify against the condenser service scope and contractor quotation."),
    "planned_factor": _default("Planned repair cost relative to an emergency repair", 0.6, "×", 0.45, 0.75,
        "Planned work is budgeted at 60% of emergency crew-plus-parts cost. Verify against matched planned and emergency maintenance work orders."),
    "discount_rate": _default("Effective annual discount rate", 0.18, "fraction/year", 0.12, 0.24,
        "Planning rate for investment appraisal; verify with finance against the approved capital-budget discount-rate memorandum."),
    "background_loss_ratio": _default("Background leakage relative to billed demand", 0.115, "fraction", 0.085, 0.145,
        "Synthetic baseline leakage is 11.5% of billed demand; this is not a measured district water balance. Verify against minimum-night-flow tests and production/billing meter reconciliation."),
    "maintenance_leak_m3h": _default("Maintenance pipe-failure leak proxy", 100.0, "m3/h", 75, 125,
        "A 100 m3/h leak represents an illustrative future pipe failure. Verify against pipe diameter, hydraulic breach modelling and historical burst work orders."),
    "maintenance_unserved_fraction": _default("Maintenance trip unserved-load proxy", 0.45, "fraction", 0.3, 0.6,
        "Assume 45% of current zone electrical demand remains unserved after backup transfer. Verify against the substation contingency and protection study."),
    "maintenance_cooling_fraction": _default("Maintenance cooling-deficit proxy", 0.5, "fraction", 0.35, 0.65,
        "Assume half of plant thermal delivery is lost in a future failure. Verify against the plant redundancy and chiller availability study."),
    "maintenance_cooling_fallback_mw": _default("Maintenance fallback plant thermal delivery", 10.0, "MW", 7.5, 12.5,
        "Use 10 MW thermal only when the live plant metric is unavailable. Verify against plant duty records and the rated-load schedule."),
}

for _type, _rate, _voll in [("government", 5.0, 45.0), ("business", 2.5, 60.0),
                            ("residential", 0.15, 20.0), ("services", 8.0, 75.0)]:
    DEFAULTS[f"customer_hour_{_type}"] = _default(f"Utility outage administration, {_type}", _rate, "EGP/customer-h", _rate * 0.5, _rate * 1.5,
        f"Illustrative {_type} account-hour budget for notification and utility customer support only; excludes customer economic harm covered by VoLL. Verify against the utility support cost ledger and outage notification work orders.")
    DEFAULTS[f"voll_{_type}"] = _default(f"Value of lost load, {_type}", _voll, "EGP/kWh", _voll * 0.5, _voll * 1.5,
        f"Illustrative {_type} customer economic outage value, including lost downstream services; not an official Egyptian VoLL. Verify against a local customer interruption-cost survey and the regulator's approved VoLL study, if available.")

for _zone, _count in ZONE_CUSTOMERS.items():
    DEFAULTS[f"customers_{_zone.replace('-', '_')}"] = _default(f"Service accounts, {_zone}", _count, "customers", _count * 0.75, _count * 1.25,
        "Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers.")

# (delay hours, escalation multiplier, minimum saving, maximum saving)
POLICIES = {
    "pipe_burst": (6.0, 1.7, 0, 2000000), "pump_failure": (8.0, 1.4, 0, 500000),
    "substation_trip": (4.0, 1.25, 0, 50000000), "cooling_fault": (10.0, 1.5, 0, 5000000),
    "stuck_sensor": (12.0, 1.1, 0, 50000), "energy_waste": (6.0, 1.0, 0, 5000000),
}
POLICY_BASIS = {
    "pipe_burst": "Continuing erosion can enlarge excavation and pipe damage",
    "pump_failure": "An idle failed pump requires slower mechanical restoration",
    "substation_trip": "A protection trip needs prompt inspection with modest assumed additional damage",
    "cooling_fault": "A deferred chiller repair risks additional component damage over a longer service window",
    "stuck_sensor": "An instrument fault can persist through a shift with little additional physical damage",
    "energy_waste": "An efficiency intervention can wait one shift without assumed additional repair damage",
}
for _kind, (_hours, _mult, _min, _max) in POLICIES.items():
    DEFAULTS[f"delay_hours_{_kind}"] = _default(f"Deferred response horizon, {_kind}", _hours, "h", _hours * 0.75, _hours * 1.25,
        f"{POLICY_BASIS[_kind]}; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA.")
    DEFAULTS[f"late_multiplier_{_kind}"] = _default(f"Late repair multiplier, {_kind}", _mult, "×", max(1.0, _mult - 0.2), _mult + 0.2 if _mult > 1 else 1.0,
        f"{POLICY_BASIS[_kind]}; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders.")
    for _bound, _value in [("min", _min), ("max", _max)]:
        DEFAULTS[f"saving_{_bound}_{_kind}"] = _default(f"Plausible saving {_bound}, {_kind}", _value, "EGP", _value, _value,
            "Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register.")


class Assumptions:
    def __init__(self, path: Path = ASSUMPTIONS_FILE):
        self.path = path
        self.values = {k: d["value"] for k, d in DEFAULTS.items()}
        self.changed: dict[str, str] = {}
        if path.exists():
            saved = json.loads(path.read_text())
            for k, item in saved.items():
                if k in self.values:
                    self.values[k], self.changed[k] = float(item["value"]), item.get("source", "user")

    def __getitem__(self, k: str) -> float:
        return self.values[k]

    def update(self, changes: dict[str, float], source: str = "user") -> None:
        for k, v in changes.items():
            if k not in self.values:
                raise KeyError(k)
            if not math.isfinite(float(v)):
                raise ValueError("Assumptions must be finite")
        for k, v in changes.items():
            self.values[k] = float(v)
            self.changed[k] = f"{source} {datetime.now(timezone.utc).date().isoformat()}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: {"value": self.values[k], "source": s} for k, s in self.changed.items()}, indent=2))

    def reset(self) -> None:
        self.values = {k: d["value"] for k, d in DEFAULTS.items()}
        self.changed = {}
        if self.path.exists():
            self.path.unlink()

    def item(self, k: str) -> Assumption:
        d = DEFAULTS[k]
        return Assumption(**{**d, "value": self.values[k]}, key=k,
                          source=self.changed.get(k, "default assumption"), user_changed=k in self.changed,
                          references=REFERENCES.get(k, []), evidence=evidence(k, d["low"], d["high"]), search_note=NOT_FOUND.get(k))

    def all(self) -> list[Assumption]:
        return [self.item(k) for k in DEFAULTS]

    def sampled(self, rng, spread: float = 0.2, keys=None) -> "Assumptions":
        """Uniform draws from documented absolute ranges; spread is a legacy fallback."""
        clone = Assumptions.__new__(Assumptions)
        clone.path, clone.changed, clone.values = self.path, dict(self.changed), dict(self.values)
        for k in sorted(UNCERTAIN if keys is None else keys):
            d = DEFAULTS[k]
            clone.values[k] = rng.uniform(d.get("low", self[k] * (1 - spread)), d.get("high", self[k] * (1 + spread)))
        return clone

    def with_override(self, k: str, v: float) -> "Assumptions":
        clone = Assumptions.__new__(Assumptions)
        clone.path, clone.values, clone.changed = self.path, dict(self.values), dict(self.changed)
        clone.values[k] = v
        return clone


@dataclass
class Term:
    label: str
    group: str
    formula: str
    inputs: list[CalcInput]
    cause: str
    amount: float

    @property
    def value(self) -> float:
        return self.amount

    @property
    def category(self) -> str:
        return self.group.split(":")[0]


@dataclass
class IncidentFacts:
    """Synthetic measured/forecast facts; secondary_mw is extra purchased energy only."""
    kind: str
    zone_types: dict[str, str]
    leak_m3h: float = 0.0
    unserved_mw: float = 0.0
    excess_mw: float = 0.0
    cooling_deficit_tons: float = 0.0
    secondary_mw: float = 0.0
    pumps_stopped: int = 0
    plants_affected: int = 0
    reasons: list[Reason] = field(default_factory=list)
    confidence: float = 0.8
    input_source: str = "measured"
    input_basis: str | None = None


UNCERTAIN = {k for k, d in DEFAULTS.items() if d["low"] != d["high"]}
TONS_PER_MW = 284.3
KIND_ALIASES = {"leak": "pipe_burst", "sensor_fault": "stuck_sensor"}
MAIN_ASSUMPTION = {"pipe_burst": "water_price", "pump_failure": "parts_pump", "substation_trip": "voll_government",
                   "cooling_fault": "cooling_price", "energy_waste": "elec_tariff", "stuck_sensor": "false_dispatch"}


def policy_kind(f: IncidentFacts) -> str:
    return KIND_ALIASES.get(f.kind, f.kind)


def customer_key(zone: str) -> str:
    return f"customers_{zone.replace('-', '_')}"


def _a(name, value, unit, source="assumed", basis=None) -> CalcInput:
    bases = {"measured": "Synthetic twin measurement or baseline history; verify against calibrated SCADA/meter records before operational use.",
             "forecast": "Synthetic failure/demand forecast; verify calibration and the stated forecast horizon against observed outcomes.",
             "rule": "Derived by the named calculation in this record; see docs/cost-model.md for its equation and units.",
             "assumed": "Planning scenario input; verify against the operating procedure documented in docs/cost-model.md."}
    return CalcInput(name=name, value=value, unit=unit, source=source, basis=basis or bases[source], status="planning" if source == "assumed" else None)


def _ai(A: Assumptions, key: str, name=None) -> CalcInput:
    d = A.item(key)
    return CalcInput(name=name or key, key=key, value=d.value, unit=d.unit, source="assumed", basis=d.basis, status=d.status)


def _fact(f, name, value, unit) -> CalcInput:
    return _a(name, value, unit, f.input_source, f.input_basis)


def build_terms(f: IncidentFacts, A: Assumptions) -> list[Term]:
    terms: list[Term] = []
    kind = policy_kind(f)
    if f.leak_m3h:
        terms.append(Term("Water loss", "loss:water", "Q_leak × P_water",
                          [_fact(f, "Q_leak", f.leak_m3h, "m3/h"), _ai(A, "water_price", "P_water")],
                          "Physical leaked water production cost", f.leak_m3h * A["water_price"]))
    if f.unserved_mw:
        customers = sum(A[customer_key(z)] for z in f.zone_types)
        if customers <= 0:
            raise ValueError("Unserved energy requires positive affected customer counts")
        for z, ztype in f.zone_types.items():
            count = A[customer_key(z)]
            key = f"voll_{ztype}"
            terms.append(Term(f"Energy not served, {z}", f"loss:unserved:{z}", "E_unserved × customers_zone / customers_total × VoLL_zone",
                [_fact(f, "E_unserved", f.unserved_mw * 1000, "kWh/h"), _ai(A, customer_key(z), "customers_zone"),
                 _a("customers_total", customers, "customers", "rule", "Sum of affected-zone service accounts; customer shares are a planning proxy for unavailable zone-level unserved-energy metering."), _ai(A, key)],
                f"Customer economic outage harm in {z}, including downstream lost services", f.unserved_mw * 1000 * count / customers * A[key]))
    if f.excess_mw:
        terms.append(Term("Energy waste", "loss:energy_waste", "(E_actual − E_expected) × P_kWh",
                          [_fact(f, "E_actual − E_expected", f.excess_mw * 1000, "kWh/h"), _ai(A, "elec_tariff", "P_kWh")],
                          "Primary-network excess purchased electricity", f.excess_mw * 1000 * A["elec_tariff"]))
    if f.cooling_deficit_tons and not f.unserved_mw:
        terms.append(Term("Cooling shortfall", "loss:cooling", "Q_deficit × P_cooling",
                          [_fact(f, "Q_deficit", f.cooling_deficit_tons, "ton-h/h"), _ai(A, "cooling_price", "P_cooling")],
                          "Cooling shortfall independent of electricity outage harm", f.cooling_deficit_tons * A["cooling_price"]))
    if kind not in ("stuck_sensor", "energy_waste"):
        for z, ztype in f.zone_types.items():
            key = f"customer_hour_{ztype}"
            terms.append(Term(f"Service disruption, {z}", f"disruption:{z}", "customers_affected × customer_hour_rate × outage_hours",
                [_ai(A, customer_key(z), "customers_affected"), _ai(A, key, "customer_hour_rate"),
                 _a("outage_hours", 1, "h", "rule", "One outage hour establishes the hourly rate; the delay/response calculations apply the respective total hours once.")],
                f"Utility notification and customer-support administration in {z}", A[customer_key(z)] * A[key]))
    if f.secondary_mw:
        terms.append(Term("Knock-on overdraw in another network", "secondary:electricity", "ΔE_secondary × P_kWh",
                          [_a("ΔE_secondary", f.secondary_mw * 1000, "kWh/h", "measured",
                              "Synthetic extra purchased electricity in a dependent network; excludes unserved energy and primary-network excess consumption."), _ai(A, "elec_tariff", "P_kWh")],
                          "Incremental dependent-network purchased electricity", f.secondary_mw * 1000 * A["elec_tariff"]))
    if kind == "stuck_sensor":
        terms.append(Term("False dispatches", "disruption:false_dispatch", "false_dispatch / false_dispatch_interval",
                          [_ai(A, "false_dispatch"), _ai(A, "false_dispatch_interval")],
                          "Unnecessary visits caused by the unresolved instrument fault", A["false_dispatch"] / A["false_dispatch_interval"]))
    parts = {"pipe_burst": "parts_pipe", "pump_failure": "parts_pump", "substation_trip": "parts_substation",
             "cooling_fault": "parts_chiller", "stuck_sensor": "parts_sensor", "energy_waste": None}[kind]
    inputs = [_ai(A, "crew_callout")] + ([_ai(A, parts)] if parts else [])
    terms.append(Term("Repair now", "repair", " + ".join(i.name for i in inputs), inputs,
                      "Restoration crew and parts", sum(i.value for i in inputs)))
    return terms


_calc_ids = itertools.count(1)
MC_RUNS, MC_SPREAD, MC_LEAK_ERR = 800, 0.20, 0.15


def _summary(f: IncidentFacts, A: Assumptions) -> dict:
    terms = build_terms(f, A)
    kind = policy_kind(f)
    t_delay, late_mult, t_resp = A[f"delay_hours_{kind}"], A[f"late_multiplier_{kind}"], A["response_hours"]
    g = lambda grp: sum(t.value for t in terms if t.category == grp)  # noqa: E731
    c_loss, c_dis, c_sec, repair_now = g("loss"), g("disruption"), g("secondary"), g("repair")
    c_delay = c_loss + c_dis + c_sec
    repair_late = repair_now * late_mult
    losses_until_crew = c_delay * t_resp
    act_now = repair_now + losses_until_crew
    cost_of_delay = c_delay * t_delay + repair_late
    return dict(terms=terms, c_loss=c_loss, c_dis=c_dis, c_sec=c_sec, c_delay=c_delay, repair_now=repair_now, repair_late=repair_late,
                losses_until_crew=losses_until_crew, act_now=act_now, cost_of_delay=cost_of_delay, saving=cost_of_delay - act_now)


def assumption_keys(f: IncidentFacts, A: Assumptions) -> list[str]:
    keys = [i.key for t in build_terms(f, A) for i in t.inputs if i.key]
    kind = policy_kind(f)
    return list(dict.fromkeys(keys + [f"delay_hours_{kind}", "response_hours", f"late_multiplier_{kind}"]))


def monte_carlo(f: IncidentFacts, A: Assumptions, incident_id: str) -> SavingRange:
    """Seeded scenario percentiles from registered ranges and ±15% measured leak error."""
    rng = np.random.default_rng(zlib.crc32(incident_id.encode()))
    out = []
    keys = assumption_keys(f, A)
    for _ in range(MC_RUNS):
        ff = dataclasses.replace(f, leak_m3h=f.leak_m3h * rng.uniform(1 - MC_LEAK_ERR, 1 + MC_LEAK_ERR))
        out.append(_summary(ff, A.sampled(rng, keys=keys))["saving"])
    p10, p50, p90 = (round(float(x), 2) for x in np.percentile(out, [10, 50, 90]))
    return SavingRange(p10=p10, p50=p50, p90=p90, runs=MC_RUNS)


def tornado(A: Assumptions, keys, evaluate) -> list[Tornado]:
    rows = []
    for key in keys:
        d = A.item(key)
        lo, hi = evaluate(key, d.low), evaluate(key, d.high)
        if not math.isclose(lo, hi, rel_tol=0.0, abs_tol=1e-9):
            rows.append(Tornado(input=key, name=d.name, low=d.low, high=d.high,
                                saving_at_low=round(lo, 2), saving_at_high=round(hi, 2)))
    rows.sort(key=lambda t: (-abs(t.saving_at_high - t.saving_at_low), t.input))
    return rows


def compute(f: IncidentFacts, A: Assumptions, incident_id: str, calc_id: str | None = None) -> CalculationRecord:
    record = _compute(f, A, incident_id, calc_id or f"calc-{next(_calc_ids):04d}")
    kind = policy_kind(f)
    keys = assumption_keys(f, A)
    main = next((k for k in keys if k.startswith("voll_")), MAIN_ASSUMPTION[kind])
    for key in dict.fromkeys([main, f"delay_hours_{kind}", "response_hours"]):
        for pct in (-20, 20):
            saving = _summary(f, A.with_override(key, A[key] * (1 + pct / 100)))["saving"]
            record.explanation.sensitivity.append(Sensitivity(input=key, change_percent=pct, new_saving=round(saving, 2)))
    record.explanation.tornado = tornado(A, keys, lambda k, v: _summary(f, A.with_override(k, v))["saving"])
    rng_ = monte_carlo(f, A, incident_id)
    record.cost.saving_range = rng_
    record.explanation.calculations.append(Calculation(
        name=f"Saving range ({rng_.runs} simulated runs)",
        formula="P10, P50, P90 of Saving; independent uniform assumption draws in registered [low, high], leak estimate ±15%",
        inputs=[_a("runs", rng_.runs, "runs", "rule", "Fixed reproducible scenario sample count, seeded by incident id."),
                _a("leak_error", MC_LEAK_ERR, "fraction", "assumed", "Planning ±15% flow-difference error; verify against paired meter calibration certificates.")], result=rng_.p50))
    record.cost.explanation = record.explanation
    return record


def _compute(f: IncidentFacts, A: Assumptions, incident_id: str, calc_id: str) -> CalculationRecord:
    n = _summary(f, A)
    kind = policy_kind(f)
    delay_key, late_key = f"delay_hours_{kind}", f"late_multiplier_{kind}"
    t_delay, late_mult, t_resp = A[delay_key], A[late_key], A["response_hours"]
    calcs = [Calculation(name=t.label, formula=t.formula, inputs=t.inputs, result=round(t.value, 2),
                         unit="EGP" if t.category == "repair" else "EGP/h") for t in n["terms"]]
    calcs += [
        Calculation(name="Cost of delay per hour", formula="C_loss + C_disruption + C_secondary",
                    inputs=[_a(k, n[v], "EGP/h", "rule") for k, v in [("C_loss", "c_loss"), ("C_disruption", "c_dis"), ("C_secondary", "c_sec")]], result=round(n["c_delay"], 2), unit="EGP/h"),
        Calculation(name="Losses until the crew arrives", formula="C_delay × response_hours",
                    inputs=[_a("C_delay", n["c_delay"], "EGP/h", "rule"), _ai(A, "response_hours")], result=round(n["losses_until_crew"], 2)),
        Calculation(name="Cost of acting now", formula="C_repair_now + C_delay × response_hours",
                    inputs=[_a("C_repair_now", n["repair_now"], "EGP", "rule"), _a("C_delay", n["c_delay"], "EGP/h", "rule"), _ai(A, "response_hours")], result=round(n["act_now"], 2)),
        Calculation(name="Repair later", formula=f"C_repair_now × {late_key}",
                    inputs=[_a("C_repair_now", n["repair_now"], "EGP", "rule"), _ai(A, late_key)], result=round(n["repair_late"], 2)),
        Calculation(name="Cost of delay", formula=f"C_delay × {delay_key} + C_repair_late",
                    inputs=[_a("C_delay", n["c_delay"], "EGP/h", "rule"), _ai(A, delay_key), _a("C_repair_late", n["repair_late"], "EGP", "rule")], result=round(n["cost_of_delay"], 2)),
        Calculation(name="Saving from acting now", formula="CostOfDelay − ActNow",
                    inputs=[_a("CostOfDelay", n["cost_of_delay"], "EGP", "rule"), _a("ActNow", n["act_now"], "EGP", "rule")], result=round(n["saving"], 2)),
    ]
    breakdown = []
    for t in n["terms"]:
        repair = t.category == "repair"
        inputs = t.inputs + [_ai(A, late_key)] if repair else t.inputs + [_ai(A, delay_key, "duration_hours")]
        formula = f"({t.formula}) × {late_key}" if repair else f"({t.formula}) × duration_hours"
        value = t.value * (late_mult if repair else t_delay)
        calcs.append(Calculation(name=f"Delayed cost: {t.label}", formula=formula, inputs=inputs, result=round(value, 2)))
        breakdown.append(CostItem(label=t.label, group=t.group, amount=round(value, 2), cause=t.cause))
    # Sum the displayed rows so attribution always reconciles exactly, including fractional-cent inputs.
    cost_of_delay = sum(t.amount for t in breakdown)
    act_now = round(n["act_now"], 2)
    saving = round(cost_of_delay - act_now, 2)
    for c in calcs:
        if c.name == "Cost of delay":
            c.result = cost_of_delay
        elif c.name == "Saving from acting now":
            c.result = saving
    min_key, max_key = f"saving_min_{kind}", f"saving_max_{kind}"
    low, high = A[min_key], A[max_key]
    review = not low <= n["saving"] <= high
    reason = f"{kind} saving {n['saving']:,.2f} EGP is outside the planning range [{low:g}, {high:g}] EGP; verify inputs and incident scope." if review else None
    keys = assumption_keys(f, A) + [min_key, max_key]
    explanation = Explanation(reasons=f.reasons, calculations=calcs, assumptions=[A.item(k) for k in keys],
                              sensitivity=[], confidence=f.confidence, calculation_id=calc_id)
    cost = Cost(act_now=act_now, delay_per_hour=round(n["c_delay"], 2), expected_saving=saving, delay_hours=t_delay,
                cost_of_delay=cost_of_delay, response_hours=t_resp, act_now_losses=round(n["losses_until_crew"], 2),
                breakdown=breakdown, review=review, review_reason=reason)
    return CalculationRecord(id=calc_id, incident_id=incident_id, created_at=datetime.now(timezone.utc), cost=cost, explanation=explanation)
