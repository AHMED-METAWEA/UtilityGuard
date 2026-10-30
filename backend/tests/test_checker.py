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


def test_a_small_whole_number_with_a_unit_is_a_quantity_not_a_count(tmp_path):
    allowed = allowed_numbers(record(tmp_path))
    out, removed = check("3 agents agree the leak is 9 m3/h and costs 3 EGP.", allowed)
    assert removed == ["9", "3"] and out.startswith("3 agents")


def test_each_figure_gets_a_verdict_and_the_value_it_matches(tmp_path):
    from app.agents.checker import allowed_labeled, figures
    rec = record(tmp_path)
    text = f"Act now for {rec.cost.act_now:,.0f} EGP, not 123,456 EGP; SS-03 at 14:32."
    got = figures(text, allowed_labeled(rec))
    assert [(f["verdict"], f["text"]) for f in got] == [("verified", f"{rec.cost.act_now:,.0f}"), ("removed", "123,456")]
    assert got[0]["matched"] == "cost of acting now" and got[0]["matched_value"] == rec.cost.act_now and got[1]["matched"] is None


def test_a_scale_word_cannot_hide_a_figure(tmp_path):
    rec = record(tmp_path)
    allowed = allowed_numbers(rec)
    out, removed = check("It costs 5 million EGP, or 9,999k, or 8.5M a month.", allowed)
    assert removed == ["5 million", "9,999k", "8.5M"] and out.count(REMOVED) == 3
    k = round(rec.cost.expected_saving / 1000)
    text = f"A saving of about {k}k EGP."  # the rounding an LLM naturally does still passes
    assert check(text, allowed) == (text, [])
