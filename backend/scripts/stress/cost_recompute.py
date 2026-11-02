"""Cost engine checked against a second, independent implementation (stress-test items K-1, K-3, K-5, K-8).

    PYTHONPATH=. .venv/bin/python scripts/stress/cost_recompute.py

The formulas from docs/cost-model.md are written out again below, from the paper, without importing anything from the engine except the inputs
(`DEFAULTS` and the affected facts). For every incident kind, with the default assumptions and with 100 random draws inside every registered range,
the engine's act-now, cost-of-delay and saving must equal this file's to the cent. Exits 1 on any difference.
"""

from __future__ import annotations

import random
import sys

import numpy as np

from app.cost import DEFAULTS, Assumptions, IncidentFacts, compute, monte_carlo
from app.sim.topology import ZONE_CUSTOMERS

ZONES = {"zone-gov": "government", "zone-cbd": "business", "zone-res-east": "residential", "zone-res-west": "residential", "zone-services": "services"}
ZONES = {z: t for z, t in ZONES.items() if z in ZONE_CUSTOMERS}


def by_hand(kind: str, f: IncidentFacts, v: dict[str, float]) -> dict[str, float]:
    """The model of docs/cost-model.md, written again."""
    loss = disruption = secondary = 0.0
    loss += f.leak_m3h * v["water_price"]
    total_customers = sum(v[f"customers_{z.replace('-', '_')}"] for z in f.zone_types)
    for z, t in f.zone_types.items():
        if f.unserved_mw:
            loss += f.unserved_mw * 1000 * (v[f"customers_{z.replace('-', '_')}"] / total_customers) * v[f"voll_{t}"]
    loss += f.excess_mw * 1000 * v["elec_tariff"]
    if f.cooling_deficit_tons and not f.unserved_mw:
        loss += f.cooling_deficit_tons * v["cooling_price"]
    if kind not in ("stuck_sensor", "energy_waste"):
        for z, t in f.zone_types.items():
            disruption += v[f"customers_{z.replace('-', '_')}"] * v[f"customer_hour_{t}"]
    secondary += f.secondary_mw * 1000 * v["elec_tariff"]
    if kind == "stuck_sensor":
        disruption += v["false_dispatch"] / v["false_dispatch_interval"]
    parts = {"pipe_burst": "parts_pipe", "pump_failure": "parts_pump", "substation_trip": "parts_substation", "cooling_fault": "parts_chiller",
             "stuck_sensor": "parts_sensor", "energy_waste": None}[kind]
    repair = v["crew_callout"] + (v[parts] if parts else 0.0)
    per_hour = loss + disruption + secondary
    act_now = repair + per_hour * v["response_hours"]
    delay = v[f"delay_hours_{kind}"]
    cost_of_delay = per_hour * delay + repair * v[f"late_multiplier_{kind}"]
    return {"act_now": act_now, "cost_of_delay": cost_of_delay, "saving": cost_of_delay - act_now, "delay_per_hour": per_hour}


CASES = {
    "pipe_burst": IncidentFacts("pipe_burst", {"zone-gov": "government"}, leak_m3h=240.0, reasons=[]),
    "pump_failure": IncidentFacts("pump_failure", {"zone-cbd": "business"}, leak_m3h=0.0, unserved_mw=0.0, pumps_stopped=1, reasons=[], excess_mw=0.0, secondary_mw=0.4),
    "substation_trip": IncidentFacts("substation_trip", {"zone-gov": "government", "zone-cbd": "business"}, unserved_mw=12.5, secondary_mw=1.1, reasons=[]),
    "cooling_fault": IncidentFacts("cooling_fault", {"zone-cbd": "business"}, cooling_deficit_tons=900.0, secondary_mw=0.8, reasons=[]),
    "stuck_sensor": IncidentFacts("stuck_sensor", {"zone-gov": "government"}, reasons=[]),
    "energy_waste": IncidentFacts("energy_waste", {"zone-cbd": "business"}, excess_mw=3.2, reasons=[]),
}


def main() -> None:
    bad = 0
    n = 0
    rng = random.Random(7)
    worst = 0.0
    for trial in range(101):
        A = Assumptions.__new__(Assumptions)
        A.path, A.changed = Assumptions.__init__.__globals__["ASSUMPTIONS_FILE"], {}
        A.values = {k: d["value"] for k, d in DEFAULTS.items()}
        if trial:  # trial 0 is the defaults
            for k, d in DEFAULTS.items():
                A.values[k] = rng.uniform(d["low"], d["high"])
        for kind, f in CASES.items():
            rec = compute(f, A, f"stress-{kind}-{trial}")
            mine = by_hand(kind, f, A.values)
            for key, got in (("act_now", rec.cost.act_now), ("cost_of_delay", rec.cost.cost_of_delay), ("saving", rec.cost.expected_saving), ("delay_per_hour", rec.cost.delay_per_hour)):
                n += 1
                diff = abs(got - mine[key])
                worst = max(worst, diff)
                if diff > 0.005 * (len(rec.cost.breakdown) + 2):  # the engine adds up the rows it shows, each rounded to the cent
                    bad += 1
                    print(f"DIFFERENT {kind} trial {trial} {key}: engine {got:.4f} by hand {mine[key]:.4f}")
            shown = sum(i.amount for i in rec.cost.breakdown)
            n += 1
            if abs(shown - rec.cost.cost_of_delay) > 1e-6:
                bad += 1
                print(f"BREAKDOWN does not add up {kind} trial {trial}: {shown} vs {rec.cost.cost_of_delay}")
    print(f"K-1  {n} comparisons (6 kinds x 101 assumption sets x 4 numbers + breakdown sums): {bad} differ, largest difference {worst:.4f} EGP")

    # K-3: units of every input that a term uses, against the unit the formula needs
    expected_unit = {"water_price": "EGP/m3", "elec_tariff": "EGP/kWh", "cooling_price": "EGP/ton-h", "crew_callout": "EGP/dispatch", "response_hours": "h",
                     "false_dispatch": "EGP/dispatch", "false_dispatch_interval": "h/dispatch"}
    wrong = [k for k, u in expected_unit.items() if DEFAULTS[k]["unit"] != u]
    wrong += [k for k in DEFAULTS if k.startswith("voll_") and DEFAULTS[k]["unit"] != "EGP/kWh"]
    wrong += [k for k in DEFAULTS if k.startswith("customer_hour_") and DEFAULTS[k]["unit"] != "EGP/customer-h"]
    wrong += [k for k in DEFAULTS if k.startswith(("parts_",)) and DEFAULTS[k]["unit"] != "EGP/repair"]
    wrong += [k for k in DEFAULTS if k.startswith(("delay_hours_", "fix_hours_")) and DEFAULTS[k]["unit"] != "h"]
    A0 = Assumptions.__new__(Assumptions); A0.path, A0.changed, A0.values = A.path, {}, {k: d["value"] for k, d in DEFAULTS.items()}
    unitless = []
    for kind, f in CASES.items():
        rec = compute(f, A0, f"units-{kind}")
        for c in rec.explanation.calculations:
            for i in c.inputs:
                if not i.unit:
                    unitless.append((kind, c.name, i.name))
    print(f"K-3  assumption units that disagree with the formula that uses them: {wrong or 'none'}; inputs shown with no unit: {len(unitless)}")
    bad += len(wrong) + len(unitless)

    # K-5: Monte Carlo
    order_bad = 0
    p50s: dict[str, list[float]] = {k: [] for k in CASES}
    for kind, f in CASES.items():
        for j in range(8):
            r = monte_carlo(f, A0, f"mc-{kind}-{j}")
            order_bad += not (r.p10 <= r.p50 <= r.p90)
            p50s[kind].append(r.p50)
        again = monte_carlo(f, A0, f"mc-{kind}-0")
        order_bad += again.p50 != p50s[kind][0]
    spread = {k: float(np.std(v) / abs(np.mean(v))) if np.mean(v) else 0.0 for k, v in p50s.items()}
    print(f"K-5  P10<=P50<=P90 and same seed same answer: {order_bad} violations in {len(CASES) * 9} runs; P50 spread across 8 incident ids (sd / mean): "
          + ", ".join(f"{k} {s:.1%}" for k, s in spread.items()))
    bad += order_bad

    # K-8: every assumption has a basis and a sensible range
    missing = [k for k, d in DEFAULTS.items() if not d["basis"].strip() or not d["unit"].strip() or not d["name"].strip()]
    order = [k for k, d in DEFAULTS.items() if not (d["low"] <= d["value"] <= d["high"])]
    flat = [k for k, d in DEFAULTS.items() if d["low"] == d["high"]]
    print(f"K-8  {len(DEFAULTS)} assumptions; with an empty basis/unit/name: {missing or 'none'}; default outside [low, high]: {order or 'none'}; fixed (low = high, not sampled): {len(flat)}")
    bad += len(missing) + len(order)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
