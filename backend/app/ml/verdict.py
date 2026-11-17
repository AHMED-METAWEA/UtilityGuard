"""Is this forecast fit to plan with? A verdict from the numbers the accuracy page already shows, with every check and its figures stated.

A model can be accurate on average and wrong on the days that matter. The verdict is the weakest of the checks, except that a bad kind of day is a caveat and not a failure of the whole forecast,
so one bad day type cannot hide behind a good average and cannot condemn a good forecast either, and it is computed from the evidence stored with the model, so the rules can change without retraining.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CAIRO = "Africa/Cairo"
SKILL_OK, SKILL_WEAK = 0.25, 0.0  # share of the best simple baseline's error that the model removes
UNDER_OK, UNDER_WEAK = 0.08, 0.15  # how far below the nominal share the band may fall
OVER_OK, OVER_WEAK = 0.20, 0.30  # a band wider than needed is safe but says less, so it is allowed to be further from nominal
DAY_OK, DAY_WEAK = 2.0, 3.0  # the worst day type's error as a multiple of the overall error (and never judged below 5% in absolute terms)
DAY_FLOOR = 5.0


def day_type_scores(frame: pd.DataFrame) -> list[dict]:
    """Error by kind of day. `frame` is indexed by time with y, p (network totals), heat (0 or 1) and holiday (the holiday factor, 0 on normal days)."""
    local = frame.index.tz_convert(CAIRO)
    weekend = np.asarray(local.weekday.isin([4, 5]))
    heat, hol = frame["heat"].values > 0, frame["holiday"].values > 0
    kinds = {
        "Working day": ~weekend & ~hol & ~heat,
        "Weekend (Friday, Saturday)": weekend & ~hol & ~heat,
        "Holiday on a working day": ~weekend & hol,
        "Holiday on a weekend day": weekend & hol,
        "Heat wave": heat,
    }
    out = []
    for name, mask in kinds.items():
        if not mask.any():
            continue
        y, p = frame["y"].values[mask], frame["p"].values[mask]
        out.append({"name": name, "days": int(len({d for d in local[mask].date})), "mape": float(np.mean(np.abs(p - y) / np.maximum(y, 1e-6)) * 100)})
    return out


def assess(skill: float, coverage: float, nominal: float, mape: float, by_day_type: list[dict]) -> dict:
    checks = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add("Better than the best simple baseline", "ok" if skill >= SKILL_OK else "weak" if skill > SKILL_WEAK else "fail",
        f"It removes {skill:.0%} of the error of the best baseline (ok from {SKILL_OK:.0%}).")
    gap = coverage - nominal
    if gap < 0:
        status = "ok" if -gap <= UNDER_OK else "weak" if -gap <= UNDER_WEAK else "fail"
        detail = f"The band held {coverage:.0%} of real values against {nominal:.0%} intended, so it is narrower than it claims."
    else:
        status = "ok" if gap <= OVER_OK else "weak" if gap <= OVER_WEAK else "fail"
        detail = f"The band held {coverage:.0%} of real values against {nominal:.0%} intended" + (", wider than needed but safe." if gap > 0.05 else ".")
    add("Band holds the share it promises", status, detail)
    worst = max(by_day_type, key=lambda d: d["mape"]) if by_day_type else None
    if worst:
        limit_ok, limit_weak = max(DAY_OK * mape, DAY_FLOOR), max(DAY_WEAK * mape, DAY_FLOOR * 1.5)
        add("No kind of day is much worse than the average", "ok" if worst["mape"] <= limit_ok else "weak" if worst["mape"] <= limit_weak else "fail",
            f"The worst is {worst['name'].lower()} at {worst['mape']:.1f}% against {mape:.1f}% overall (ok up to {limit_ok:.1f}%).")
    # Skill and the band describe the forecast as a whole; a bad kind of day is a caveat on it. A forecast that is good except on holidays is
    # "indicative" and says which days to distrust, not "unreliable".
    if any(c["status"] == "fail" for c in checks[:2]):
        label = "unreliable"
    elif any(c["status"] != "ok" for c in checks):
        label = "indicative"
    else:
        label = "reliable"
    return {"label": label, "checks": checks}
