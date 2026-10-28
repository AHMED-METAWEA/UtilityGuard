"""Deterministic cost and explanation engine (spec 9 and 9A).

Every incident cost is built from named terms:
    C_delay      = C_loss + C_disruption + C_secondary            (EGP per hour)
    CostOfDelay  = C_delay × t_delay + C_repair_late
    Saving       = CostOfDelay − C_repair_now
The LLM never produces any of these numbers; it only reads the record.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import (
    Assumption, CalcInput, Calculation, CalculationRecord, Cost, CostItem, Explanation, Reason, Sensitivity,
)

DATA = Path(__file__).resolve().parents[2] / "data"
ASSUMPTIONS_FILE = DATA / "assumptions.json"

# key: (name, value, unit)
DEFAULTS: dict[str, tuple[str, float, str]] = {
    "water_price": ("Water production cost", 8.0, "EGP/m3"),
    "elec_tariff": ("Electricity tariff", 1.6, "EGP/kWh"),
    "cooling_price": ("Cooling energy cost", 2.2, "EGP/ton-h"),
    "crew_callout": ("Crew call-out cost", 3400.0, "EGP/dispatch"),
    "parts_pipe": ("Pipe repair parts", 15000.0, "EGP/repair"),
    "parts_pump": ("Pump repair parts", 42000.0, "EGP/repair"),
    "parts_substation": ("Substation restore and inspection", 26000.0, "EGP/repair"),
    "parts_chiller": ("Chiller repair parts", 58000.0, "EGP/repair"),
    "parts_sensor": ("Sensor recalibration or replacement", 2400.0, "EGP/repair"),
    "penalty_government": ("Disruption penalty, government zone", 2500.0, "EGP/h"),
    "penalty_business": ("Disruption penalty, business zone", 3000.0, "EGP/h"),
    "penalty_residential": ("Disruption penalty, residential zone", 1200.0, "EGP/h"),
    "penalty_services": ("Disruption penalty, services zone", 4000.0, "EGP/h"),
    "delay_hours": ("Hours until repair if delayed", 6.0, "h"),
    "late_multiplier": ("Late repair cost multiplier", 1.7, "×"),
    "false_dispatch": ("Cost of a crew sent on a false alarm", 3400.0, "EGP/dispatch"),
}


class Assumptions:
    def __init__(self, path: Path = ASSUMPTIONS_FILE):
        self.path = path
        self.values = {k: v for k, (_, v, _) in DEFAULTS.items()}
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
            self.values[k] = float(v)
            self.changed[k] = f"{source} {datetime.now(timezone.utc).date().isoformat()}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: {"value": self.values[k], "source": s} for k, s in self.changed.items()}, indent=2))

    def reset(self) -> None:
        self.values = {k: v for k, (_, v, _) in DEFAULTS.items()}
        self.changed = {}
        if self.path.exists():
            self.path.unlink()

    def item(self, k: str) -> Assumption:
        name, _, unit = DEFAULTS[k]
        return Assumption(name=name, key=k, value=self.values[k], unit=unit,
                          source=self.changed.get(k, "default assumption"), user_changed=k in self.changed)

    def all(self) -> list[Assumption]:
        return [self.item(k) for k in DEFAULTS]

    def with_override(self, k: str, v: float) -> "Assumptions":
        clone = Assumptions.__new__(Assumptions)
        clone.path, clone.values, clone.changed = self.path, dict(self.values), dict(self.changed)
        clone.values[k] = v
        return clone


@dataclass
class Term:
    """One line of cost: amount = product of inputs (or an explicit total)."""
    label: str
    group: str  # loss | disruption | secondary | repair
    formula: str
    inputs: list[CalcInput]
    cause: str
    amount: float | None = None

    @property
    def value(self) -> float:
        if self.amount is not None:
            return self.amount
        v = 1.0
        for i in self.inputs:
            v *= i.value
        return v


@dataclass
class IncidentFacts:
    """Measured or forecast facts about an incident, produced by detection."""
    kind: str  # leak, pump_failure, substation_trip, cooling_fault, energy_waste, sensor_fault
    zone_types: dict[str, str]  # zone id -> zone type (affected zones)
    leak_m3h: float = 0.0
    unserved_mw: float = 0.0
    excess_mw: float = 0.0
    cooling_deficit_tons: float = 0.0
    secondary_mw: float = 0.0  # knock-on electrical overdraw in another network
    pumps_stopped: int = 0
    plants_affected: int = 0
    reasons: list[Reason] = field(default_factory=list)
    confidence: float = 0.8


TONS_PER_MW = 284.3  # refrigeration tons per MW thermal
INPUT_KEY = {"P_water": "water_price", "P_kWh": "elec_tariff", "P_cooling": "cooling_price"}

MAIN_ASSUMPTION = {"leak": "water_price", "pump_failure": "parts_pump", "substation_trip": "elec_tariff",
                   "cooling_fault": "cooling_price", "energy_waste": "elec_tariff", "sensor_fault": "false_dispatch"}


def _a(name, value, unit, source="assumed") -> CalcInput:
    return CalcInput(name=name, value=round(value, 4), unit=unit, source=source)


def build_terms(f: IncidentFacts, A: Assumptions) -> list[Term]:
    terms: list[Term] = []
    if f.leak_m3h:
        terms.append(Term("Water loss", "loss", "Q_leak × P_water",
                          [_a("Q_leak", f.leak_m3h, "m3/h", "measured"), _a("P_water", A["water_price"], "EGP/m3")],
                          f"{f.leak_m3h:,.0f} m3/h × {A['water_price']:g} EGP/m3"))
    if f.unserved_mw:
        terms.append(Term("Energy not served", "loss", "E_unserved × P_kWh",
                          [_a("E_unserved", f.unserved_mw * 1000, "kWh/h", "measured"), _a("P_kWh", A["elec_tariff"], "EGP/kWh")],
                          f"{f.unserved_mw:,.1f} MW not delivered × {A['elec_tariff']:g} EGP/kWh"))
    if f.excess_mw:
        terms.append(Term("Energy waste", "loss", "(E_actual − E_expected) × P_kWh",
                          [_a("E_actual − E_expected", f.excess_mw * 1000, "kWh/h", "measured"), _a("P_kWh", A["elec_tariff"], "EGP/kWh")],
                          f"{f.excess_mw:,.1f} MW above forecast × {A['elec_tariff']:g} EGP/kWh"))
    if f.cooling_deficit_tons:
        terms.append(Term("Cooling shortfall", "loss", "Q_deficit × P_cooling",
                          [_a("Q_deficit", f.cooling_deficit_tons, "ton-h/h", "measured"), _a("P_cooling", A["cooling_price"], "EGP/ton-h")],
                          f"{f.cooling_deficit_tons:,.0f} ton-h/h undelivered × {A['cooling_price']:g} EGP/ton-h"))
    for z, ztype in f.zone_types.items():
        if f.kind in ("sensor_fault", "energy_waste"):
            break
        key = f"penalty_{ztype}"
        terms.append(Term(f"Service disruption, {z}", "disruption", "penalty_zone",
                          [_a(key, A[key], "EGP/h")], f"{A[key]:,.0f} EGP/h for a {ztype} zone"))
    if f.secondary_mw:
        terms.append(Term("Knock-on overdraw in another network", "secondary", "ΔE_secondary × P_kWh",
                          [_a("ΔE_secondary", f.secondary_mw * 1000, "kWh/h", "measured"), _a("P_kWh", A["elec_tariff"], "EGP/kWh")],
                          f"{f.secondary_mw:,.2f} MW extra draw × {A['elec_tariff']:g} EGP/kWh"))

    parts = {"leak": "parts_pipe", "pump_failure": "parts_pump", "substation_trip": "parts_substation",
             "cooling_fault": "parts_chiller", "sensor_fault": "parts_sensor", "energy_waste": None}[f.kind]
    terms.append(Term("Crew call-out", "repair", "crew_callout", [_a("crew_callout", A["crew_callout"], "EGP")], "assumed crew cost"))
    if parts:
        terms.append(Term(DEFAULTS[parts][0], "repair", parts, [_a(parts, A[parts], "EGP")], "assumed parts cost"))
    return terms


_calc_ids = itertools.count(1)


def compute(f: IncidentFacts, A: Assumptions, incident_id: str, calc_id: str | None = None) -> CalculationRecord:
    record = _compute(f, A, incident_id, calc_id or f"calc-{next(_calc_ids):04d}")
    main = MAIN_ASSUMPTION[f.kind]
    sens = []
    for key in dict.fromkeys([main, "delay_hours"]):
        for pct in (-20, 20):
            alt = _compute(f, A.with_override(key, A[key] * (1 + pct / 100)), incident_id, record.id)
            sens.append(Sensitivity(input=key, change_percent=pct, new_saving=alt.cost.expected_saving))
    record.explanation.sensitivity = sens
    record.cost.explanation = record.explanation
    return record


def _compute(f: IncidentFacts, A: Assumptions, incident_id: str, calc_id: str) -> CalculationRecord:
    terms = build_terms(f, A)
    t_delay, late_mult = A["delay_hours"], A["late_multiplier"]
    g = lambda grp: sum(t.value for t in terms if t.group == grp)  # noqa: E731
    c_loss, c_dis, c_sec, repair_now = g("loss"), g("disruption"), g("secondary"), g("repair")
    c_delay = c_loss + c_dis + c_sec
    repair_late = repair_now * late_mult
    if f.kind == "sensor_fault":
        # acting = recalibrate; not acting = expected false dispatches + blind spot risk
        c_delay = A["false_dispatch"] / 4  # one false dispatch every 4 h on average
    cost_of_delay = c_delay * t_delay + repair_late
    saving = cost_of_delay - repair_now

    R = lambda x: round(x, 0)  # noqa: E731
    calcs = [Calculation(name=t.label, formula=t.formula, inputs=t.inputs, result=R(t.value),
                         unit="EGP/h" if t.group != "repair" else "EGP") for t in terms]
    calcs += [
        Calculation(name="Cost of delay per hour", formula="C_loss + C_disruption + C_secondary",
                    inputs=[_a("C_loss", R(c_loss), "EGP/h", "rule"), _a("C_disruption", R(c_dis), "EGP/h", "rule"),
                            _a("C_secondary", R(c_sec), "EGP/h", "rule")], result=R(c_delay), unit="EGP/h"),
        Calculation(name="Repair now", formula="crew_callout + parts", inputs=[_a(t.label, t.value, "EGP") for t in terms if t.group == "repair"],
                    result=R(repair_now)),
        Calculation(name="Repair later", formula="C_repair_now × late_multiplier",
                    inputs=[_a("C_repair_now", R(repair_now), "EGP", "rule"), _a("late_multiplier", late_mult, "×")], result=R(repair_late)),
        Calculation(name="Cost of delay", formula="C_delay × t_delay + C_repair_late",
                    inputs=[_a("C_delay", R(c_delay), "EGP/h", "rule"), _a("t_delay", t_delay, "h"),
                            _a("C_repair_late", R(repair_late), "EGP", "rule")], result=R(cost_of_delay)),
        Calculation(name="Saving from acting now", formula="(C_delay × t_delay + C_repair_late) − C_repair_now",
                    inputs=[_a("Cost of delay", R(cost_of_delay), "EGP", "rule"), _a("C_repair_now", R(repair_now), "EGP", "rule")],
                    result=R(saving)),
    ]
    keys = [INPUT_KEY.get(i.name, i.name) for t in terms for i in t.inputs]
    keys += ["delay_hours", "late_multiplier"] + (["false_dispatch"] if f.kind == "sensor_fault" else [])
    keys = [k for k in dict.fromkeys(keys) if k in DEFAULTS]
    explanation = Explanation(reasons=f.reasons, calculations=calcs, assumptions=[A.item(k) for k in keys],
                              sensitivity=[], confidence=f.confidence, calculation_id=calc_id)
    breakdown = [CostItem(label=t.label, amount=R(t.value * (1 if t.group == "repair" else t_delay)),
                          cause=t.cause if t.group == "repair" else f"{t.cause} × {t_delay:g} h") for t in terms]
    breakdown.append(CostItem(label="Late repair premium", amount=R(repair_late - repair_now),
                              cause=f"repair grows ×{late_mult:g} if delayed"))
    cost = Cost(act_now=R(repair_now), delay_per_hour=R(c_delay), expected_saving=R(saving), delay_hours=t_delay,
                cost_of_delay=R(cost_of_delay), breakdown=breakdown)
    return CalculationRecord(id=calc_id, incident_id=incident_id, created_at=datetime.now(timezone.utc),
                             cost=cost, explanation=explanation)
