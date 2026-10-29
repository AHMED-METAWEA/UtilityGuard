"""Independent arithmetic, ownership and provenance checks for the published model."""

import json
import re
from pathlib import Path

import numpy as np
import pytest

from app.cost import DEFAULTS, POLICIES, Assumptions, IncidentFacts, _summary, build_terms, compute

DOC = Path(__file__).resolve().parents[2] / "docs" / "cost-model.md"
EXAMPLES = json.loads(re.search(r"```json cost-examples\n(.*?)\n```", DOC.read_text(), re.S).group(1))


@pytest.fixture
def A(tmp_path):
    return Assumptions(tmp_path / "a.json")


@pytest.mark.parametrize("ex", EXAMPLES["incidents"], ids=lambda ex: ex["kind"])
def test_documented_incident_and_single_cost_owner(A, ex):
    f = IncidentFacts(kind=ex["kind"], zone_types=ex["zones"], **ex["facts"])
    rec = compute(f, A, "documented-" + ex["kind"])
    c = rec.cost
    assert c.delay_per_hour == ex["hourly"]
    assert c.act_now == ex["act"]
    assert c.cost_of_delay == ex["delay"]
    assert c.expected_saving == ex["saving"]
    assert not c.review
    assert sum(b.amount for b in c.breakdown) == c.cost_of_delay
    assert len({b.group for b in c.breakdown}) == len(c.breakdown)
    assert len({b.cause for b in c.breakdown}) == len(c.breakdown)
    assert [b.group for b in c.breakdown].count("repair") == 1
    assert len({t.group for t in build_terms(f, A)}) == len(build_terms(f, A))
    for calc in rec.explanation.calculations:
        assert calc.name and calc.formula
        assert all(i.basis for i in calc.inputs)
    assert all(a == A.item(a.key) for a in rec.explanation.assumptions)
    line = next(line for line in DOC.read_text().splitlines() if line.startswith(f"| {ex['kind']} |"))
    assert all(str(ex[k]) in line for k in ("hourly", "act", "delay", "saving"))


def test_fractional_costs_still_reconcile_exactly(A):
    f = IncidentFacts(kind="leak", zone_types={"zone-gov": "government", "zone-cbd": "business"},
                      leak_m3h=123.456789, secondary_mw=0.123456789)
    A = A.with_override("water_price", 7.123456).with_override("late_multiplier_pipe_burst", 1.712345)
    c = compute(f, A, "fractions").cost
    assert sum(b.amount for b in c.breakdown) == c.cost_of_delay
    assert c.expected_saving == round(c.cost_of_delay - c.act_now, 2)


def test_voll_allocates_unserved_energy_once_and_tariff_only_buys_extra_energy(A):
    f = IncidentFacts(kind="substation_trip", zone_types={"zone-gov": "government", "zone-cult": "services"},
                      unserved_mw=20, secondary_mw=0.25, cooling_deficit_tons=1000)
    terms = build_terms(f, A)
    unserved = [t for t in terms if t.group.startswith("loss:unserved:")]
    assert [t.value for t in unserved] == [20000 * 500 / 800 * 45, 20000 * 300 / 800 * 75]
    assert not any(t.group == "loss:cooling" for t in terms)
    before = _summary(f, A)["saving"]
    after = _summary(f, A.with_override("elec_tariff", 2.6))["saving"]
    assert after - before == 250 * (4 - 1)  # only the additional 250 kWh/h purchase
    for t in terms:
        if t.category == "disruption":
            assert {i.name for i in t.inputs} == {"customers_affected", "customer_hour_rate", "outage_hours"}
    rec = compute(f, A, "trip")
    assert {"voll_government", "voll_services", "customers_zone_gov", "customers_zone_cult"} <= {a.key for a in rec.explanation.assumptions}


def test_tornado_recomputes_real_endpoint_costs_and_orders_swings(A):
    f = IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=140, secondary_mw=0.125)
    rec = compute(f, A, "tornado")
    rows = {t.input: t for t in rec.explanation.tornado}
    assert (rows["water_price"].low, rows["water_price"].high) == (6, 10)
    assert (rows["water_price"].saving_at_low, rows["water_price"].saving_at_high) == (30580, 33380)
    assert (rows["late_multiplier_pipe_burst"].saving_at_low, rows["late_multiplier_pipe_burst"].saving_at_high) == (28300, 35660)
    assert (rows["customers_zone_gov"].saving_at_low, rows["customers_zone_gov"].saving_at_high) == (28855, 35105)
    assert not {"discount_rate", "voll_government", "parts_chiller", "saving_max_pipe_burst"} & rows.keys()
    swings = [abs(t.saving_at_high - t.saving_at_low) for t in rec.explanation.tornado]
    assert swings == sorted(swings, reverse=True)
    for t in rows.values():
        assert t.saving_at_low == round(_summary(f, A.with_override(t.input, t.low))["saving"], 2)
        assert t.saving_at_high == round(_summary(f, A.with_override(t.input, t.high))["saving"], 2)


@pytest.mark.parametrize("ex", EXAMPLES["incidents"], ids=lambda ex: ex["kind"])
@pytest.mark.parametrize("bound", ["min", "max"])
def test_sanity_bounds_flag_without_changing_saving(A, ex, bound):
    f = IncidentFacts(kind=ex["kind"], zone_types=ex["zones"], **ex["facts"])
    baseline = compute(f, A, "bounds")
    threshold = ex["saving"] + (1 if bound == "min" else -1)
    altered = compute(f, A.with_override(f"saving_{bound}_{ex['kind']}", threshold), "bounds")
    assert altered.cost.review
    assert ex["kind"] in altered.cost.review_reason
    assert altered.cost.expected_saving == baseline.cost.expected_saving
    assert altered.cost.breakdown == baseline.cost.breakdown
    assert altered.cost.saving_range == baseline.cost.saving_range
    boundary = compute(f, A.with_override(f"saving_{bound}_{ex['kind']}", ex["saving"]), "bounds")
    assert not boundary.cost.review


def test_extreme_incident_keeps_number_and_requests_review(A):
    f = IncidentFacts(kind="pipe_burst", zone_types={"zone-gov": "government"}, leak_m3h=1e9)
    c = compute(f, A, "extreme").cost
    assert c.review and c.expected_saving > A["saving_max_pipe_burst"]
    assert c.expected_saving == round(_summary(f, A)["saving"], 2)


def test_registry_table_metadata_and_override_attribution(A):
    doc = DOC.read_text()
    for key, d in DEFAULTS.items():
        item = A.item(key)
        assert item.low <= item.value <= item.high
        assert item.reviewed == "2026-10-29"
        assert item.status in {"planning", "benchmark", "engineering"}
        assert item.basis and item.source == "default assumption"
        row = next(line for line in doc.splitlines() if line.startswith(f"| {key} |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert float(cells[1]) == d["value"]
        assert cells[2:4] == [d["unit"], d["status"]]
        assert (float(cells[4]), float(cells[5])) == (d["low"], d["high"])
        assert cells[6] == d["basis"]
    A.update({"water_price": 12}, source="finance proposal")
    item = Assumptions(A.path).item("water_price")
    assert item.user_changed and item.source.startswith("finance proposal ")
    assert item.status == "planning" and item.basis == DEFAULTS["water_price"]["basis"]
    assert (item.low, item.high) == (6, 10)
    assert "userChanged" in item.dump()
    assert not {"delay_hours", "late_multiplier", "penalty_government"} & DEFAULTS.keys()
    assert set(POLICIES) == {e["kind"] for e in EXAMPLES["incidents"]}


def test_sampling_uses_absolute_registered_range_not_twenty_percent(A):
    class Endpoints:
        def __init__(self, upper):
            self.upper = upper

        def uniform(self, low, high):
            return high if self.upper else low

    overridden = A.with_override("water_price", 100)
    for upper in (False, True):
        sample = overridden.sampled(Endpoints(upper), keys=["water_price", "pm_n1"])
        assert sample["water_price"] == (10 if upper else 6)
        assert sample["pm_n1"] == (1.5 if upper else 0.5)
        assert sample["crew_callout"] == A["crew_callout"]
    draw = A.sampled(np.random.default_rng(42))
    assert all(d["low"] <= draw[k] <= d["high"] for k, d in DEFAULTS.items())
