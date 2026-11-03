"""Writes the tables the healthcare-investment project reads: what happens to water, electricity and cooling when one asset fails, and what it costs.

    PYTHONPATH=. .venv/bin/python scripts/export_for_health.py                 # integration/health/out/
    PYTHONPATH=. .venv/bin/python scripts/export_for_health.py --out /somewhere --seed 42

Every scenario is the same "what if this asset fails" run the agents use (`what_if` in app/agents/tools.py): a sandbox copy of the twin, the real
detector, and the real cost engine with the default assumptions. Nothing here is a new model. What is written:

- `scenarios.csv`        one row per failed asset: what the detector measured, and the cost and its P10/P50/P90 range.
- `scenario_zones.csv`   one row per (failed asset, district it affects): the share of the incident that district carries.
- `district_risk.csv`    one row per district of the healthcare project (`district_key` 1001..1015), joined through `integration/health/zone_mapping.csv`.
- `hospital_exposure.csv` one row per (failed asset, district with hospitals the failure reaches): how many hospitals, and how sure the match to our zones is.
- `districts_on_twin_map.csv` their `districts.csv` with the centroids moved to where the twin's map draws each district (same columns, names and areas).
- `joint_decision.csv`   one row per district: the twin's outage cost, their environmental stress and their hospitals side by side, and which of four groups the
                         district falls in. Nothing from the two projects is added together.
- `reliability_packages.csv`  ranked improvement packages (same shape as the healthcare project's package comparison), from `integration/health/packages.csv`.
- `MANIFEST.json`        seed, time, commit, assumptions used, SHA-256 of every file.
- `README.md`            what each column means and how to join.

The twin is simulated and the cost assumptions are planning values, so these numbers say how the method behaves on this twin, not what a real outage costs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
MAPPING = REPO / "integration" / "health" / "zone_mapping.csv"
DEFAULT_OUT = REPO / "integration" / "health" / "out"
AT = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)  # a Sunday, 10:00 in Cairo: a working day
KINDS = {"substation": "substation_trip", "pump": "pump_failure", "plant": "cooling_fault", "pipe": "pipe_burst"}
WARM_TICKS = 60


def r(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(float(x), n)


def read_mapping() -> list[dict]:
    with MAPPING.open(encoding="utf8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["zones"] = [z for z in row["our_zone_ids"].split(";") if z]
    return rows


def assets_to_run(g) -> list[str]:
    """Every substation, cooling plant, district pump and district pipe: the assets whose loss the twin can simulate."""
    by_kind: dict[str, list[str]] = {k: [] for k in KINDS}
    for a in g.topology.assets:
        if a.kind in by_kind and a.id not in by_kind[a.kind]:
            by_kind[a.kind].append(a.id)
    pumps = [p for p in g.zone_pump.values()]
    pipes = [p for p in g.zone_pipe.values()]
    by_kind["pump"] = [p for p in by_kind["pump"] if p in pumps]
    by_kind["pipe"] = [p for p in by_kind["pipe"] if p in pipes]
    return [a for k in ("substation", "plant", "pump", "pipe") for a in sorted(by_kind[k])]


async def build(seed: int, at: datetime = AT, assets: list[str] | None = None, keep: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    from app.agents.tools import what_if
    from app.cost import Assumptions, customer_key
    from app.detection import Detector
    from app.runtime import Runtime

    rt = Runtime(seed)
    det = Detector(rt)
    rt.detector = det
    rt.tick_hooks.append(det.tick)
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))  # the registered defaults, never a saved override
    for i in range(WARM_TICKS):
        await rt.tick_once(at + timedelta(seconds=2 * i))
    g = rt.engine.g
    names = {a.id: a.name for a in g.topology.assets}
    zone_type = {z.id: z.type for z in g.topology.zones}
    scenarios, zones = [], []
    for asset in assets or assets_to_run(g):
        kind = KINDS[g.asset_index[asset].kind]
        facts, rec, _ = await what_if(rt, asset, A)
        if keep is not None:
            keep[f"{kind}:{asset}"] = facts
            keep["A"] = A
        row = {"scenario_id": f"{kind}:{asset}", "kind": kind, "asset_id": asset, "asset_name": names[asset], "detected": facts is not None}
        if facts is None or rec is None:
            scenarios.append({**row, "affected_zones": "", "leak_m3h": None, "unserved_mw": None, "excess_mw": None, "cooling_deficit_tons": None,
                              "secondary_mw": None, "pumps_stopped": None, "delay_hours": None, "response_hours": None, "cost_per_hour_egp": None,
                              "act_now_egp": None, "cost_of_delay_egp": None, "saving_egp": None, "saving_p10_egp": None, "saving_p50_egp": None,
                              "saving_p90_egp": None, "needs_review": None})
            continue
        c = rec.cost
        scenarios.append({**row, "affected_zones": ";".join(facts.zone_types), "leak_m3h": r(facts.leak_m3h), "unserved_mw": r(facts.unserved_mw),
                          "excess_mw": r(facts.excess_mw), "cooling_deficit_tons": r(facts.cooling_deficit_tons), "secondary_mw": r(facts.secondary_mw),
                          "pumps_stopped": facts.pumps_stopped or (1 if kind == "pump_failure" else 0), "delay_hours": c.delay_hours, "response_hours": c.response_hours,
                          "cost_per_hour_egp": r(c.delay_per_hour, 2), "act_now_egp": r(c.act_now, 2), "cost_of_delay_egp": r(c.cost_of_delay, 2),
                          "saving_egp": r(c.expected_saving, 2), "saving_p10_egp": r(c.saving_range.p10, 2), "saving_p50_egp": r(c.saving_range.p50, 2),
                          "saving_p90_egp": r(c.saving_range.p90, 2), "needs_review": c.review})
        customers = {z: A[customer_key(z)] for z in facts.zone_types}
        total = sum(customers.values())
        for z in facts.zone_types:
            share = customers[z] / total
            # only the cost terms that belong to one district (energy not served there, and the notification and support work there)
            own = sum(i.amount for i in c.breakdown if i.group.endswith(f":{z}"))
            zones.append({"scenario_id": row["scenario_id"], "kind": kind, "asset_id": asset, "zone_id": z, "zone_type": zone_type[z],
                          "customers": customers[z], "customer_share": r(share), "unserved_mw": r(facts.unserved_mw * share),
                          "zone_cost_of_delay_egp": r(own, 2)})
    meta = {"seed": seed, "at": at.isoformat(), "warm_ticks": WARM_TICKS, "assets": len(scenarios),
            "assumptions": "registered defaults (backend/app/cost/__init__.py DEFAULTS), no saved override"}
    return scenarios, zones, meta


def district_risk(mapping: list[dict], scenarios: list[dict], zones: list[dict]) -> list[dict]:
    """One row per district of the healthcare project. A direct district takes its own zone's numbers. A type district takes the average over the
    zones of that type (each zone first, then the mean). A district with no counterpart is written with empty numbers and match_kind none."""
    def per_zone(zone: str) -> dict:
        hits = [z for z in zones if z["zone_id"] == zone]
        worst = max(hits, key=lambda z: z["zone_cost_of_delay_egp"] or 0, default=None)
        return {"n": len(hits), "cost": worst["zone_cost_of_delay_egp"] if worst else 0.0, "mw": max((z["unserved_mw"] or 0 for z in hits), default=0.0),
                "scenario": worst["scenario_id"] if worst and worst["zone_cost_of_delay_egp"] else ""}

    out = []
    for m in mapping:
        row = {"district_key": m["district_key"], "district_code": m["district_code"], "match_kind": m["match_kind"], "our_zone_ids": m["our_zone_ids"],
               "scenarios_affecting": None, "worst_zone_cost_of_delay_egp": None, "worst_zone_unserved_mw": None, "worst_scenario_id": None}
        if m["zones"]:
            parts = [per_zone(z) for z in m["zones"]]
            row["scenarios_affecting"] = r(sum(p["n"] for p in parts) / len(parts), 2)
            row["worst_zone_cost_of_delay_egp"] = r(sum(p["cost"] for p in parts) / len(parts), 2)
            row["worst_zone_unserved_mw"] = r(sum(p["mw"] for p in parts) / len(parts))
            row["worst_scenario_id"] = max(parts, key=lambda p: p["cost"])["scenario"] if m["match_kind"] == "direct" else ""
        out.append(row)
    return out


HEALTH_DATA = REPO / "integration" / "health" / "data"
BAND_LOW, BAND_HIGH = 20.0, 35.0  # their Environmental Stress Index bands: below 20 low, 20 to under 35 moderate, 35 and above high


def read_facility_counts() -> dict[str, dict]:
    """Hospitals, clinics and specialists per district_key, from their facilities file (copied unchanged into integration/health/data)."""
    counts: dict[str, dict] = {}
    with (HEALTH_DATA / "healthcare_facilities.csv").open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            c = counts.setdefault(row["district_key"], {"hospitals": 0, "clinics": 0, "specialists": 0})
            c[{"hospital": "hospitals", "clinic": "clinics", "doctors": "specialists"}[row["fclass"]]] += 1
    return counts


def read_environment() -> tuple[str, dict[str, dict]]:
    """Their stress scores per district_key for the latest month of their panel, as they wrote them."""
    with (HEALTH_DATA / "environment_monthly.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    month = max(row["month"] for row in rows)
    return month[:7], {row["district_key"]: row for row in rows if row["month"] == month}


PACKAGE_LABELS = {"center_prevention": "A New center + prevention", "capacity": "B Expand existing capacity", "access": "C Transport + triage access", "prevention": "D Prevention only"}


def read_their_packages() -> dict[str, dict]:
    """Their what-if engine's figures per district (see integration/health/extract_packages.py), as extracted. Empty when the extract is not there."""
    path = HEALTH_DATA / "their_packages.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {row["district_key"]: {k: float(v) for k, v in row.items() if k != "district_key"} for row in csv.DictReader(f)}


def their_side(t: dict | None) -> dict:
    """The hospital pressure of a district in their engine and its best package: the one that leaves the lowest pressure (the cheaper one on a tie), which is how
    their own ranking orders the packages when one district is the target. Nothing is computed here beyond picking the lowest of their own figures."""
    cols = ("their_pressure_baseline", "their_over_capacity", "their_pressure_growth_only", "their_best_package", "their_best_package_pressure", "their_best_package_cost_m_egp")
    if not t:
        return dict.fromkeys(cols)
    best = min(PACKAGE_LABELS, key=lambda p: (round(t[f"{p}_pressure"], 6), t[f"{p}_cost_m_egp"]))
    return {"their_pressure_baseline": t["pressure_baseline"], "their_over_capacity": t["pressure_baseline"] > 1.0, "their_pressure_growth_only": t["pressure_growth_only"],
            "their_best_package": PACKAGE_LABELS[best], "their_best_package_pressure": t[f"{best}_pressure"], "their_best_package_cost_m_egp": t[f"{best}_cost_m_egp"]}


def districts_on_twin_map(zone_centers: dict) -> list[dict]:
    """Their `districts.csv` as they sent it (same columns, same order, same names and areas) with the centroid replaced by where the twin's map draws the district:
    the centre of the zone it is matched to, or a point of a small ring around it when several districts share a zone (see app/city.py). A district with no
    counterpart in the twin (the park) has no place on our map, so it keeps the centroid of their own file."""
    from app.city import CityData

    city = CityData(zone_centers=zone_centers)
    placed = {d.district_key: d.placed_at for d in city.districts().districts}
    with (HEALTH_DATA / "districts.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        at = placed.get(row["district_key"])
        if at is not None:
            row["centroid_lon"], row["centroid_lat"] = f"{at.lng:.6f}", f"{at.lat:.6f}"
    return rows


def hospital_exposure(mapping: list[dict], scenarios: list[dict], counts: dict[str, dict]) -> list[dict]:
    """Which hospitals a failure can reach. A hospital is placed by the district of their file it belongs to, and that district by `zone_mapping.csv`, so the
    placement is only as good as the mapping: `all_matched_zones` means every zone the district is matched to is hit (always so for a direct district),
    `some_matched_zones` means only some are, so the district may or may not be affected and the count is an upper bound. The twin has no link from a hospital
    to a substation, pump or plant, only from a zone to them."""
    out = []
    for s in scenarios:
        if not s["detected"] or not s["affected_zones"]:
            continue
        hit_zones = set(s["affected_zones"].split(";"))
        for m in mapping:
            c = counts.get(m["district_key"], {"hospitals": 0, "clinics": 0, "specialists": 0})
            hit = [z for z in m["zones"] if z in hit_zones]
            if not c["hospitals"] or not hit:
                continue
            out.append({"scenario_id": s["scenario_id"], "kind": s["kind"], "asset_id": s["asset_id"], "district_key": m["district_key"],
                        "district_code": m["district_code"], "match_kind": m["match_kind"], "matched_zones": ";".join(m["zones"]), "zones_hit": ";".join(hit),
                        "certainty": "all_matched_zones" if len(hit) == len(m["zones"]) else "some_matched_zones", "hospitals": c["hospitals"],
                        "clinics": c["clinics"], "specialists": c["specialists"]})
    return out


def _median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def joint_decision(mapping: list[dict], risk: list[dict], exposure: list[dict], counts: dict[str, dict], env: dict[str, dict], env_month: str,
                   theirs: dict[str, dict] | None = None) -> list[dict]:
    """One row per district with the two sides next to each other and no combined number. The group says whether each side is above or below the median of
    the districts that have it (a relative reading: both ranges are narrow, and no threshold of ours is invented)."""
    risk_by = {x["district_key"]: x for x in risk}
    rows = []
    for m in mapping:
        k = m["district_key"]
        c = counts.get(k, {"hospitals": 0, "clinics": 0, "specialists": 0})
        e = env[k]
        x = risk_by[k]
        mine = [h for h in exposure if h["district_key"] == k]
        scores = {n: float(e[f"esi_{n}"]) for n in ("air", "waste", "water", "heat")}
        overall = float(e["environmental_stress_index"])
        rows.append({"district_key": k, "district_code": m["district_code"], "district_name_ar": m["district_name_ar"], "match_kind": m["match_kind"],
                     "our_zone_ids": m["our_zone_ids"], "hospitals": c["hospitals"], "clinics": c["clinics"], "specialists": c["specialists"],
                     "scenarios_affecting": x["scenarios_affecting"], "worst_zone_cost_of_delay_egp": x["worst_zone_cost_of_delay_egp"],
                     "worst_zone_unserved_mw": x["worst_zone_unserved_mw"], "worst_scenario_id": x["worst_scenario_id"],
                     "scenarios_reaching_hospitals_sure": sum(h["certainty"] == "all_matched_zones" for h in mine),
                     "scenarios_reaching_hospitals_possible": sum(h["certainty"] == "some_matched_zones" for h in mine),
                     "env_month": env_month, "esi_overall": overall, "esi_air": scores["air"], "esi_waste": scores["waste"], "esi_water": scores["water"],
                     "esi_heat": scores["heat"], "esi_band": "high" if overall >= BAND_HIGH else "moderate" if overall >= BAND_LOW else "low",
                     "highest_score": max(scores, key=scores.get), **their_side((theirs or {}).get(k))})
    cost_med = _median([r["worst_zone_cost_of_delay_egp"] for r in rows if r["worst_zone_cost_of_delay_egp"] is not None])
    env_med = _median([r["esi_overall"] for r in rows])
    for r_ in rows:
        if r_["worst_zone_cost_of_delay_egp"] is None:
            r_["outage_cost_vs_median"], r_["group"] = "", "no_outage_data"
            r_["environment_vs_median"] = "higher" if r_["esi_overall"] > env_med else "lower"
            continue
        o = "higher" if r_["worst_zone_cost_of_delay_egp"] > cost_med else "lower"
        v = "higher" if r_["esi_overall"] > env_med else "lower"
        r_["outage_cost_vs_median"], r_["environment_vs_median"] = o, v
        r_["group"] = {("higher", "higher"): "both_higher", ("higher", "lower"): "outage_higher", ("lower", "higher"): "environment_higher", ("lower", "lower"): "both_lower"}[(o, v)]
    return rows


PACKAGES = REPO / "integration" / "health" / "packages.csv"
DEFAULT_BUDGET_M_EGP = 15.0


def read_packages() -> list[dict]:
    with PACKAGES.open(encoding="utf8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["cost"], row["value"] = float(row["cost_m_egp_5yr_per_asset"]), float(row["effect_value"])
    return rows


def event_cost(facts, A, response_hours: float | None = None) -> float:
    """What one such failure costs when a crew is sent now: the repair, plus the hourly cost until the asset is back (response time and on-site work),
    the way the board's plan counts it (see app/board/analysis.py)."""
    from app.cost import _summary, policy_kind
    n = _summary(facts, A)
    kind = policy_kind(facts)
    hours = (A["response_hours"] if response_hours is None else response_hours) + A[f"fix_hours_{kind}"]
    return n["repair_now"] + n["c_delay"] * hours


def rank_packages(facts_by_scenario: dict, A, packages: list[dict], mapping: list[dict], budget_m_egp: float = DEFAULT_BUDGET_M_EGP) -> list[dict]:
    """For every (package, asset it applies to): what one failure of that asset costs now, what it costs with the package, how many such failures in five
    years it takes for the package to pay for itself, and whether it fits the budget when the best ones are funded first. All costs come from the cost
    engine; only the package cost and its effect are the placeholders of packages.csv."""
    import dataclasses
    from app.cost import _summary, policy_kind

    rows = []
    for sid, facts in facts_by_scenario.items():
        if sid == "A" or facts is None:
            continue
        kind, asset = sid.split(":", 1)
        for pkg in packages:
            if pkg["applies_to"] != kind:
                continue
            base = event_cost(facts, A)
            if pkg["effect"] == "residual_unserved_fraction":
                after = event_cost(dataclasses.replace(facts, unserved_mw=facts.unserved_mw * pkg["value"]), A)
            elif pkg["effect"] == "residual_cooling_deficit_fraction":
                after = event_cost(dataclasses.replace(facts, cooling_deficit_tons=facts.cooling_deficit_tons * pkg["value"]), A)
            elif pkg["effect"] == "planned_instead_of_emergency":
                after = _summary(facts, A)["repair_now"] * A["planned_factor"]
            elif pkg["effect"] == "response_hours_with_package":
                after = event_cost(facts, A, response_hours=pkg["value"])
            else:
                raise ValueError(f"unknown package effect {pkg['effect']}")
            avoided = max(0.0, base - after)
            cost = pkg["cost"] * 1e6
            zones = list(facts.zone_types)
            direct = [m["district_key"] for m in mapping if m["match_kind"] == "direct" and m["zones"] and all(z in zones for z in m["zones"])]
            rows.append({"package_id": pkg["package_id"], "package_name": pkg["package_name"], "asset_id": asset, "zones_protected": ";".join(zones),
                         "direct_districts_protected": ";".join(direct), "cost_m_egp_5yr": pkg["cost"], "event_cost_egp": r(base, 2),
                         "event_cost_with_package_egp": r(after, 2), "avoided_per_event_egp": r(avoided, 2),
                         "break_even_events_5yr": r(cost / avoided, 2) if avoided > 0 else None, "basis": pkg["basis"]})
    rows.sort(key=lambda x: (x["break_even_events_5yr"] is None, x["break_even_events_5yr"] or 0, x["cost_m_egp_5yr"], x["asset_id"]))
    spent = 0.0
    for i, row in enumerate(rows, 1):
        spent += row["cost_m_egp_5yr"]
        row["rank"], row["cumulative_cost_m_egp"] = i, round(spent, 2)
        row["budget_status"] = "Funded" if spent <= budget_m_egp else "Over budget"
    order = ["rank", "package_id", "package_name", "asset_id", "zones_protected", "direct_districts_protected", "cost_m_egp_5yr", "cumulative_cost_m_egp",
             "budget_status", "event_cost_egp", "event_cost_with_package_egp", "avoided_per_event_egp", "break_even_events_5yr", "basis"]
    return [{k: row[k] for k in order} for row in rows]


def write_csv(path: Path, rows: list[dict], lineterminator: str = "\r\n") -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator=lineterminator)
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_M_EGP, help="budget in M EGP for the Funded / Over budget column of reliability_packages.csv")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    kept: dict = {}
    scenarios, zones, meta = asyncio.run(build(args.seed, keep=kept))
    mapping = read_mapping()
    risk = district_risk(mapping, scenarios, zones)
    ranked = rank_packages(kept, kept["A"], read_packages(), mapping, args.budget)
    counts = read_facility_counts()
    exposure = hospital_exposure(mapping, scenarios, counts)
    env_month, env = read_environment()
    theirs = read_their_packages()
    joint = joint_decision(mapping, risk, exposure, counts, env, env_month, theirs)
    from app.sim.topology import build as build_topology

    on_map = districts_on_twin_map({z.id: z.center for z in build_topology().topology.zones})
    meta["budget_m_egp"] = args.budget
    meta["environment_month"] = env_month
    files = {"scenarios.csv": scenarios, "scenario_zones.csv": zones, "district_risk.csv": risk, "hospital_exposure.csv": exposure, "joint_decision.csv": joint, "districts_on_twin_map.csv": on_map,
             "reliability_packages.csv": ranked}
    for name, rows in files.items():
        write_csv(out / name, rows, "\n" if name == "districts_on_twin_map.csv" else "\r\n")  # a copy of their file keeps its line endings
    (out / "zone_mapping.csv").write_bytes(MAPPING.read_bytes())
    (out / "packages.csv").write_bytes(PACKAGES.read_bytes())
    if theirs:
        (out / "their_packages.csv").write_bytes((HEALTH_DATA / "their_packages.csv").read_bytes())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    manifest = {**meta, "written_at": datetime.now(timezone.utc).isoformat(), "git_commit": commit, "simulated": True,
                "files": {n: {"rows": len(rows), "sha256": sha(out / n)} for n, rows in files.items()} | {n: {"rows": len(rows), "sha256": sha(out / n)} for n, rows in (("zone_mapping.csv", mapping), ("packages.csv", read_packages())) + ((("their_packages.csv", list(theirs)),) if theirs else ())}}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    (out / "README.md").write_text(README)
    print(f"wrote {len(scenarios)} scenarios, {len(zones)} scenario-zone rows, {len(risk)} districts, {len(exposure)} hospital exposures, {len(ranked)} ranked packages to {out}")
    assert all(not (isinstance(v, float) and math.isnan(v)) for row in scenarios for v in row.values())


README = """# Tables for the healthcare-investment project

Made by `backend/scripts/export_for_health.py` from the UtilityGuard digital twin of the New Administrative Capital. `MANIFEST.json` says how (seed, commit,
SHA-256 of every file). **The twin is simulated and the cost figures use planning assumptions, so these tables show how the method behaves, not what a real
outage would cost.**

## How to join

`district_key` (1001..1015, the key of `master_panel.csv`) -> `district_risk.csv` directly. For detail go through `zone_mapping.csv` (`district_key` -> our zone
ids) and then `scenario_zones.csv` (`zone_id`) and `scenarios.csv` (`scenario_id`). Nothing in your files is changed by this.

`match_kind` says how good the match is: `direct` (the same district in both projects: 1002, 1009, 1010, 1012), `type` (no equal district; the numbers are the
average of our zones of the same type, so read them as "a residential district", not as that district), `none` (no counterpart, empty numbers).

## scenarios.csv (one row per failed asset)

| Column | Meaning |
| --- | --- |
| `scenario_id` | `<kind>:<asset_id>` |
| `kind` | `substation_trip`, `pump_failure`, `cooling_fault`, `pipe_burst` |
| `detected` | The twin's detector raised an incident within the run. When false the numeric columns are empty |
| `affected_zones` | Our zones hit, separated by `;` |
| `leak_m3h`, `unserved_mw`, `excess_mw`, `cooling_deficit_tons`, `secondary_mw`, `pumps_stopped` | What the detector measured while the asset was down: water lost (m3/h), electricity demand not served (MW), excess draw (MW), cooling shortfall (tons), electricity bought for a dependent network (MW), pumps stopped (for `pump_failure` the failed pump itself counts) |
| `delay_hours`, `response_hours` | The deferred-response horizon and the crew response time of the cost model (assumptions) |
| `cost_per_hour_egp` | Cost of the incident for each hour it stays open |
| `act_now_egp`, `cost_of_delay_egp`, `saving_egp` | Cost of sending a crew now, of waiting `delay_hours`, and the difference |
| `saving_p10_egp`, `saving_p50_egp`, `saving_p90_egp` | Range of the saving when every assumption varies inside its registered range (800 seeded runs) |
| `needs_review` | The saving falls outside the plausible range of the cost model |

## scenario_zones.csv (one row per failed asset and affected zone)

`customer_share` is the zone's share of the affected service accounts; `unserved_mw` is the incident's unserved load split by that share (the way the cost
model splits it); `zone_cost_of_delay_egp` holds only the cost terms that belong to that zone (energy not served there and the notification and support
work there). Costs shared by the whole incident (repair, water lost, cooling, secondary) are in `scenarios.csv` only, so summing the zone column does not double count.

## district_risk.csv (one row per district_key)

`scenarios_affecting`: how many of the failed-asset scenarios touch the district (an average over the zones when `match_kind` is `type`).
`worst_zone_cost_of_delay_egp` and `worst_zone_unserved_mw`: the largest of the zone's values over all scenarios (averaged over zones for `type`).
`worst_scenario_id`: which scenario it was (only for `direct`).

## districts_on_twin_map.csv

Your `districts.csv` with the same columns, the same order and the same names, codes and areas. Only `centroid_lon` and `centroid_lat` differ: they are where the twin's map
draws each district, which is the centre of the zone of the twin the district is matched to by name and kind (`zone_mapping.csv`), or a point on a small ring (about
550 m) around it when several districts share one zone. They are drawing positions to put both projects on one map, **not measured centroids or boundaries** of your
districts. `NAC-PARK` has no counterpart in the twin, so it keeps the centroid of your file.

## hospital_exposure.csv (one row per failed asset and district with hospitals that it reaches)

Their hospitals are placed by the district of their file, and the district by `zone_mapping.csv`; the twin links a zone, not a hospital, to its substation, pump
and plant. `certainty` says how far to trust a row: `all_matched_zones` (every zone the district is matched to is hit; always so for a `direct` district) or
`some_matched_zones` (only some are, so the district may not be affected at all: read `hospitals` as an upper bound). `hospitals`, `clinics`, `specialists` are
the counts of the district in their facilities file.

## joint_decision.csv (one row per district_key)

The twin's side and their side next to each other, never added together: `worst_zone_cost_of_delay_egp`, `worst_zone_unserved_mw`, `scenarios_affecting` and the two
`scenarios_reaching_hospitals_*` counts are ours; `esi_*` (their 0 to 100 stress scores for `env_month`, with their bands: below 20 low, 20 to under 35 moderate,
35 and above high) and the facility counts are theirs. `outage_cost_vs_median` and `environment_vs_median` say whether the district is above or below the median of
the districts that have the figure (a relative reading: no threshold of ours is invented, and all overall scores sit in one band). `group` is `both_higher`,
`outage_higher`, `environment_higher`, `both_lower`, or `no_outage_data` (no counterpart in the twin).

## their side of joint_decision.csv, and their_packages.csv

`their_pressure_baseline` is the pressure ratio (demand over capacity) of the district in **their** what-if engine in its default stress-calibrated view; above 1.0
(`their_over_capacity`) the district is over 1.0 in that view, which their app describes as a scenario visualization, not observed utilization. `their_pressure_growth_only` is the same district after their default population growth with no package, and
`their_best_package` is the one of their four packages that leaves the lowest pressure (the cheaper on a tie), with its pressure and its 5-year cost
(`their_best_package_pressure`, `their_best_package_cost_m_egp`). All of it is their engine's output, called unchanged once per district with that district as
the only target; `their_packages.csv` has the figures of every package, and `integration/health/data/PACKAGES_SOURCE.json` the settings, their commit and the
SHA-256 of their files. Their package costs are placeholders of their own configuration. These columns are empty when the extract is not there.

## reliability_packages.csv (ranked improvement packages)

Same idea as the package comparison of the healthcare dashboard: one row per (package, asset it protects), best first.

| Column | Meaning |
| --- | --- |
| `rank` | Fewest failures needed to pay the package back comes first; ties go to the cheaper package |
| `cost_m_egp_5yr`, `cumulative_cost_m_egp`, `budget_status` | Placeholder five-year cost (from `packages.csv`), the running total down the ranking, and `Funded` while the running total stays inside the budget (`--budget`, default 15 M EGP) |
| `event_cost_egp` | What one failure of that asset costs when a crew is sent now: repair plus the hourly cost until the asset is back (response time and on-site work), as the board's plan counts it. From the cost engine |
| `event_cost_with_package_egp`, `avoided_per_event_egp` | The same failure with the package in place, and the difference |
| `break_even_events_5yr` | Package cost divided by the cost avoided per failure: how many such failures in five years it takes for the package to pay for itself. Empty when it avoids nothing |
| `zones_protected`, `direct_districts_protected` | Our zones the failure hits, and the districts of the healthcare project that are the same district as one of them (`match_kind` `direct`) |

The package costs and effects are **placeholders** in `packages.csv` (one editable file, like the cost and price model of the proposal); the failure costs are the
cost engine's. No failure rate is assumed: the table says how many failures are needed, and the reader judges whether that many is likely.

## What these tables do not contain

Water quality (E. coli, chlorine, turbidity), air quality and waste: the twin models flow, pressure, electricity and cooling, not chemistry or emissions.
"""

if __name__ == "__main__":
    main()
