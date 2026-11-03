"""The tables written for the healthcare-investment project: the mapping is complete, the numbers are the cost engine's, and a run is repeatable."""

import asyncio
import csv
import json

import pytest

from app.sim.topology import build
from scripts import export_for_health as ex

SMALL = ["elec-sub-04", "water-pipe-01", "water-pump-04", "cool-plant-02"]


@pytest.fixture(scope="module")
def tables():
    return asyncio.run(ex.build(42, assets=SMALL))


def test_every_district_of_the_other_project_is_mapped_to_real_zones_or_marked_none():
    mapping = ex.read_mapping()
    g = build()
    zones = {z.id: z.type for z in g.topology.zones}
    assert [m["district_key"] for m in mapping] == [str(k) for k in range(1001, 1016)]
    for m in mapping:
        assert m["match_kind"] in ("direct", "type", "none")
        assert all(z in zones for z in m["zones"]), m
        if m["match_kind"] == "direct":
            assert len(m["zones"]) == 1
        if m["match_kind"] == "type":
            assert len(m["zones"]) >= 1 and {zones[z] for z in m["zones"]} == {m["our_zone_type"]}
        if m["match_kind"] == "none":
            assert m["zones"] == []


def test_a_district_listed_as_direct_has_the_same_name_in_both_projects():
    direct = {m["district_key"]: m["zones"][0] for m in ex.read_mapping() if m["match_kind"] == "direct"}
    assert direct == {"1002": "zone-res-2", "1009": "zone-cbd", "1010": "zone-gov", "1012": "zone-cult"}


def test_scenario_rows_are_detected_complete_and_in_range(tables):
    scenarios, zones, meta = tables
    assert [s["asset_id"] for s in scenarios] == SMALL and meta["seed"] == 42
    for s in scenarios:
        assert s["detected"] and s["affected_zones"]
        for key in ("leak_m3h", "unserved_mw", "cooling_deficit_tons", "cost_per_hour_egp", "act_now_egp", "cost_of_delay_egp"):
            assert s[key] is not None and s[key] >= 0, (s["scenario_id"], key)
        assert s["saving_p10_egp"] <= s["saving_p50_egp"] <= s["saving_p90_egp"]
    pump = next(s for s in scenarios if s["kind"] == "pump_failure")
    assert pump["pumps_stopped"] == 1


def test_zone_rows_split_the_incident_without_losing_or_inventing_anything(tables):
    scenarios, zones, _ = tables
    for s in scenarios:
        mine = [z for z in zones if z["scenario_id"] == s["scenario_id"]]
        assert {z["zone_id"] for z in mine} == set(s["affected_zones"].split(";"))
        assert sum(z["customer_share"] for z in mine) == pytest.approx(1, abs=1e-3)
        assert sum(z["unserved_mw"] for z in mine) == pytest.approx(s["unserved_mw"], abs=1e-2)
        # zone costs hold only zone terms, so together they cannot exceed the incident's cost of delay
        assert sum(z["zone_cost_of_delay_egp"] for z in mine) <= s["cost_of_delay_egp"] + 0.01


def test_the_cost_in_the_table_is_what_the_cost_engine_gives_for_the_same_incident(tables):
    """The export adds no model: re-run one what-if and compare the cost to the cent."""
    from app.agents.tools import what_if
    from app.cost import Assumptions
    from app.detection import Detector
    from app.runtime import Runtime
    from datetime import timedelta
    from pathlib import Path

    scenarios, _, _ = tables

    async def again():
        rt = Runtime(42)
        det = Detector(rt)
        rt.detector = det
        rt.tick_hooks.append(det.tick)
        for i in range(ex.WARM_TICKS):
            await rt.tick_once(ex.AT + timedelta(seconds=2 * i))
        return await what_if(rt, "elec-sub-04", Assumptions(path=Path("/nonexistent/a.json")))

    _, rec, _ = asyncio.run(again())
    row = next(s for s in scenarios if s["asset_id"] == "elec-sub-04")
    assert row["cost_of_delay_egp"] == round(rec.cost.cost_of_delay, 2) and row["act_now_egp"] == round(rec.cost.act_now, 2)


def test_a_second_run_gives_the_same_rows(tables):
    again = asyncio.run(ex.build(42, assets=SMALL))
    assert again[0] == tables[0] and again[1] == tables[1]


def test_district_risk_follows_the_mapping(tables):
    scenarios, zones, _ = tables
    risk = {r["district_key"]: r for r in ex.district_risk(ex.read_mapping(), scenarios, zones)}
    assert len(risk) == 15
    assert risk["1015"]["match_kind"] == "none" and risk["1015"]["worst_zone_cost_of_delay_egp"] is None
    assert risk["1002"]["match_kind"] == "direct" and risk["1002"]["worst_zone_cost_of_delay_egp"] is not None
    residential = {k: risk[k]["worst_zone_cost_of_delay_egp"] for k in ("1001", "1003", "1004", "1005", "1006", "1007", "1008")}
    assert len(set(residential.values())) == 1  # a type match gives every district of that type the same numbers, and says so in match_kind
    assert all(risk[k]["match_kind"] == "type" and risk[k]["worst_scenario_id"] == "" for k in residential)


def test_the_files_are_written_with_a_manifest_that_matches(tmp_path, tables):
    scenarios, zones, meta = tables
    risk = ex.district_risk(ex.read_mapping(), scenarios, zones)
    ex.write_csv(tmp_path / "scenarios.csv", scenarios)
    ex.write_csv(tmp_path / "district_risk.csv", risk)
    with (tmp_path / "scenarios.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(SMALL) and rows[0]["scenario_id"] == "substation_trip:elec-sub-04"
    assert "NaN" not in (tmp_path / "scenarios.csv").read_text() and json.dumps(meta)


@pytest.fixture(scope="module")
def ranked():
    kept: dict = {}
    scenarios, zones, _ = asyncio.run(ex.build(42, assets=SMALL, keep=kept))
    return scenarios, ex.rank_packages(kept, kept["A"], ex.read_packages(), ex.read_mapping(), budget_m_egp=10.0), kept


def test_packages_config_only_uses_kinds_and_effects_the_ranking_understands():
    known = {"substation_trip", "pump_failure", "cooling_fault", "pipe_burst"}
    effects = {"residual_unserved_fraction", "residual_cooling_deficit_fraction", "planned_instead_of_emergency", "response_hours_with_package"}
    for p in ex.read_packages():
        assert p["applies_to"] in known and p["effect"] in effects and p["cost"] > 0 and p["basis"].startswith("Placeholder")


def test_event_cost_is_the_boards_definition_by_hand(ranked):
    """repair + hourly cost x (response hours + on-site hours), recomputed from the incident-level numbers of scenarios.csv."""
    scenarios, rows, _ = ranked
    sub = next(s for s in scenarios if s["asset_id"] == "elec-sub-04")
    row = next(x for x in rows if x["asset_id"] == "elec-sub-04")
    repair = 3400.0 + 26000.0  # crew call-out + substation parts, the registered defaults
    assert row["event_cost_egp"] == pytest.approx(repair + sub["cost_per_hour_egp"] * (1.0 + 1.5), abs=1.0)
    assert row["event_cost_with_package_egp"] < row["event_cost_egp"]


def test_the_ranking_is_ordered_budgeted_and_never_pays_for_nothing(ranked):
    _, rows, _ = ranked
    assert [x["rank"] for x in rows] == list(range(1, len(rows) + 1))
    be = [x["break_even_events_5yr"] for x in rows]
    assert all(b is not None for b in be) and be == sorted(be)
    assert all(x["avoided_per_event_egp"] >= 0 and x["event_cost_with_package_egp"] <= x["event_cost_egp"] for x in rows)
    spent = 0.0
    for x in rows:
        spent += x["cost_m_egp_5yr"]
        assert x["cumulative_cost_m_egp"] == pytest.approx(spent, abs=0.01)
        assert (x["budget_status"] == "Funded") == (spent <= 10.0)
    assert all(x["break_even_events_5yr"] == pytest.approx(x["cost_m_egp_5yr"] * 1e6 / x["avoided_per_event_egp"], rel=1e-3) for x in rows)


def test_a_planned_pump_repair_costs_planned_factor_of_the_emergency_repair(ranked):
    _, rows, _ = ranked
    pump = next(x for x in rows if x["package_id"] == "P3")
    assert pump["event_cost_with_package_egp"] == pytest.approx((3400.0 + 42000.0) * 0.6, abs=0.01)


def test_changing_a_package_effect_changes_the_result(ranked):
    """The placeholder is read from the file, not baked in: a package that leaves all the load unserved avoids nothing."""
    _, _, kept = ranked
    pkgs = ex.read_packages()
    for p in pkgs:
        if p["package_id"] == "P1":
            p["value"] = 1.0
    rows = ex.rank_packages(kept, kept["A"], pkgs, ex.read_mapping(), budget_m_egp=10.0)
    p1 = next(x for x in rows if x["package_id"] == "P1")
    assert p1["avoided_per_event_egp"] == 0 and p1["break_even_events_5yr"] is None and p1["rank"] == len(rows)


# ---------- hospital exposure and the joint decision table ----------
def _map(key, kind, zones):
    return {"district_key": key, "district_code": f"NAC-{key}", "district_name_ar": key, "match_kind": kind, "our_zone_ids": ";".join(zones), "zones": zones}


def _scn(sid, zones):
    return {"scenario_id": sid, "kind": "substation_trip", "asset_id": sid.split(":")[1], "detected": True, "affected_zones": ";".join(zones)}


def test_the_facility_counts_are_the_ones_in_their_file():
    counts = ex.read_facility_counts()
    assert sum(c["hospitals"] for c in counts.values()) == 30 and sum(c["hospitals"] + c["clinics"] + c["specialists"] for c in counts.values()) == 81
    assert counts["1013"]["hospitals"] == 8 and set(counts) == {str(k) for k in range(1001, 1016)}


def test_exposure_says_how_sure_each_row_is_and_skips_what_is_not_reached():
    mapping = [_map("1", "direct", ["zone-a"]), _map("2", "type", ["zone-a", "zone-b"]), _map("3", "type", ["zone-a"]), _map("4", "direct", ["zone-c"]), _map("5", "none", [])]
    counts = {k: {"hospitals": 2, "clinics": 1, "specialists": 0} for k in "12345"} | {"3": {"hospitals": 0, "clinics": 4, "specialists": 0}}
    rows = ex.hospital_exposure(mapping, [_scn("substation_trip:s1", ["zone-a"]), {**_scn("pump_failure:p1", ["zone-a"]), "detected": False}], counts)
    got = {r["district_key"]: r for r in rows}
    assert set(got) == {"1", "2"}  # 3 has no hospital, 4 is not hit, 5 has no zone, and an undetected scenario gives nothing
    assert got["1"]["certainty"] == "all_matched_zones" and got["2"]["certainty"] == "some_matched_zones" and got["2"]["zones_hit"] == "zone-a"
    assert all(r["scenario_id"] == "substation_trip:s1" and r["hospitals"] == 2 for r in rows)


def test_exposure_marks_a_type_district_sure_only_when_every_matched_zone_is_hit():
    mapping = [_map("2", "type", ["zone-a", "zone-b"])]
    counts = {"2": {"hospitals": 3, "clinics": 0, "specialists": 0}}
    both = ex.hospital_exposure(mapping, [_scn("substation_trip:s", ["zone-a", "zone-b", "zone-c"])], counts)
    assert both[0]["certainty"] == "all_matched_zones"


def _risk(key, cost):
    return {"district_key": key, "scenarios_affecting": 1.0, "worst_zone_cost_of_delay_egp": cost, "worst_zone_unserved_mw": 1.0, "worst_scenario_id": ""}


def _env(overall):
    return {"environmental_stress_index": str(overall), "esi_air": "10", "esi_waste": "30", "esi_water": "5", "esi_heat": "24"}


def test_the_joint_table_puts_the_two_sides_next_to_each_other_and_combines_nothing():
    mapping = [_map(str(i), "direct", [f"z{i}"]) for i in range(1, 5)] + [_map("9", "none", [])]
    risk = [_risk("1", 100.0), _risk("2", 100.0), _risk("3", 500.0), _risk("4", 500.0), _risk("9", None)]
    env = {"1": _env(10), "2": _env(30), "3": _env(10), "4": _env(36), "9": _env(10)}
    counts = {k: {"hospitals": 1, "clinics": 0, "specialists": 0} for k in env}
    rows = {r["district_key"]: r for r in ex.joint_decision(mapping, risk, [], counts, env, "2026-09")}
    assert [rows[k]["group"] for k in "1234"] == ["both_lower", "environment_higher", "outage_higher", "both_higher"]
    assert rows["9"]["group"] == "no_outage_data" and rows["9"]["worst_zone_cost_of_delay_egp"] is None
    assert rows["4"]["esi_band"] == "high" and rows["2"]["esi_band"] == "moderate" and rows["1"]["esi_band"] == "low"  # their bands, at 20 and 35
    assert rows["1"]["highest_score"] == "waste" and rows["1"]["env_month"] == "2026-09"
    assert not any("total" in k or "combined" in k or "index_x" in k for k in rows["1"])  # no number made from both sides


def test_the_joint_table_on_the_real_files_has_a_row_per_district(tables):
    scenarios, zones, _ = tables
    mapping = ex.read_mapping()
    counts = ex.read_facility_counts()
    month, env = ex.read_environment()
    rows = ex.joint_decision(mapping, ex.district_risk(mapping, scenarios, zones), ex.hospital_exposure(mapping, scenarios, counts), counts, env, month)
    assert [r["district_key"] for r in rows] == [str(k) for k in range(1001, 1016)] and month == "2026-09"
    assert {r["group"] for r in rows} <= {"both_higher", "outage_higher", "environment_higher", "both_lower", "no_outage_data"}
    assert next(r for r in rows if r["district_key"] == "1015")["group"] == "no_outage_data"
    assert next(r for r in rows if r["district_key"] == "1013")["hospitals"] == 8


def test_their_engine_figures_are_read_as_extracted_and_the_best_package_is_the_lowest_pressure():
    theirs = ex.read_their_packages()
    assert set(theirs) == {str(k) for k in range(1001, 1016)}
    t = theirs["1001"]
    assert t["pressure_baseline"] == pytest.approx(1.2271, abs=1e-4) and t["center_prevention_pressure"] == pytest.approx(0.9766, abs=1e-4)  # what their dashboard shows for R1
    assert t["center_prevention_cost_m_egp"] == 40.5 and t["capacity_cost_m_egp"] == 9.0 and t["access_cost_m_egp"] == 16.0 and t["prevention_cost_m_egp"] == 7.5
    side = ex.their_side(t)
    assert side["their_over_capacity"] is True and side["their_best_package"] == "A New center + prevention" and side["their_best_package_pressure"] == t["center_prevention_pressure"]
    assert ex.their_side(None)["their_best_package"] is None and ex.their_side({})["their_pressure_baseline"] is None


def test_the_best_package_on_a_tie_is_the_cheaper_one():
    t = {"pressure_baseline": 0.8, "pressure_growth_only": 0.9, "center_prevention_pressure": 0.7, "center_prevention_cost_m_egp": 40.5, "capacity_pressure": 0.7,
         "capacity_cost_m_egp": 9.0, "access_pressure": 0.85, "access_cost_m_egp": 16.0, "prevention_pressure": 0.88, "prevention_cost_m_egp": 7.5}
    assert ex.their_side(t)["their_best_package"] == "B Expand existing capacity" and ex.their_side(t)["their_over_capacity"] is False


def test_the_joint_table_carries_their_pressure_when_the_extract_is_there_and_leaves_it_empty_otherwise():
    mapping = [_map("1001", "direct", ["z1"])]
    base = dict(risk=[_risk("1001", 100.0)], exposure=[], counts={"1001": {"hospitals": 1, "clinics": 0, "specialists": 0}}, env={"1001": _env(20)}, env_month="2026-09")
    with_t = ex.joint_decision(mapping, **base, theirs=ex.read_their_packages())[0]
    without = ex.joint_decision(mapping, **base)[0]
    assert with_t["their_pressure_baseline"] == pytest.approx(1.2271, abs=1e-4) and with_t["their_best_package"].startswith("A ")
    assert without["their_pressure_baseline"] is None and without["their_best_package"] is None


def test_the_districts_file_for_the_other_team_changes_only_the_coordinates():
    import csv as _csv
    from app.sim.topology import build as _build

    zones = {z.id: z.center for z in _build().topology.zones}
    new = ex.districts_on_twin_map(zones)
    with (ex.HEALTH_DATA / "districts.csv").open(encoding="utf-8-sig", newline="") as f:
        old = list(_csv.DictReader(f))
    assert [list(r) for r in new] == [list(r) for r in old] and len(new) == 15  # same columns, same order
    for a, b in zip(old, new):
        assert {k: v for k, v in a.items() if k not in ("centroid_lon", "centroid_lat")} == {k: v for k, v in b.items() if k not in ("centroid_lon", "centroid_lat")}
    park = next(r for r in new if r["code"] == "NAC-PARK")
    assert (park["centroid_lon"], park["centroid_lat"]) == ("31.718", "30.01")  # no counterpart: unchanged
    moved = [(a, b) for a, b in zip(old, new) if a["code"] != "NAC-PARK"]
    assert all((a["centroid_lon"], a["centroid_lat"]) != (b["centroid_lon"], b["centroid_lat"]) for a, b in moved)
    assert len({(b["centroid_lon"], b["centroid_lat"]) for _, b in moved}) == 14  # no two districts on the same point
    assert next(r for r in new if r["code"] == "NAC-GOV")["centroid_lat"] != "30.01"


def test_a_file_can_be_written_with_the_line_endings_of_the_original(tmp_path):
    ex.write_csv(tmp_path / "a.csv", [{"x": "1", "y": "2"}], "\n")
    ex.write_csv(tmp_path / "b.csv", [{"x": "1", "y": "2"}])
    assert (tmp_path / "a.csv").read_bytes() == b"x,y\n1,2\n" and (tmp_path / "b.csv").read_bytes() == b"x,y\r\n1,2\r\n"
