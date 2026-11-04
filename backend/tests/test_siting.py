"""The siting what-if: the extra load enters the twin's physics exactly as stated, an empty answer changes nothing, and the probabilities behave."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.siting import SitingRequest
from app.siting import KINDS, _days, evaluate, options
from app.sim.engine import BG_LOSS, CAIRO, CHILLER_BASE, Engine, Event


def targets(extra, zone_events=(), hour=12):
    e = Engine(42)
    e.extra_load = extra
    e.events = list(zone_events)
    e.tick = 0
    t = e._targets(datetime(2026, 7, 8, hour, tzinfo=CAIRO))
    return e, t


def test_a_zero_load_changes_nothing():
    a, ta = targets({})
    b, tb = targets({"zone-gov": (0.0, 0.0, 0.0)})
    assert ta == tb and a.zone_demand == b.zone_demand


def test_water_and_electricity_enter_the_zone_demand_as_stated():
    a, _ = targets({})
    b, _ = targets({"zone-cbd": (10.0, 3.0, 0.0)})
    assert b.zone_demand["zone-cbd"][0] - a.zone_demand["zone-cbd"][0] == pytest.approx(10.0 * (1 + BG_LOSS))
    temp_f = 1 + 0.012 * (b.ambient - 30)
    assert b.zone_demand["zone-cbd"][1] - a.zone_demand["zone-cbd"][1] == pytest.approx(3.0 * (0.8 + 0.2 * temp_f))
    assert b.zone_demand["zone-gov"] == a.zone_demand["zone-gov"]  # no other zone moves


def test_cooling_goes_to_the_plant_where_there_is_district_cooling_and_to_electricity_where_there_is_not():
    a, ta = targets({})
    b, tb = targets({"zone-gov": (0.0, 0.0, 5.0)})
    temp_f = 1 + 0.012 * (b.ambient - 30)
    assert b.zone_demand["zone-gov"][2] - a.zone_demand["zone-gov"][2] == pytest.approx(5.0 * temp_f ** 3)
    assert tb["cool-plant-01-load"] > ta["cool-plant-01-load"]
    c, tc = targets({"zone-res-n": (0.0, 0.0, 5.0)})
    assert c.zone_demand["zone-res-n"][2] == a.zone_demand["zone-res-n"][2] == 0.0  # no district cooling there
    assert c.zone_demand["zone-res-n"][1] - a.zone_demand["zone-res-n"][1] == pytest.approx(5.0 * CHILLER_BASE * (1 + 0.018 * (c.ambient - 30)))
    assert tc["cool-plant-02-load"] == ta["cool-plant-02-load"] and tc["cool-plant-01-load"] == ta["cool-plant-01-load"]


def test_the_facility_load_reaches_the_substation_and_the_backup_when_the_substation_trips():
    _, a = targets({})
    _, b = targets({"zone-gov": (0.0, 4.0, 0.0)})
    assert b["elec-sub-02-load"] - a["elec-sub-02-load"] > 3.0
    trip = [Event("substation_trip", "elec-sub-02", 1.0)]
    _, c = targets({}, trip)
    _, d = targets({"zone-gov": (0.0, 4.0, 0.0)}, trip)
    assert d["elec-sub-03-load"] > c["elec-sub-03-load"]  # sub-03 is the backup of sub-02 and carries the facility's priority share


def test_the_days_are_sunday_to_thursday_of_the_summer():
    days = _days()
    assert days[0].month == 6 and days[-1].month == 9 and all(d.weekday() not in (4, 5) for d in days) and 80 < len(days) < 100


def test_a_small_facility_in_a_roomy_zone_changes_no_probability_and_a_huge_one_does():
    small = evaluate(SitingRequest(zone_id="zone-res-n", kind="hospital", units=50, runs=40))
    assert all(r.probability_after == r.probability_before for c in small.conditions for r in c.risks)
    huge = evaluate(SitingRequest(zone_id="zone-res-2", kind="hospital", units=4000, runs=40))
    ordinary = next(c for c in huge.conditions if c.condition == "normal")
    electricity = next(r for r in ordinary.risks if r.network == "electricity")
    assert electricity.probability_after > electricity.probability_before and electricity.asset == "elec-sub-01"
    assert huge.cost.mitigations and huge.cost.total_m_egp == pytest.approx(huge.cost.capex_m_egp + huge.cost.mitigation_m_egp)


def test_probabilities_are_shares_and_a_bigger_facility_never_lowers_them():
    a = evaluate(SitingRequest(zone_id="zone-gov", kind="hospital", units=100, runs=40))
    b = evaluate(SitingRequest(zone_id="zone-gov", kind="hospital", units=600, runs=40))
    for ca, cb in zip(a.conditions, b.conditions):
        for ra, rb in zip(ca.risks, cb.risks):
            assert 0 <= ra.probability_before <= ra.probability_after <= rb.probability_after <= 1
            assert ra.probability_before == rb.probability_before  # the same days without the facility


def test_the_same_request_gives_the_same_answer():
    req = SitingRequest(zone_id="zone-cbd", kind="hospital_expansion", units=150, runs=30)
    assert evaluate(req).dump() == evaluate(req).dump()


def test_a_zone_without_district_cooling_has_no_cooling_row_and_its_chillers_are_electricity():
    r = evaluate(SitingRequest(zone_id="zone-res-ne", kind="hospital", units=300, runs=30))
    assert all(x.network != "cooling" for c in r.conditions for x in c.risks)
    assert r.load.cooling_mw == 0 and r.load.on_site_chillers_mw == pytest.approx(300 * KINDS["hospital"].cooling.value * CHILLER_BASE)


def test_a_custom_load_needs_all_three_numbers_and_an_unknown_zone_is_refused():
    with pytest.raises(ValueError):
        evaluate(SitingRequest(zone_id="zone-gov", kind="custom", units=1, water_m3h=5))
    with pytest.raises(KeyError):
        evaluate(SitingRequest(zone_id="zone-nowhere", kind="hospital", units=10))


def test_the_cost_adds_the_facility_and_the_upgrades_and_a_clinic_has_no_price_of_its_own():
    r = evaluate(SitingRequest(zone_id="zone-res-n", kind="clinic", units=2, runs=20))
    assert r.cost.capex_m_egp is None and r.cost.total_m_egp is None
    r = evaluate(SitingRequest(zone_id="zone-res-n", kind="clinic", units=2, runs=20, capex_m_egp=3.0))
    assert r.cost.total_m_egp == pytest.approx(3.0 + r.cost.mitigation_m_egp)


def test_the_answers_name_their_assumptions_and_say_they_are_simulated():
    r = evaluate(SitingRequest(zone_id="zone-gov", kind="hospital", units=300, runs=20))
    assert r.simulated and len(r.assumptions) == 3 and all(a.low <= a.value <= a.high and a.basis for a in r.assumptions)
    assert r.verdict and {c.condition for c in r.conditions} == {"normal", "heat", "substation_loss"}


def test_the_api_serves_the_options_and_an_evaluation():
    c = TestClient(app)
    o = c.get("/api/v1/siting/options").json()
    assert {z["id"] for z in o["zones"]} >= {"zone-gov", "zone-res-n"} and [k["id"] for k in o["kinds"]] == list(KINDS)
    assert o == options() or o["kinds"][0]["assumptions"][0]["name"]
    body = {"zoneId": "zone-gov", "kind": "hospital", "units": 200, "runs": 30}
    r = c.post("/api/v1/siting/evaluate", json=body)
    assert r.status_code == 200 and r.json()["zone"]["id"] == "zone-gov" and r.json()["conditions"][0]["risks"]
    assert c.post("/api/v1/siting/evaluate", json={**body, "zoneId": "nope"}).status_code == 404
    assert c.post("/api/v1/siting/evaluate", json={**body, "kind": "custom"}).status_code == 422
    assert c.post("/api/v1/siting/evaluate", json={**body, "units": -1}).status_code == 422
    custom = c.post("/api/v1/siting/evaluate", json={"zoneId": "zone-gov", "kind": "custom", "units": 2, "waterM3H": 5, "electricityMw": 1.5, "coolingMw": 0, "runs": 20})
    assert custom.status_code == 200 and custom.json()["load"] == {"waterM3H": 10.0, "electricityMw": 3.0, "coolingMw": 0.0, "onSiteChillersMw": 0.0}  # the keys the web app reads
