"""Decision comparisons checked independently against incident arithmetic."""

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.cost import DEFAULTS, POLICIES, Assumptions, IncidentFacts, compute
from app.decision import (
    _totals, break_even, build_decision, cheapest, cost_lines, from_finding,
    multiplier, option_ids, paired_statistics, wait_cost,
)
from app.schemas import AgentFinding, DecisionReport, ErrorResponse, Incident

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "rest"


@pytest.fixture
def A(tmp_path):
    return Assumptions(tmp_path / "assumptions.json")


def burst():
    return IncidentFacts(kind="pipe_burst", zone_types={"zone-gov": "government"},
                         leak_m3h=140, secondary_mw=0.125)


def finding_for(f, A):
    finding = AgentFinding.model_validate(json.loads((FIX.parent / "ws" / "agent_finding.json").read_text())["payload"])
    record = compute(f, A, finding.incident_id)
    return finding.model_copy(update={"cost": record.cost, "explain": record.explanation})


def test_pipe_burst_hand_calculated_totals_and_owned_lines(A):
    assert [A[k] for k in ("water_price", "elec_tariff", "crew_callout", "parts_pipe",
                           "response_hours", "delay_hours_pipe_burst", "late_multiplier_pipe_burst",
                           "planned_factor", "isolate_loss_reduction", "late_multiplier_cap")] == [8, 1.6, 3400, 15000, 1, 6, 1.7, 0.6, 0.85, 3]
    # 1120 water + 2500 administration + 200 secondary = 3820 EGP/h; repair = 18400.
    expected = {
        "fix_now": 18400 + 3820 * 1,
        "isolate_fix": 3820 * 1 + (1120 * 0.15 + 2500 + 200) * 5 + 18400 * 0.6 + 3400,
        "wait_6h": 3820 * 6 + 18400 * 1.7,
        "wait_24h": 3820 * 24 + 18400 * 3,
        "wait_72h": 3820 * 72 + 18400 * 3,
    }
    assert expected == dict(fix_now=22220, isolate_fix=32600, wait_6h=54200, wait_24h=146880, wait_72h=330240)
    report = build_decision(burst(), A, "hand-example")
    assert dict(zip(option_ids(burst()), _totals(burst(), A))) == expected
    assert {o.id: o.current_cost for o in report.options} == expected
    isolate = next(o for o in report.options if o.id == "isolate_fix")
    assert {c.name: c.result for c in isolate.cost_lines} == {
        "Water loss": 1960, "Service disruption, zone-gov": 15000,
        "Knock-on overdraw in another network": 1200, "Repair now": 11040,
        "Isolation crew call-out": 3400,
    }
    assert next(o for o in report.options if o.id == "fix_now").current_cost == compute(burst(), A, "hand-example").cost.act_now
    for option in report.options:
        assert sum(c.result for c in option.cost_lines) == option.current_cost
        assert all(c.name and c.formula and c.unit and all(i.unit and i.basis for i in c.inputs) for c in option.cost_lines)
        assert option.p10 <= option.p50 <= option.p90
        assert option.expected_regret >= 0
        assert option.win_share_text == f"cheapest in {option.win_share:.1%} of sampled price scenarios"
    assumptions = {a.key: a for a in report.assumptions}
    for key in ("isolate_loss_reduction", "late_multiplier_cap", "planned_factor"):
        assert assumptions[key] == A.item(key)
        assert assumptions[key].status == "planning"


@pytest.mark.parametrize("kind", POLICIES)
def test_wait_at_own_horizon_equals_existing_cost_of_delay(A, kind):
    f = IncidentFacts(kind=kind, zone_types={"zone-gov": "government"},
                      **{"pipe_burst": dict(leak_m3h=140, secondary_mw=0.125),
                         "substation_trip": dict(unserved_mw=20),
                         "cooling_fault": dict(cooling_deficit_tons=1200),
                         "energy_waste": dict(excess_mw=3)}.get(kind, {}))
    hours = A[f"delay_hours_{kind}"]
    cost = compute(f, A, "horizon-" + kind).cost
    assert multiplier(A, kind, hours) == A[f"late_multiplier_{kind}"]
    assert wait_cost(f, A, hours) == cost.cost_of_delay
    assert sum(c.result for c in cost_lines(f, A, f"wait_{hours:g}h")) == cost.cost_of_delay
    assert ("isolate_fix" in option_ids(f)) == (kind == "pipe_burst")


@pytest.mark.parametrize("kind", POLICIES)
def test_fractional_horizon_cost_matches_displayed_cost_exactly(A, kind):
    f = IncidentFacts(kind=kind, zone_types={"zone-gov": "government"},
                      leak_m3h=123.456789, secondary_mw=0.123456789)
    A = A.with_override("water_price", 7.123456).with_override("crew_callout", 3400.123456)
    cost = compute(f, A, "fractional-" + kind).cost
    assert wait_cost(f, A, A[f"delay_hours_{kind}"]) == cost.cost_of_delay


def test_paired_regret_uses_each_sample_winner():
    samples = np.array([[0, 8, 12], [12, 8, 12]], dtype=float)
    means, regrets, wins = paired_statistics(samples)
    assert means.tolist() == [6, 8, 12]
    assert regrets.tolist() == [2, 4, 8]
    assert wins.tolist() == [0.5, 0.5, 0]
    assert np.all(regrets >= 0)
    assert means.argmin() == regrets.argmin()
    assert regrets[means.argmin()] > 0  # subtracting min(option means) would incorrectly give zero
    assert np.allclose(regrets - regrets.min(), means - means.min())


def test_statistics_match_shared_registered_draws(A):
    report = build_decision(burst(), A, "paired", seed=17)
    rng = np.random.default_rng(report.seed)
    keys = [a.key for a in report.assumptions if not a.key.startswith("saving_")]
    samples = []
    for _ in range(800):
        s = A.sampled(rng, keys=keys)
        water = 140 * s["water_price"]
        other = s["customers_zone_gov"] * s["customer_hour_government"] + 125 * s["elec_tariff"]
        repair = s["crew_callout"] + s["parts_pipe"]
        response, horizon = s["response_hours"], s["delay_hours_pipe_burst"]
        costs = [repair + (water + other) * response,
                 (water + other) * response + (water * (1 - s["isolate_loss_reduction"]) + other) * (horizon - response)
                 + repair * s["planned_factor"] + s["crew_callout"]]
        costs += [(water + other) * w + repair * min(s["late_multiplier_cap"], 1 + (s["late_multiplier_pipe_burst"] - 1) * w / horizon)
                  for w in (6, 24, 72)]
        samples.append(costs)
    samples = np.asarray(samples)
    means, regrets, wins = paired_statistics(samples)
    assert report.runs == 800
    assert report.recommended_option_id == report.options[int(means.argmin())].id
    for i, option in enumerate(report.options):
        assert option.expected_cost == round(float(means[i]), 2)
        assert option.expected_regret == round(float(regrets[i]), 2)
        assert option.win_share == wins[i]
        assert [option.p10, option.p50, option.p90] == [round(float(v), 2) for v in np.percentile(samples[:, i], [10, 50, 90])]
        assert option.p90 < samples[:, i].max()
        assert option.net_benefit_vs_fix_now == round(float(means[i] - means[0]), 2)


def test_negative_wait_net_benefit_is_retained(A, monkeypatch):
    monkeypatch.setitem(DEFAULTS, "response_hours", {**DEFAULTS["response_hours"], "low": 8, "high": 8})
    A = A.with_override("response_hours", 8)
    f = IncidentFacts(kind="energy_waste", zone_types={}, excess_mw=0.001)
    report = build_decision(f, A, "cheap-fault")
    wait = next(o for o in report.options if o.id == "wait_6h")
    assert wait.current_cost == 3400 + 1.6 * 6
    assert wait.net_benefit_vs_fix_now < 0
    assert wait.expected_cost < report.options[0].expected_cost
    assert report.recommended_option_id == "wait_6h"


def test_break_even_leak_rate_and_degenerate_crossings(A):
    for row, w in zip(break_even(burst(), A), (6, 24, 72)):
        m = min(3, 1 + 0.7 * w / 6)
        assert row.parameter == "leak_m3h" and row.unit == "m3/h"
        assert row.value == pytest.approx((18400 * (1 - m) / (w - 1) - 2700) / 8)
        rate = row.value * 8 + 2700
        assert rate * w + 18400 * m == pytest.approx(18400 + rate)
        assert row.value < 0 and row.reason
        assert row.formula and all(i.basis and i.unit for i in row.inputs)
    row = break_even(burst(), A.with_override("response_hours", 6))[0]
    assert row.value is None and row.reason == "No finite break-even rate"
    f = IncidentFacts(kind="energy_waste", zone_types={}, excess_mw=1)
    row = break_even(f, A.with_override("response_hours", 6))[0]
    assert row.value is None and row.reason == "Every rate ties"


def test_decision_map_current_cell_and_all_grid_cells(A):
    report = build_decision(burst(), A, "map")
    grid = report.decision_map
    assert len(grid.x_values) == len(grid.y_values) == len(grid.cells) == 9
    assert grid.x_key != grid.y_key
    assert grid.x_values[0] == A.item(grid.x_key).low and grid.x_values[-1] == A.item(grid.x_key).high
    assert grid.y_values[0] == A.item(grid.y_key).low and grid.y_values[-1] == A.item(grid.y_key).high
    assert grid.cells[grid.y_values.index(grid.current_y)][grid.x_values.index(grid.current_x)] == grid.current_option_id
    assert grid.current_option_id == report.recommended_option_id
    for y, row in zip(grid.y_values, grid.cells):
        assert len(row) == 9
        for x, cell in zip(grid.x_values, row):
            varied = A.with_override(grid.x_key, x).with_override(grid.y_key, y)
            assert cell == cheapest(_totals(burst(), varied), option_ids(burst()))


def test_map_includes_non_midpoint_current_values(A):
    A = A.with_override("customer_hour_government", 4.37).with_override("late_multiplier_pipe_burst", 1.63)
    grid = build_decision(burst(), A, "map-override").decision_map
    assert grid.current_x in grid.x_values and grid.current_y in grid.y_values
    assert grid.cells[grid.y_values.index(grid.current_y)][grid.x_values.index(grid.current_x)] == grid.current_option_id


def test_map_shows_where_isolation_recommendation_flips(A):
    f = IncidentFacts(kind="pipe_burst", zone_types={}, leak_m3h=700)
    report = build_decision(f, A, "flip")
    grid = report.decision_map
    assert {cell for row in grid.cells for cell in row} == {"fix_now", "isolate_fix"}
    assert grid.cells[grid.y_values.index(grid.current_y)][grid.x_values.index(grid.current_x)] == report.recommended_option_id


def test_exact_ties_have_no_recommendation(A):
    f = IncidentFacts(kind="energy_waste", zone_types={})
    report = build_decision(f, A, "tie")
    assert report.recommended_option_id is None
    assert report.decision_map.current_option_id is None
    assert report.dump()["recommendedOptionId"] is None
    assert report.dump()["decisionMap"]["currentOptionId"] is None
    assert all(o.expected_regret == 0 and o.win_share == 1 for o in report.options)


def test_review_preserves_costs(A):
    base = build_decision(burst(), A, "review")
    altered = build_decision(burst(), A.with_override("saving_max_pipe_burst", 1), "review")
    assert not base.review and altered.review
    assert "recommended expected cost" in altered.review_reason
    assert altered.options == base.options


@pytest.mark.parametrize("f", [burst(), IncidentFacts(kind="substation_trip", zone_types={"zone-gov": "government", "zone-cult": "services"}, unserved_mw=12.345678, secondary_mw=0.123456)])
def test_rebuild_uses_finding_snapshot_instead_of_changed_live_values(A, f):
    A = A.with_override("water_price", 7.123456)
    finding = finding_for(f, A)
    changed = A.with_override("water_price", 100).with_override("customers_zone_gov", 100000)
    report = from_finding(finding, changed)
    assert report.options[0].current_cost == finding.cost.act_now
    assert report == build_decision(f, A, finding.incident_id)
    assert changed["water_price"] == 100


def test_seeded_output_is_deterministic(A):
    first = build_decision(burst(), A, "deterministic", seed=7).dump()
    assert first == build_decision(burst(), A, "deterministic", seed=7).dump()
    assert first != build_decision(burst(), A, "deterministic", seed=8).dump()


def test_endpoint_validates_schema_and_missing_incident_or_finding(A, monkeypatch):
    from app import main

    finding = finding_for(burst(), A)
    incident = Incident.model_validate(json.loads((FIX / "incidents.json").read_text())[0])
    incident = incident.model_copy(update={"calculation_id": finding.explain.calculation_id, "finding_ids": [finding.id]})
    monkeypatch.setattr(main.runtime, "incidents", {incident.id: incident})
    monkeypatch.setattr(main.runtime, "findings", {finding.id: finding})
    monkeypatch.setattr(main.service, "A", A)
    client = TestClient(main.app)
    path = f"/api/v1/incidents/{incident.id}/decision"
    response = client.get(path)
    assert response.status_code == 200
    report = DecisionReport.model_validate(response.json())
    assert report.incident_id == incident.id
    assert report.options[0].current_cost == finding.cost.act_now
    missing = client.get("/api/v1/incidents/unknown/decision")
    assert missing.status_code == 404
    assert ErrorResponse.model_validate(missing.json()).code == "not_found"
    monkeypatch.setattr(main.runtime, "findings", {})
    missing = client.get(path)
    assert missing.status_code == 404
    assert ErrorResponse.model_validate(missing.json()).code == "not_found"


@pytest.mark.parametrize("kind", POLICIES)
def test_fractional_wait_horizon_still_reconciles_exactly(A, kind):
    A = A.with_override("crew_callout", 3400.12345).with_override("customer_hour_government", 5.12345)
    f = IncidentFacts(kind=kind, zone_types={"zone-gov": "government"}, leak_m3h=123.456789 if kind == "pipe_burst" else 0)
    record = compute(f, A, "fractional")
    lines = cost_lines(f, A, f"wait_{A[f'delay_hours_{kind}']}h")
    assert sum(line.result for line in lines) == record.cost.cost_of_delay
    assert round(sum(line.result for line in cost_lines(f, A, "fix_now")), 2) == record.cost.act_now


def test_non_pipe_break_even_is_hourly_loss_rate(A):
    f = IncidentFacts(kind="stuck_sensor", zone_types={})
    for row, w in zip(break_even(f, A), (6, 24, 72)):
        rate = 5800 * (1 - min(3, 1 + 0.1 * w / 12)) / (w - 1)
        assert row.parameter == "delay_per_hour" and row.unit == "EGP/h"
        assert row.value == pytest.approx(rate)


def test_stuck_sensor_retains_negative_wait_benefit(A, monkeypatch):
    monkeypatch.setitem(DEFAULTS, "response_hours", {**DEFAULTS["response_hours"], "low": 8, "high": 8})
    report = build_decision(IncidentFacts(kind="stuck_sensor", zone_types={}), A.with_override("response_hours", 8), "cheap-sensor")
    wait = next(o for o in report.options if o.id == "wait_6h")
    assert wait.current_cost == 850 * 6 + 5800 * 1.05
    assert wait.net_benefit_vs_fix_now < 0


def test_current_map_choice_can_differ_from_sampled_price_choice(A):
    f = IncidentFacts(kind="pipe_burst", zone_types={}, leak_m3h=600)
    report = build_decision(f, A, "flip")
    grid = report.decision_map
    assert report.recommended_option_id == "fix_now"
    assert grid.current_option_id == "isolate_fix"
    assert grid.cells[grid.y_values.index(grid.current_y)][grid.x_values.index(grid.current_x)] == grid.current_option_id
