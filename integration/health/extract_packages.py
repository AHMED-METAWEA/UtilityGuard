"""Runs the healthcare team's own what-if engine, unchanged, and records what it gives for each district under each of its four packages.

    <their python> extract_packages.py --app /path/to/their/repository

Run it with the Python that has THEIR requirements installed (the one `run_all.sh` sets up): their engine needs pandas and imports their dashboard code. Their
repository is only READ and nothing is added to it. For each district the engine is called with that district as the only target and their default settings,
so the figures are what their dashboard shows when that single district is selected: the pressure ratio (demand over capacity) in their default "stress-calibrated"
view, the same district after population growth with no package, and after each of the four packages with its 5-year cost. `data/PACKAGES_SOURCE.json` holds
the settings, the commit of their repository and the SHA-256 of the panel and of the engine files, so the extract can be checked against the version it came from.
The costs and effects are the placeholders of their own `dashboard/config.py`, which says so itself.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="their repository (read only)")
    args = ap.parse_args()
    app = Path(args.app).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(app))
    import os

    os.chdir(app)
    from dashboard.config import FINANCIAL_ASSUMPTIONS as F, SCENARIO_PACKAGES
    from dashboard.data_loader import load_panel
    from dashboard.simulation_engine import derive_baseline_pressure, simulate_policy_package

    mode = "Peak-surge / stress-calibrated capacity"  # the default view of their page
    baseline = derive_baseline_pressure(load_panel(), mode)
    settings = {"population_growth_pct": float(F["default_population_growth_pct"]), "capacity_expansion_pct": float(F["default_capacity_expansion_pct"]),
                "prevention_reach_pct": float(F["default_prevention_reach_pct"]), "access_improvement_minutes": float(F["default_access_improvement_minutes"]),
                "budget_limit_m_egp": float(F["default_budget_m_egp"])}
    no_package = {**settings, "prevention_reach_pct": 0.0}  # their prevention package with zero reach: population growth only
    packages = list(SCENARIO_PACKAGES.values())
    rows = []
    for key in baseline["district_key"].astype(str):
        row = {"district_key": key, "pressure_baseline": float(baseline.loc[baseline["district_key"].astype(str) == key, "baseline_pressure"].iloc[0])}
        grown = simulate_policy_package(baseline, no_package, "prevention", [key])
        row["pressure_growth_only"] = float(grown.loc[grown["district_key"].astype(str) == key, "scenario_pressure"].iloc[0])
        for p in packages:
            sim = simulate_policy_package(baseline, settings, p, [key])
            row[f"{p}_pressure"] = float(sim.loc[sim["district_key"].astype(str) == key, "scenario_pressure"].iloc[0])
            row[f"{p}_cost_m_egp"] = float(sim["cost_m_egp"].iloc[0])
        rows.append(row)
    rows.sort(key=lambda r: r["district_key"])
    OUT.mkdir(exist_ok=True)
    cols = list(rows[0])
    with (OUT / "their_packages.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in rows)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=app).stdout.strip() or "unknown"
    (OUT / "PACKAGES_SOURCE.json").write_text(json.dumps({
        "what": "Output of the healthcare team's own what-if engine (dashboard/simulation_engine.py), called unchanged once per district with that district as the only target.",
        "source_commit": commit, "capacity_view": mode, "settings": settings, "package_labels": SCENARIO_PACKAGES,
        "columns": {"pressure_baseline": "demand over capacity in their default stress-calibrated view; above 1.0 the district is over capacity",
                    "pressure_growth_only": "the same district after the population growth of the settings and no package",
                    "<package>_pressure": "after the package (and the growth)", "<package>_cost_m_egp": "their 5-year cost of the package, a placeholder of their config"},
        "files_sha256": {"Data/Processed/master_panel.csv": sha(app / "Data/Processed/master_panel.csv"), "dashboard/simulation_engine.py": sha(app / "dashboard/simulation_engine.py"),
                         "dashboard/config.py": sha(app / "dashboard/config.py")},
        "extracted_at": datetime.now(timezone.utc).isoformat(), "simulated": True,
    }, indent=1, ensure_ascii=False))
    print(f"wrote {len(rows)} districts, {len(cols)} columns from their commit {commit[:10]}")


if __name__ == "__main__":
    main()
