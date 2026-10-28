from app.agents.checker import REMOVED, allowed_numbers, check
from app.cost import Assumptions, IncidentFacts, compute


def record(tmp_path):
    return compute(IncidentFacts(kind="leak", zone_types={"zone-gov": "government"}, leak_m3h=140), Assumptions(tmp_path / "a.json"), "inc-1")


def test_exact_and_rounded_figures_pass(tmp_path):
    rec = record(tmp_path)
    allowed = allowed_numbers(rec)
    text = f"Act now for {rec.cost.act_now:,.0f} EGP and save {rec.cost.expected_saving:,.0f} EGP; the leak is 140 m3/h over 6 h."
    out, removed = check(text, allowed)
    assert removed == [] and out == text


def test_invented_figure_is_removed(tmp_path):
    out, removed = check("This will cost 99,999 EGP.", allowed_numbers(record(tmp_path)))
    assert removed == ["99,999"] and REMOVED in out


def test_ids_times_and_small_counts_are_not_quantities(tmp_path):
    text = "SS-03 feeds P-04 and DCP-01 in R5 at 14:32; 3 agents agree."
    out, removed = check(text, allowed_numbers(record(tmp_path)))
    assert removed == [] and out == text


def test_negative_and_decimal_values(tmp_path):
    out, removed = check("Load changes by -109.29 MW.", {109.29})
    assert removed == [] and "-109.29" in out
