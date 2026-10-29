"""Documented advisor examples exercise real history aggregation and opportunity functions."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.cost import TONS_PER_MW, Assumptions
from app.schemas import AssetStatus, SavingRange
from app.savings import _opportunity, _specs, build_savings, financials
from app.sim.engine import CAIRO
from app.sim.topology import build
from tests.test_cost_provenance import DOC, EXAMPLES


@pytest.fixture
def A(tmp_path):
    return Assumptions(tmp_path / "a.json")


def baseline(asset_id=None):
    g = build()
    row = {}
    for z in g.topology.zones:
        row[f"demand:cooling:{z.id}"] = 10 / TONS_PER_MW
        row[f"demand:water:{z.id}"] = 1115 / len(g.topology.zones)
        row[f"{g.zone_pump[z.id]}-pressure"] = 4.25
    row.update({"kpi:cooling_elec_mw": 2, "kpi:ambient": 30,
                "kpi:plant_elec:cool-plant-01": 0.09, "kpi:plant_elec:cool-plant-02": 0.07,
                "kpi:plant_delivered:cool-plant-01": 100 / TONS_PER_MW,
                "kpi:plant_delivered:cool-plant-02": 100 / TONS_PER_MW})
    t0 = datetime(2026, 10, 25, tzinfo=CAIRO)
    rows = [(t0 + timedelta(hours=h), dict(row)) for h in range(24)]
    status = {asset_id: AssetStatus(asset_id=asset_id, status="normal", health_score=90, failure_probability=0.8)} if asset_id else {}
    rt = SimpleNamespace(engine=SimpleNamespace(g=g, zone_demand={z.id: (0, 10, 0) for z in g.topology.zones},
                         metrics={"plant_delivered:cool-plant-01": 10}), asset_status=status, now=t0, seed=42)
    return rt, rows


@pytest.mark.parametrize("ex", EXAMPLES["opportunities"], ids=lambda ex: ex["id"])
def test_documented_opportunity_payback_and_npv(A, ex):
    asset_id = ex["id"].removeprefix("opp-pm-") if ex["id"].startswith("opp-pm-") else None
    rt, rows = baseline(asset_id)
    s = next(s for s in _specs(rt, rows, A) if s.id == ex["id"])
    assert s.fn(A) == pytest.approx(ex["monthly"], abs=1e-6)
    o = _opportunity(s, A, SavingRange(p10=0, p50=0, p90=0, runs=0))
    assert o.monthly_saving == ex["monthly"]
    assert o.investment == ex["investment"]
    assert o.payback_months == ex["payback"]
    assert o.npv3_years == ex["npv"]
    assert o.payback_days == round(ex["investment"] / ex["monthly"] * 30, 1)
    assert all(i.basis for c in o.calculations for i in c.inputs)
    assert all(a.basis and a.status for a in o.assumptions)
    assert "discount_rate" in {a.key for a in o.assumptions}
    assert {"Monthly saving", "Upfront investment", "Simple payback", "Three-year NPV"} <= {c.name for c in o.calculations}
    text = DOC.read_text().split("## Example verification data")[0]
    assert str(ex["monthly"]) in text and str(ex["npv"]) in text
    dumped = o.dump()
    assert {"paybackMonths", "npv3Years", "calculations"} <= dumped.keys()
    if s.cost_facts:
        # Every nested cost assumption is present, including parts, VoLL and customers.
        nested_keys = {i.key for c in o.calculations for i in c.inputs if i.key}
        assert nested_keys <= {a.key for a in o.assumptions}
        for changed in ("crew_callout", "planned_factor"):
            assert s.fn(A.with_override(changed, A[changed] * 1.1)) != s.fn(A)


def test_financials_zero_rate_no_saving_and_discount_change(A):
    assert financials(1000, 12000, 0) == (12, 24000)
    assert financials(0, 12000, 0.18) == (None, -12000)
    rt, rows = baseline()
    s = next(s for s in _specs(rt, rows, A) if s.id == "opp-setback")
    zero = SavingRange(p10=0, p50=0, p90=0, runs=0)
    original = _opportunity(s, A, zero)
    discounted = _opportunity(s, A.with_override("discount_rate", 0.24), zero)
    assert original.monthly_saving == discounted.monthly_saving
    assert original.payback_months == discounted.payback_months
    assert discounted.npv3_years < original.npv3_years
    nothing = _opportunity(s, A.with_override("setback_reduction", 0), zero)
    assert nothing.payback_days is None and nothing.payback_months is None


def test_joint_savings_ranges_are_seeded_and_report_totals_reconcile(A):
    rt, rows = baseline("elec-sub-02")
    hist = SimpleNamespace(hourly=lambda *args: rows)
    a, b = build_savings(rt, hist, A), build_savings(rt, hist, A)
    assert a.dump() == b.dump()
    assert a.total_monthly == sum(o.monthly_saving for o in a.opportunities)
    assert a.total_investment == sum(o.investment for o in a.opportunities)
    assert a.total_range.p10 <= a.total_range.p50 <= a.total_range.p90
    assert all(o.saving_range.p10 <= o.saving_range.p50 <= o.saving_range.p90 for o in a.opportunities)
