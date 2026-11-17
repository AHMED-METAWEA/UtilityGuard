"""Paired price scenarios and conditional response choices for an incident."""

from __future__ import annotations

import zlib

import numpy as np

from app.cost import (
    MC_RUNS, Assumptions, IncidentFacts, _a, _ai, _summary, assumption_keys, policy_kind, tornado,
)
from app.schemas import AgentFinding, Calculation, DecisionMap, DecisionOption, DecisionReport, BreakEven

WAITS = (6, 24, 72)


def multiplier(A: Assumptions, kind: str, hours: float) -> float:
    return min(A["late_multiplier_cap"],
               1 + (A[f"late_multiplier_{kind}"] - 1) * (hours / A[f"delay_hours_{kind}"]))


def option_ids(f: IncidentFacts) -> list[str]:
    return ["fix_now"] + (["isolate_fix"] if policy_kind(f) == "pipe_burst" else []) + [f"wait_{w}h" for w in WAITS]


def wait_cost(f: IncidentFacts, A: Assumptions, hours: float) -> float:
    """Displayed total, using the existing Cost's term-by-term cent rounding."""
    return sum(line.result for line in cost_lines(f, A, f"wait_{hours}h"))


def _totals(f: IncidentFacts, A: Assumptions) -> list[float]:
    n, kind = _summary(f, A), policy_kind(f)
    costs = [n["act_now"]]
    if kind == "pipe_burst":
        response = A["response_hours"]
        remaining = A[f"delay_hours_{kind}"] - response
        water = sum(t.value for t in n["terms"] if t.group == "loss:water")
        residual = n["c_delay"] - water * A["isolate_loss_reduction"]
        costs.append(n["c_delay"] * response + residual * remaining
                     + n["repair_now"] * A["planned_factor"] + A["crew_callout"])
    costs += [n["c_delay"] * w + n["repair_now"] * multiplier(A, kind, w) for w in WAITS]
    return costs


def cheapest(costs, ids) -> str | None:
    winners = [id_ for id_, cost in zip(ids, costs) if cost == min(costs)]
    return winners[0] if len(winners) == 1 else None


def paired_statistics(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    best = samples.min(axis=1, keepdims=True)
    return samples.mean(axis=0), (samples - best).mean(axis=0), (samples == best).mean(axis=0)


def cost_lines(f: IncidentFacts, A: Assumptions, option_id: str) -> list[Calculation]:
    n, kind = _summary(f, A), policy_kind(f)
    hours, mult = A["response_hours"], 1.0
    duration_inputs = [_ai(A, "response_hours")]
    exposure_formula = "response_hours"
    multiplier_inputs = []
    repair_formula = "1"
    if option_id.startswith("wait_"):
        delay_key, late_key = f"delay_hours_{kind}", f"late_multiplier_{kind}"
        hours = float(option_id.removeprefix("wait_").removesuffix("h"))
        mult = multiplier(A, kind, hours)
        duration_inputs = [_a("w", hours, "h", "rule", "Comparison repair horizon measured from now; waiting has no additional response-hour loss.")]
        exposure_formula = "w"
        multiplier_inputs = duration_inputs + [_ai(A, late_key), _ai(A, delay_key), _ai(A, "late_multiplier_cap")]
        repair_formula = f"min(late_multiplier_cap, 1 + ({late_key} - 1) × w / {delay_key})"
    elif option_id == "isolate_fix":
        delay_key = f"delay_hours_{kind}"
        hours, mult = A[delay_key], A["planned_factor"]
        duration_inputs = [_ai(A, delay_key)]
        exposure_formula = delay_key
        multiplier_inputs = [_ai(A, "planned_factor")]
        repair_formula = "planned_factor"
    lines = []
    for term in n["terms"]:
        repair = term.category == "repair"
        exposure, formula, inputs = hours, exposure_formula, duration_inputs
        if option_id == "isolate_fix" and term.group == "loss:water":
            exposure = A["response_hours"] + (hours - A["response_hours"]) * (1 - A["isolate_loss_reduction"])
            formula = f"response_hours + ({delay_key} - response_hours) × (1 - isolate_loss_reduction)"
            inputs = duration_inputs + [_ai(A, "response_hours"), _ai(A, "isolate_loss_reduction")]
        lines.append(Calculation(name=term.label, formula=f"({term.formula}) × ({repair_formula if repair else formula})",
                                 inputs=term.inputs + (multiplier_inputs if repair else inputs),
                                 result=round(term.value * (mult if repair else exposure), 2)))
    if option_id == "isolate_fix":
        lines.append(Calculation(name="Isolation crew call-out", formula="crew_callout", inputs=[_ai(A, "crew_callout")], result=round(A["crew_callout"], 2)))
    if option_id == "fix_now":
        adjustment = round(round(n["act_now"], 2) - sum(line.result for line in lines), 2)
        if adjustment:
            lines.append(Calculation(name="Cent reconciliation", formula="round(ActNow, 2) - sum(rounded cost lines)",
                                     inputs=[_a("ActNow", n["act_now"], "EGP", "rule")], result=adjustment))
    return lines


def break_even(f: IncidentFacts, A: Assumptions) -> list[BreakEven]:
    """Solve for pipe leak rate, or hourly consequence rate for other kinds."""
    n, kind = _summary(f, A), policy_kind(f)
    rows = []
    for w in WAITS:
        mult, response = multiplier(A, kind, w), A["response_hours"]
        numerator = n["repair_now"] * (1 - mult)
        denominator = w - response
        inputs = [_a("C_repair_now", n["repair_now"], "EGP", "rule"), _a("w", w, "h", "rule"),
                  _ai(A, "response_hours"), _ai(A, f"late_multiplier_{kind}"),
                  _ai(A, f"delay_hours_{kind}"), _ai(A, "late_multiplier_cap"), _a("m(w)", mult, "×", "rule")]
        parameter, unit = "delay_per_hour", "EGP/h"
        formula = "C_repair_now × (1 - m(w)) / (w - response_hours)"
        if kind == "pipe_burst":
            water = sum(t.value for t in n["terms"] if t.group == "loss:water")
            other = n["c_delay"] - water
            numerator -= other * denominator
            denominator *= A["water_price"]
            inputs += [_a("C_other", other, "EGP/h", "rule"), _ai(A, "water_price")]
            parameter, unit = "leak_m3h", "m3/h"
            formula = "[C_repair_now × (1 - m(w)) - C_other × (w - response_hours)] / [water_price × (w - response_hours)]"
        value = numerator / denominator if denominator else None
        reason = None
        if value is None:
            reason = "Every rate ties" if numerator == 0 else "No finite break-even rate"
        elif value < 0:
            reason = "No break-even within non-negative rates; the signed algebraic crossing is retained."
        rows.append(BreakEven(option_id=f"wait_{w}h", parameter=parameter, value=value, unit=unit,
                              formula=formula, inputs=inputs, reason=reason))
    return rows


def _axis(A: Assumptions, key: str) -> list[float]:
    item = A.item(key)
    values = np.linspace(item.low, item.high, 9).tolist()
    if item.low <= item.value <= item.high and item.value not in values:
        nearest = min(range(1, 8), key=lambda i: abs(values[i] - item.value))
        values[nearest] = item.value
    return sorted(values)


def decision_map(f: IncidentFacts, A: Assumptions, draws, ids, means, keys) -> DecisionMap:
    wait_index = min((i for i, id_ in enumerate(ids) if id_.startswith("wait_")), key=lambda i: means[i])

    def gap(key, value):
        costs = np.asarray([_totals(f, draw.with_override(key, value)) for draw in draws])
        return float(np.mean(costs[:, wait_index] - costs[:, 0]))

    rows = tornado(A, keys, gap)
    ranked = [row.input for row in rows]
    ranked += [key for key in keys if key not in ranked and A.item(key).low != A.item(key).high]
    x_key, y_key = ranked[:2]
    x, y = A.item(x_key), A.item(y_key)
    x_values, y_values = _axis(A, x_key), _axis(A, y_key)
    cells = [[cheapest(_totals(f, A.with_override(x_key, xv).with_override(y_key, yv)), ids)
              for xv in x_values] for yv in y_values]
    return DecisionMap(x_key=x_key, x_name=x.name, x_unit=x.unit, x_values=x_values,
                       y_key=y_key, y_name=y.name, y_unit=y.unit, y_values=y_values, cells=cells,
                       current_x=A[x_key], current_y=A[y_key], current_option_id=cheapest(_totals(f, A), ids))


def build_decision(f: IncidentFacts, A: Assumptions, incident_id: str, seed: int = 42) -> DecisionReport:
    kind, ids = policy_kind(f), option_ids(f)
    keys = assumption_keys(f, A) + ["late_multiplier_cap"] + (["isolate_loss_reduction", "planned_factor"] if kind == "pipe_burst" else [])
    sample_seed = zlib.crc32(incident_id.encode()) ^ seed
    rng = np.random.default_rng(sample_seed)
    draws = [A.sampled(rng, keys=keys) for _ in range(MC_RUNS)]
    samples = np.asarray([_totals(f, draw) for draw in draws])
    means, regrets, wins = paired_statistics(samples)
    recommended = cheapest(means, ids)
    options = []
    for i, id_ in enumerate(ids):
        lines = cost_lines(f, A, id_)
        p10, p50, p90 = (round(float(v), 2) for v in np.percentile(samples[:, i], [10, 50, 90]))
        hours = A["response_hours"] if id_ == "fix_now" else A[f"delay_hours_{kind}"] if id_ == "isolate_fix" else float(id_.removeprefix("wait_").removesuffix("h"))
        current = sum(line.result for line in lines)
        options.append(DecisionOption(id=id_, name={"fix_now": "Fix now", "isolate_fix": "Isolate, then fix"}.get(id_, f"Wait {hours:g} hours"),
                                      delay_hours=hours, current_cost=round(current, 2) if id_ == "fix_now" else current,
                                      expected_cost=round(float(means[i]), 2), p10=p10, p50=p50, p90=p90,
                                      expected_regret=round(float(regrets[i]), 2), win_share=float(wins[i]),
                                      win_share_text=f"cheapest in {wins[i]:.1%} of sampled price scenarios",
                                      cost_lines=lines, net_benefit_vs_fix_now=round(float(means[i] - means[0]), 2)))
    low_key, high_key = f"saving_min_{kind}", f"saving_max_{kind}"
    review = recommended is not None and not A[low_key] <= means[ids.index(recommended)] <= A[high_key]
    reason = f"{kind} recommended expected cost {means[ids.index(recommended)]:,.2f} EGP is outside the existing planning bounds [{A[low_key]:g}, {A[high_key]:g}] EGP; these bounds were calibrated for savings, so verify incident scope and costs." if review else None
    return DecisionReport(incident_id=incident_id, recommended_option_id=recommended, runs=MC_RUNS, seed=sample_seed,
                          assumptions=[A.item(k) for k in keys + [low_key, high_key]], options=options,
                          break_even=break_even(f, A), decision_map=decision_map(f, A, draws, ids, means, keys),
                          review=review, review_reason=reason)


def from_finding(finding: AgentFinding, A: Assumptions, seed: int = 42) -> DecisionReport:
    """Recover unrounded facts and registered values from the finding's cost snapshot."""
    explanation = finding.explain
    for item in explanation.assumptions:
        A = A.with_override(item.key, item.value)
        if item.user_changed:
            A.changed[item.key] = item.source
        else:
            A.changed.pop(item.key, None)
    kind = next(item.key.removeprefix("delay_hours_") for item in explanation.assumptions if item.key.startswith("delay_hours_"))
    zones, facts = {}, {}
    names = {"Water loss": ("leak_m3h", "Q_leak", 1), "Energy not served, ": ("unserved_mw", "E_unserved", 1000),
             "Energy waste": ("excess_mw", "E_actual − E_expected", 1000),
             "Cooling shortfall": ("cooling_deficit_tons", "Q_deficit", 1),
             "Knock-on overdraw in another network": ("secondary_mw", "ΔE_secondary", 1000)}
    for calc in explanation.calculations:
        inputs = {i.name: i for i in calc.inputs}
        if calc.name.startswith("Service disruption, "):
            zones[calc.name.removeprefix("Service disruption, ")] = inputs["customer_hour_rate"].key.removeprefix("customer_hour_")
        for label, (field, name, divisor) in names.items():
            if calc.name == label or label.endswith(", ") and calc.name.startswith(label):
                facts[field] = inputs[name].value / divisor
                if field != "secondary_mw":
                    facts.update(input_source=inputs[name].source, input_basis=inputs[name].basis)
    return build_decision(IncidentFacts(kind=kind, zone_types=zones, reasons=explanation.reasons,
                                       confidence=explanation.confidence, **facts), A, finding.incident_id, seed)
