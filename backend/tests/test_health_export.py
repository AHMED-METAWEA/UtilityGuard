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
