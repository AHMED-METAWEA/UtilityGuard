"""Cost formulas from spec 9A, checked by hand-computed values."""

import pytest

from app.cost import Assumptions, IncidentFacts, compute


@pytest.fixture
def A(tmp_path):
    return Assumptions(tmp_path / "assumptions.json")


def leak():
    return IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=140, secondary_mw=0.125)


def test_water_leak_matches_spec_formula(A):
    rec = compute(leak(), A, "inc-1")
    c = rec.cost
    # C_water/h = Q_leak × P_water = 140 × 8
    loss = 140 * 8
    secondary = 125 * 1.6  # 0.125 MW = 125 kWh/h × 1.6 EGP/kWh
    c_delay = loss + 2500 + secondary
    repair_now = 3400 + 15000
    repair_late = repair_now * 1.7
    assert c.delay_per_hour == round(c_delay)
    assert c.act_now == repair_now
    assert c.cost_of_delay == round(c_delay * 6 + repair_late)
    assert c.expected_saving == round(c_delay * 6 + repair_late - repair_now)
    names = {x.name for x in rec.explanation.calculations}
    assert {"Water loss", "Cost of delay per hour", "Saving from acting now"} <= names
    keys = {a.key for a in rec.explanation.assumptions}
    assert {"water_price", "penalty_government", "crew_callout", "parts_pipe", "delay_hours"} <= keys


def test_saving_is_difference_of_both_sides(A):
    c = compute(leak(), A, "inc-1").cost
    assert c.expected_saving == pytest.approx(c.cost_of_delay - c.act_now, abs=1)


def test_sensitivity_plus_minus_20(A):
    rec = compute(leak(), A, "inc-1")
    s = {(x.input, x.change_percent): x.new_saving for x in rec.explanation.sensitivity}
    base = rec.cost.expected_saving
    water_part = 140 * 8 * 6
    assert s[("water_price", -20)] == pytest.approx(base - 0.2 * water_part, abs=1)
    assert s[("water_price", 20)] == pytest.approx(base + 0.2 * water_part, abs=1)
    assert s[("delay_hours", 20)] > base > s[("delay_hours", -20)]


def test_changing_assumption_recomputes_and_is_flagged(A):
    before = compute(leak(), A, "inc-1").cost.expected_saving
    A.update({"water_price": 12})
    rec = compute(leak(), A, "inc-1")
    assert rec.cost.expected_saving == pytest.approx(before + 140 * 4 * 6, abs=1)
    item = next(a for a in rec.explanation.assumptions if a.key == "water_price")
    assert item.user_changed and item.value == 12
    assert Assumptions(A.path)["water_price"] == 12  # persisted
    with pytest.raises(KeyError):
        A.update({"nope": 1})


def test_energy_formula(A):
    f = IncidentFacts(kind="energy_waste", zone_types={"zone-cbd": "business"}, excess_mw=3.0)
    rec = compute(f, A, "inc-2")
    calc = next(x for x in rec.explanation.calculations if x.name == "Energy waste")
    assert calc.result == 3000 * 1.6  # (E_actual − E_expected) × P_kWh


@pytest.mark.parametrize("kind,extra", [
    ("substation_trip", dict(unserved_mw=20, pumps_stopped=2)),
    ("pump_failure", dict(leak_m3h=0)),
    ("cooling_fault", dict(cooling_deficit_tons=1200)),
    ("sensor_fault", {}),
])
def test_every_kind_has_positive_saving_and_full_record(A, kind, extra):
    f = IncidentFacts(kind=kind, zone_types={"zone-gov": "government"}, **extra)
    rec = compute(f, A, "inc-3")
    assert rec.cost.expected_saving > 0
    assert rec.explanation.calculations and rec.explanation.assumptions and rec.explanation.sensitivity
    assert rec.cost.explanation is rec.explanation
