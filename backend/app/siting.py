"""The siting what-if: what does a new hospital, an expansion or a clinic in a zone do to the water, electricity and cooling networks?

The facility is added to the zone's demand inside the twin's own physics (`Engine.extra_load`), and the twin is read on simulated summer weekdays, hour by hour,
with and without it. Three conditions are read, never mixed into one number: an ordinary day, a heat wave, and the loss of any one substation (the neighbour
then carries part of its load). A "breach" is a limit the twin already has:

- electricity: a substation above its firm capacity (`dashboard.SUB_CAPACITY_MW`); the warning level is `briefing.PEAK_WARN` of it;
- cooling: a plant asked for more than the top of its normal operating range (the range the twin calibrated on August operation); the warning is the detector's 85%;
- water: a district pump that can no longer hold its discharge set-point (`PUMP_SETPOINT`); the warning is a pipe under its normal pressure.

The probability is the share of the simulated days (each with its own temperature and demand draw) with at least one breach, over many draws of the
facility's load inside its assumption ranges. It is a probability under the stated assumptions, not a forecast of the real network. Nothing here uses a language
model, and the live twin is never touched: a separate `Engine` is built for each answer.
"""

from __future__ import annotations

import csv
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from app.briefing import PEAK_WARN
from app.cost import DEFAULTS
from app.dashboard import SUB_CAPACITY_MW
from app.schemas.siting import (
    ConditionResult, Mitigation, NetworkRisk, SitingAssumption, SitingCost, SitingDistrict, SitingLoad, SitingRequest, SitingResult, SitingZone,
)
from app.sim.engine import CAIRO, CHILLER_BASE, COOLED_ZONES, PUMP_SETPOINT, Engine, Event
from app.sim.topology import COOLING_PLANTS, SUBSTATION_BACKUP, ZONE_SUBSTATION

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "integration" / "health" / "out"
HEAT_C = 46.0  # the Scenario Lab default for a heat wave
COOLING_RATING = 1.0  # share of the top of a plant's normal operating range counted as its limit (the twin has no nameplate rating)
COOLING_WATCH = 0.85  # the detector's "at its limit"
MITIGATE_AT = 0.05  # a network gets a mitigation when the facility leaves at least this share of days in breach and adds at least MITIGATE_ADDS to it
MITIGATE_ADDS = 0.10
PACKAGE_OF = {"electricity": "P1", "cooling": "P2", "water": "P3"}
PLANNING = "Illustrative planning allowance, not a design figure. Replace it with the load schedule of the design brief (electrical, plumbing, HVAC)."


def _a(name: str, value: float, low: float, high: float, unit: str, basis: str = PLANNING) -> SitingAssumption:
    return SitingAssumption(name=name, value=value, low=low, high=high, unit=unit, basis=basis)


@dataclass(frozen=True)
class Kind:
    label: str
    unit: str
    water: SitingAssumption  # per unit
    electricity: SitingAssumption
    cooling: SitingAssumption
    capex_m_egp: float | None
    capex_basis: str


HOSPITAL = dict(
    water=_a("Water demand per bed", 0.027, 0.019, 0.040, "m3/h per bed",
             "Average flow. 650 L/bed/day is the typical figure of a US plumbing-design chart (172 gal/bed/day, ASPE); the range runs from 450 L (Indian National Building Code, hospitals over 100 beds) to 950 L (top of the same chart). Peak-hour flow is higher; the twin's hospital demand is flat. Replace with the plumbing design."),
    electricity=_a("Electrical demand per bed", 0.004, 0.002, 0.007, "MW per bed",
                   "Derived, not found as a per-bed figure: hospital energy intensity of 177 to 380 kWh/m2/year (published Indonesian study; a global figure it quotes) times 80 to 112 m2 per bed (Korean survey average; Chinese hospital standard), divided by 8760 h and a measured hospital load factor of 0.73. Not Egyptian data. Includes the building's HVAC fans and pumps. Replace with the electrical load schedule."),
    cooling=_a("Cooling demand per bed", 0.02, 0.012, 0.03, "MW thermal per bed",
               "One published reference point: a 759-bed US hospital campus with 5,800 tons of chillers is 7.6 tons per bed (0.027 MW thermal), redundancy included. Cairo's climate argues for the upper half. Replace with the HVAC load calculation."),
)
THEIR = "The healthcare team's placeholder (their dashboard/config.py: %s). Not a local cost estimate."
KINDS = {
    "hospital": Kind("New hospital", "beds", **HOSPITAL, capex_m_egp=24.0, capex_basis=THEIR % "center_fixed 24 M EGP, the fixed cost of package A"),
    "hospital_expansion": Kind("Hospital expansion", "added beds", **HOSPITAL, capex_m_egp=9.0, capex_basis=THEIR % "capacity expansion 9 M EGP at their default 15%"),
    "clinic": Kind("Clinic", "clinics", water=_a("Water demand per clinic", 0.6, 0.3, 1.0, "m3/h per clinic"),
                   electricity=_a("Electrical demand per clinic", 0.12, 0.06, 0.2, "MW per clinic"),
                   cooling=_a("Cooling demand per clinic", 0.1, 0.05, 0.15, "MW thermal per clinic"), capex_m_egp=None,
                   capex_basis="No cost in the healthcare team's files for a clinic: enter one to include it"),
    "custom": Kind("Custom load", "facilities", water=_a("Water demand per facility", 0, 0, 0, "m3/h"), electricity=_a("Electrical demand per facility", 0, 0, 0, "MW"),
                   cooling=_a("Cooling demand per facility", 0, 0, 0, "MW thermal"), capex_m_egp=None, capex_basis="Enter the cost of the facility to include it"),
}
SPREAD = 0.25  # a custom load is read as the stated value plus or minus this share


def _days() -> list[date]:
    """Summer weekdays (Sunday to Thursday): the days a hospital, a ministry and a business district are all at work."""
    d, out = date(2026, 6, 1), []
    while d <= date(2026, 9, 30):
        if d.weekday() not in (4, 5):
            out.append(d)
        d += timedelta(days=1)
    return out


def _packages() -> dict[str, tuple[str, float]]:
    try:
        with (OUT / "packages.csv").open(encoding="utf-8-sig", newline="") as f:
            return {r["package_id"]: (r["package_name"], float(r["cost_m_egp_5yr_per_asset"])) for r in csv.DictReader(f)}
    except (OSError, ValueError, KeyError):
        return {}


def _districts(zone: str) -> list[SitingDistrict]:
    try:
        with (OUT / "joint_decision.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if zone in r["our_zone_ids"].split(";")]
    except OSError:
        return []

    def num(x: str) -> float | None:
        return float(x) if x not in ("", None) else None

    out = [SitingDistrict(district_key=r["district_key"], district_code=r["district_code"], match_kind=r["match_kind"], hospitals=int(r["hospitals"]),
                          their_pressure_baseline=num(r.get("their_pressure_baseline", "")), their_over_capacity=(r["their_over_capacity"] == "True") if r.get("their_over_capacity") else None,
                          esi_overall=num(r["esi_overall"])) for r in rows]
    return sorted(out, key=lambda d: (d.match_kind != "direct", d.district_key))


class _Reader:
    """Reads the twin's true values for one zone load, one condition and one day, hour by hour."""

    def __init__(self, seed: int):
        self.e = Engine(seed)
        g = self.e.g
        self.sub_ids = list(SUBSTATION_BACKUP)
        self.rating = {p: COOLING_RATING * g.sensor_index[f"{p}-load"].normal_range.max for p in COOLING_PLANTS}
        self.watch = {p: COOLING_WATCH * g.sensor_index[f"{p}-load"].normal_range.max for p in COOLING_PLANTS}
        self.pumps = list(g.zone_pump.values())
        self.pipes = list(g.zone_pipe.values())
        self.pipe_min = {p: g.sensor_index[f"{p}-pressure"].normal_range.min for p in self.pipes}

    def day(self, d: date, events: list[Event], extra: dict, networks: tuple[str, ...], subs: list[str], plants: list[str]) -> dict:
        e = self.e
        e.events, e.extra_load, e.tick = events, extra, 0
        e_util = c_util = w_short = 0.0
        e_asset = c_asset = w_asset = None
        e_watch = c_watch = w_watch = False
        excess_mwh = 0.0
        for h in range(24):
            t = e._targets(datetime(d.year, d.month, d.day, h, tzinfo=CAIRO))
            if "electricity" in networks:
                for s in subs:
                    u = t[f"{s}-load"] / SUB_CAPACITY_MW
                    if u > e_util:
                        e_util, e_asset = u, s
                    e_watch |= u >= PEAK_WARN
                    excess_mwh += max(0.0, t[f"{s}-load"] - SUB_CAPACITY_MW)
            if "cooling" in networks:
                for p in plants:
                    u = t[f"{p}-load"] / self.rating[p]
                    if u > c_util:
                        c_util, c_asset = u, p
                    c_watch |= t[f"{p}-load"] >= self.watch[p]
            if "water" in networks:
                for p in self.pumps:
                    short = PUMP_SETPOINT - t[f"{p}-pressure"]
                    if short > w_short:
                        w_short, w_asset = short, p
                w_watch |= any(t[f"{p}-pressure"] < self.pipe_min[p] for p in self.pipes)
        return {"electricity": (e_util, e_asset, e_watch), "cooling": (c_util, c_asset, c_watch), "water": (w_short, w_asset, w_watch), "excess_mwh": excess_mwh}


LIMITS = {
    "electricity": f"a substation above its firm capacity of {SUB_CAPACITY_MW:g} MW (warning from {PEAK_WARN:.0%})",
    "cooling": f"a cooling plant asked for more than the top of its normal operating range (warning from {COOLING_WATCH:.0%} of it)",
    "water": f"a district pump that can no longer hold its {PUMP_SETPOINT:g} bar discharge set-point (warning: a pipe under its normal pressure)",
}
CONDITIONS = {
    "normal": "An ordinary summer weekday",
    "heat": f"A heat wave of {HEAT_C:g} °C",
    "substation_loss": "The substation of the zone lost, or the one it backs up",
}


def _flag(network: str, util: float) -> bool:
    return util > 1.0 if network != "water" else util > 0.02  # water: the pump is short of its set-point by more than 0.02 bar


def evaluate(req: SitingRequest, seed: int = 42) -> SitingResult:
    reader = _Reader(seed)
    g = reader.e.g
    zone = g.zone_index.get(req.zone_id)
    if zone is None:
        raise KeyError(req.zone_id)
    kind = KINDS[req.kind]
    if req.kind == "custom":
        for field in ("water_m3h", "electricity_mw", "cooling_mw"):
            if getattr(req, field) is None:
                raise ValueError("A custom load needs water_m3h, electricity_mw and cooling_mw (use 0 for none).")
        w, el, co = (req.water_m3h, req.electricity_mw, req.cooling_mw)
        per = [(w, w * (1 - SPREAD), w * (1 + SPREAD)), (el, el * (1 - SPREAD), el * (1 + SPREAD)), (co, co * (1 - SPREAD), co * (1 + SPREAD))]
        assumptions = [SitingAssumption(name=n, value=v, low=lo, high=hi, unit=u, basis=f"Entered by the user; read as plus or minus {SPREAD:.0%}")
                       for n, (v, lo, hi), u in zip(("Water demand per facility", "Electrical demand per facility", "Cooling demand per facility"), per, ("m3/h", "MW", "MW thermal"))]
    else:
        per = [(x.value, x.low, x.high) for x in (kind.water, kind.electricity, kind.cooling)]
        assumptions = [kind.water, kind.electricity, kind.cooling]
    units = req.units
    nominal = tuple(units * v for v, _, _ in per)

    def extra_for(draw: tuple[float, float, float]) -> dict:
        return {req.zone_id: draw}

    rng = random.Random(seed)
    days = _days()
    packages = _packages()

    own = ZONE_SUBSTATION[zone.id]
    watched_subs = [own, SUBSTATION_BACKUP[own]]  # the only two substations the facility's load can reach
    plant = next((pid for pid, (_, _, served) in COOLING_PLANTS.items() if zone.id in served), None)
    plants = [plant] if plant else []  # a zone without district cooling puts its chillers on the electricity side instead
    trips = [own] + [s for s, b in SUBSTATION_BACKUP.items() if b == own]  # losing the zone's substation, or the neighbour it is the backup of

    def networks(name: str) -> tuple[str, ...]:
        return ("electricity",) if name == "substation_loss" else ("electricity", "water") + (("cooling",) if plant else ())

    def conds(name: str) -> list[list[Event]]:
        if name == "normal":
            return [[]]
        if name == "heat":
            return [[Event("heat_wave", None, HEAT_C)]]
        return [[Event("substation_trip", s, 1.0)] for s in trips]

    base_cache: dict = {}

    def worst(results: list[dict]) -> dict:
        out = {"excess_mwh": max(r["excess_mwh"] for r in results)}
        for n in ("electricity", "cooling", "water"):
            best = max(results, key=lambda r: r[n][0])
            out[n] = (best[n][0], best[n][1], any(r[n][2] for r in results))
        return out

    def read(d: date, name: str, extra: dict) -> dict:
        return worst([reader.day(d, ev, extra, networks(name), watched_subs, plants) for ev in conds(name)])

    samples = []
    for _ in range(req.runs):
        d = rng.choice(days)
        draw = tuple(units * (rng.triangular(lo, hi, v) if hi > lo else v) for v, lo, hi in per)
        samples.append((d, draw))

    results: list[ConditionResult] = []
    for name, label in CONDITIONS.items():
        nets = networks(name)
        before, after = [], []
        for d, draw in samples:
            key = (d, name)
            if key not in base_cache:
                base_cache[key] = read(d, name, {})
            before.append(base_cache[key])
            after.append(read(d, name, extra_for(draw)))
        risks = []
        for n in nets:
            pb = float(np.mean([_flag(n, r[n][0]) for r in before]))
            pa = float(np.mean([_flag(n, r[n][0]) for r in after]))
            hits = Counter(r[n][1] for r in after if _flag(n, r[n][0]) and r[n][1])
            top = hits.most_common(1)[0][0] if hits else None
            ub, ua = [r[n][0] for r in before], [r[n][0] for r in after]
            risks.append(NetworkRisk(
                network=n, asset=top, limit=LIMITS[n], probability_before=pb, probability_after=pa,
                watch_before=float(np.mean([r[n][2] for r in before])), watch_after=float(np.mean([r[n][2] for r in after])),
                utilisation_before_p95=float(np.percentile(ub, 95)) if n != "water" else None,
                utilisation_after_p95=float(np.percentile(ua, 95)) if n != "water" else None,
                utilisation_after_max=float(max(ua)) if n != "water" else None))
        extra_mwh = float(np.mean([a["excess_mwh"] - b["excess_mwh"] for a, b in zip(after, before)]))
        voll = DEFAULTS[f"voll_{zone.type}"]
        k = max(extra_mwh, 0.0) * 1000  # kWh
        results.append(ConditionResult(
            condition=name, label=label, days=len(samples), risks=risks,
            any_before=float(np.mean([any(_flag(n, r[n][0]) for n in nets) for r in before])),
            any_after=float(np.mean([any(_flag(n, r[n][0]) for n in nets) for r in after])),
            extra_unserved_mwh_per_day=extra_mwh, extra_unserved_cost_egp_per_day=k * voll["value"],
            extra_unserved_cost_low_egp_per_day=k * voll["low"], extra_unserved_cost_high_egp_per_day=k * voll["high"]))

    # what the facility needs from the utilities: one mitigation package per network that is left in breach and made worse, per limiting asset
    mitigations: list[Mitigation] = []
    for n in ("electricity", "cooling", "water"):
        assets, worst_after, worst_before = set(), 0.0, 0.0
        for c in results:
            for r in c.risks:
                if r.network == n and r.probability_after >= MITIGATE_AT and r.probability_after - r.probability_before >= MITIGATE_ADDS and r.asset:
                    assets.add(r.asset)
                    if r.probability_after > worst_after:
                        worst_after, worst_before = r.probability_after, r.probability_before
        pid = PACKAGE_OF[n]
        if assets and pid in packages:
            name, cost = packages[pid]
            mitigations.append(Mitigation(network=n, package=name, assets=sorted(assets), cost_m_egp=cost * len(assets),
                                          reason=f"With the facility {worst_after:.0%} of simulated days are in breach on {', '.join(sorted(assets))}, {worst_before:.0%} without it."))
    mit = sum(m.cost_m_egp for m in mitigations)
    capex = req.capex_m_egp if req.capex_m_egp is not None else kind.capex_m_egp
    basis = "Entered by the user" if req.capex_m_egp is not None else kind.capex_basis
    cost = SitingCost(capex_m_egp=capex, capex_basis=basis, mitigations=mitigations, mitigation_m_egp=mit, total_m_egp=None if capex is None else capex + mit,
                      note="Utility upgrades use the twin's placeholder package costs (integration/health/out/packages.csv), per limiting asset. Costs of the facility are not in the twin.")

    chillers = 0.0
    if nominal[2] and req.zone_id not in COOLED_ZONES:
        chillers = nominal[2] * CHILLER_BASE
    load = SitingLoad(water_m3h=nominal[0], electricity_mw=nominal[1] + chillers, cooling_mw=nominal[2] if req.zone_id in COOLED_ZONES else 0.0, on_site_chillers_mw=chillers)
    sz = SitingZone(id=zone.id, name=zone.name, type=zone.type, substation=ZONE_SUBSTATION[zone.id], pump=g.zone_pump[zone.id], cooling_plant=plant)
    return SitingResult(zone=sz, kind=req.kind, units=units, unit=kind.unit, load=load, assumptions=assumptions, conditions=results, cost=cost,
                        districts=_districts(zone.id), verdict=_verdict(results, sz), runs=req.runs, seed=seed)


def _verdict(results: list[ConditionResult], zone: SitingZone) -> str:
    """One sentence a person can repeat, composed from the numbers above."""
    pairs = [(c, r) for c in results for r in c.risks]
    c, r = max(pairs, key=lambda cr: cr[1].probability_after - cr[1].probability_before)
    when = c.label[0].lower() + c.label[1:]
    if r.probability_after - r.probability_before >= MITIGATE_ADDS:
        article = "an" if r.network[0] in "aeiou" else "a"
        return (f"In {zone.name} the facility raises the share of simulated days with {article} {r.network} breach from {r.probability_before:.0%} to {r.probability_after:.0%} "
                f"({when}), limited by {r.asset or 'the network'}. See the utility upgrades below.")
    return (f"In {zone.name} the facility pushes no network into breach by more than {MITIGATE_ADDS * 100:.0f} points of simulated days. "
            f"The largest change is {r.network}, from {r.probability_before:.0%} to {r.probability_after:.0%} ({when}).")


DEFAULT_UNITS = {"hospital": 300, "hospital_expansion": 100, "clinic": 3, "custom": 1}


def options() -> dict:
    """What the siting form offers: the zones with the networks that serve them, and the kinds of facility with their load assumptions."""
    g = Engine(42).g
    plants = {z: pid for pid, (_, _, served) in COOLING_PLANTS.items() for z in served}
    zones = [{"id": z.id, "name": z.name, "type": z.type, "substation": ZONE_SUBSTATION[z.id], "pump": g.zone_pump[z.id], "coolingPlant": plants.get(z.id)} for z in g.topology.zones]
    kinds = [{"id": k, "label": v.label, "unit": v.unit, "defaultUnits": DEFAULT_UNITS[k], "capexMEgp": v.capex_m_egp, "capexBasis": v.capex_basis,
              "assumptions": [a.dump() for a in (v.water, v.electricity, v.cooling)]} for k, v in KINDS.items()]
    return {"zones": zones, "kinds": kinds, "limits": LIMITS, "spread": SPREAD}
