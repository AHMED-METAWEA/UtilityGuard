"""Copies the environmental columns of the healthcare team's `master_panel.csv` into `data/environment_monthly.csv`, and records where they came from.

    python extract_environment.py --source /path/to/master_panel.csv [--commit 64059cf510b0...]

The source file is only READ. Only the month is rewritten, as 2025-01-01, whichever of the two date formats their file uses (see `iso_month`). The extract keeps the keys (`district_key`, `month`) and the columns the map shows (air, waste, water pollution, heat, their
stress scores and the health counts), unchanged. `data/SOURCE.json` holds the SHA-256 of the whole source file so anyone can check the extract was made
from the version they have. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def iso_month(text: str) -> str:
    """Their file wrote the month as 2025-01-01 and, since commit 9179942, as 1/1/2025 (month/day/year). Both mean the first of that month: the copy always writes 2025-01-01."""
    t = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-01")
        except ValueError:
            pass
    raise SystemExit(f"cannot read the month {text!r}: expected 2025-01-01 or 1/1/2025")
OUT = HERE / "data"
REPO = "https://github.com/Gehan3/utilization-Environmental-measurements-Healthcare_Investment_Planning"
COLUMNS = [
    "district_key", "month", "residents_registered", "avg_temp_c", "heat_days_ge38c",
    "p3_avg_aqi", "p3_max_aqi", "p3_pm25_ugm3", "p3_pm10_ugm3", "p3_days_aqi_gt150",
    "p2_avg_bin_fill_pct", "p2_bin_hours_ge85_pct", "p2_overflow_incidents", "p2_collection_efficiency_pct", "p2_uncollected_tons", "p2_odor_pest_risk_index",
    "pw_water_pollution_index", "pw_ecoli_positive_pct", "pw_chlorine_residual_mg_l", "pw_turbidity_ntu", "pw_supply_interruption_hours", "pw_leak_incidents", "pw_nrw_pct",
    "esi_heat", "esi_air", "esi_water", "esi_waste", "environmental_stress_index", "need_score",
    "total_visits_demand", "p3_respiratory_visits_total", "pw_waterborne_visits_total", "p3_heat_related_visits", "pc_centers", "hospital_centers", "cpr",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--commit", default="unknown", help="git commit of the source repository the file came from")
    args = ap.parse_args()
    src = Path(args.source)
    raw = src.read_bytes()
    with src.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"the source has no column(s) {missing}")
        rows = [{c: (iso_month(row[c]) if c == "month" else row[c]) for c in COLUMNS} for row in reader]
    rows.sort(key=lambda r: (r["month"], r["district_key"]))
    OUT.mkdir(exist_ok=True)
    with (OUT / "environment_monthly.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    months = sorted({r["month"] for r in rows})
    (OUT / "SOURCE.json").write_text(json.dumps({
        "what": "Environmental columns of the healthcare team's master_panel.csv, copied unchanged. The twin did not generate or alter any of these values.",
        "source_repository": REPO, "source_commit": args.commit, "source_file": "Data/Processed/master_panel.csv",
        "source_sha256": hashlib.sha256(raw).hexdigest(), 
        "extracted_columns": COLUMNS, "rows": len(rows), "districts": len({r["district_key"] for r in rows}), "first_month": months[0], "last_month": months[-1],
        "extracted_at": datetime.now(timezone.utc).isoformat(), "simulated": True,
        "other_files": {"districts.csv": "district list with centroids and areas, sent by the healthcare team", "healthcare_facilities.csv": "hospitals, clinics and specialist doctors, sent by the healthcare team"},
    }, indent=1, ensure_ascii=False))
    print(f"wrote {len(rows)} rows, {len(COLUMNS)} columns, months {months[0]} to {months[-1]}")


if __name__ == "__main__":
    main()
