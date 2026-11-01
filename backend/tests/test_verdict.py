"""The reliability verdict, the error by kind of day, and the calendar that makes the weekend cases visible to the model."""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.ml import HISTORY_DAYS, TEST_DAYS, scenario_calendar
from app.ml.verdict import assess, day_type_scores


def frame(rows):
    """rows: (local date, hour, y, p, heat, holiday). Indexed by Cairo-local time, as the model's out-of-fold frame is."""
    idx = pd.DatetimeIndex([pd.Timestamp(datetime(d.year, d.month, d.day, h), tz="Africa/Cairo").tz_convert("UTC") for d, h, *_ in rows])
    return pd.DataFrame({"y": [r[2] for r in rows], "p": [r[3] for r in rows], "heat": [r[4] for r in rows], "holiday": [r[5] for r in rows]}, index=idx)


def test_error_is_reported_for_each_kind_of_day():
    mon, fri = date(2026, 11, 2), date(2026, 11, 6)  # Monday and Friday
    rows = [(mon, 10, 100, 104, 0, 0), (mon, 11, 100, 104, 0, 0), (fri, 10, 100, 102, 0, 0),
            (date(2026, 11, 3), 10, 100, 120, 0, 1.3),  # a holiday on a Tuesday
            (date(2026, 11, 7), 10, 100, 103, 0, 1.3),  # a holiday on a Saturday
            (date(2026, 11, 4), 10, 100, 108, 45, 0)]
    got = {d["name"]: d for d in day_type_scores(frame(rows))}
    assert got["Working day"]["mape"] == 4.0 and got["Working day"]["days"] == 1
    assert got["Weekend (Friday, Saturday)"]["mape"] == 2.0
    assert got["Holiday on a working day"]["mape"] == 20.0
    assert got["Holiday on a weekend day"]["mape"] == 3.0
    assert got["Heat wave"]["mape"] == 8.0


def day(name, mape, days=5):
    return {"name": name, "days": days, "mape": mape}


def test_a_forecast_that_passes_every_check_is_reliable():
    v = assess(skill=0.5, coverage=0.8, nominal=0.8, mape=3.0, by_day_type=[day("Working day", 3.0), day("Heat wave", 4.0)])
    assert v["label"] == "reliable" and [c["status"] for c in v["checks"]] == ["ok", "ok", "ok"]


def test_a_band_wider_than_needed_is_safe_but_one_narrower_than_it_claims_is_not():
    wide = assess(0.5, 0.97, 0.8, 3.0, [day("Working day", 3.0)])
    narrow = assess(0.5, 0.60, 0.8, 3.0, [day("Working day", 3.0)])
    assert wide["checks"][1]["status"] in ("ok", "weak") and wide["label"] != "unreliable"
    assert narrow["checks"][1]["status"] == "fail" and narrow["label"] == "unreliable"


def test_no_skill_over_the_simple_baseline_is_unreliable():
    v = assess(skill=-0.1, coverage=0.8, nominal=0.8, mape=3.0, by_day_type=[day("Working day", 3.0)])
    assert v["label"] == "unreliable" and v["checks"][0]["status"] == "fail"


def test_a_bad_kind_of_day_is_a_caveat_that_names_the_day_not_a_verdict_on_everything():
    v = assess(skill=0.5, coverage=0.8, nominal=0.8, mape=3.7, by_day_type=[day("Working day", 3.9), day("Holiday on a working day", 11.6, 7)])
    assert v["label"] == "indicative"
    check = v["checks"][2]
    assert check["status"] == "fail" and "holiday on a working day" in check["detail"] and "11.6%" in check["detail"] and "3.7%" in check["detail"]


def test_every_check_states_the_figures_behind_its_status():
    v = assess(0.49, 0.89, 0.8, 3.7, [day("Working day", 3.9)])
    assert "49%" in v["checks"][0]["detail"] and "89%" in v["checks"][1]["detail"] and "80%" in v["checks"][1]["detail"]


def test_the_training_calendar_has_holidays_and_heat_waves_on_the_weekend_too():
    """The model never saw a holiday on a Friday or Saturday because the calendar skipped them, so the first Friday someone ran the holiday scenario
    the water forecast was 7% to 11% out."""
    start = date(2026, 8, 18)
    cal = scenario_calendar(42, start)
    test_from = start + timedelta(days=HISTORY_DAYS - TEST_DAYS)
    for kind, idx in (("heat", 0), ("holiday", 1)):
        days = [d for d, v in cal.items() if v[idx]]
        assert sum(d.weekday() in (4, 5) for d in days if d < test_from) >= 2, kind
        assert sum(d.weekday() in (4, 5) for d in days if d >= test_from) >= 1, kind
    assert all(not (v[0] and v[1]) for v in cal.values())  # a day is a heat day or a holiday, never both
    assert len(cal) == len(set(cal))


def test_a_first_start_inside_a_running_event_loop_does_not_crash():
    """uvicorn imports the app inside its own event loop. With no cached models the detector evaluation (which uses asyncio.run) used to be called from
    there, and a fresh clone's first start failed with 'asyncio.run() cannot be called from a running event loop'."""
    import asyncio
    from app.ml.service import outside_loop

    def needs_its_own_loop():
        async def one():
            return 7
        return asyncio.run(one())

    assert outside_loop(needs_its_own_loop) == 7  # no loop running
    async def inside():
        return outside_loop(needs_its_own_loop)
    assert asyncio.run(inside()) == 7  # a loop is running: the call moves to a thread
