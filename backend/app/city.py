"""Reads the healthcare team's districts, facilities and environment from `integration/health/data` and serves them as they are.

Nothing is computed or changed here: the files are copied from the other project (`integration/health/extract_environment.py` records where from and a SHA-256
of the source), and this module only joins them by `district_key`. If the folder is missing the feature is simply off.
"""

from __future__ import annotations

import csv
import json
import os
import math
import re
from datetime import date
from pathlib import Path

from app.schemas import LatLng
from app.schemas.city import CityDistrict, CityDistricts, CityFacility, CityMetrics, CitySource, CityStress, FacilityCounts

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO / "integration" / "health" / "data"
RING_DEG = 0.005  # about 550 m: where districts that share a zone of the twin sit around that zone's centre
KIND = {"hospital": "hospitals", "clinic": "clinics", "doctors": "doctors"}

# field of CityMetrics -> column of the environment file
NUMBERS = {
    "aqi_mean": "p3_avg_aqi", "aqi_max": "p3_max_aqi", "pm25_ugm3": "p3_pm25_ugm3", "pm10_ugm3": "p3_pm10_ugm3", "days_aqi_over_150": "p3_days_aqi_gt150",
    "bin_fill_pct": "p2_avg_bin_fill_pct", "bin_hours_over_85_pct": "p2_bin_hours_ge85_pct", "overflow_incidents": "p2_overflow_incidents",
    "collection_efficiency_pct": "p2_collection_efficiency_pct", "uncollected_tons": "p2_uncollected_tons", "odor_pest_risk": "p2_odor_pest_risk_index",
    "water_pollution_index": "pw_water_pollution_index", "ecoli_positive_pct": "pw_ecoli_positive_pct", "chlorine_residual_mg_l": "pw_chlorine_residual_mg_l",
    "turbidity_ntu": "pw_turbidity_ntu", "supply_interruption_hours": "pw_supply_interruption_hours", "leak_incidents": "pw_leak_incidents",
    "non_revenue_water_pct": "pw_nrw_pct", "avg_temp_c": "avg_temp_c", "heat_days": "heat_days_ge38c", "visits_demand": "total_visits_demand",
    "respiratory_visits": "p3_respiratory_visits_total", "waterborne_visits": "pw_waterborne_visits_total", "heat_related_visits": "p3_heat_related_visits",
}
STRESS = {"air": "esi_air", "waste": "esi_waste", "water": "esi_water", "heat": "esi_heat", "overall": "environmental_stress_index"}


class CityError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class CityData:
    def __init__(self, folder: Path | str | None = None, zone_centers: dict[str, LatLng] | None = None):
        self.folder = Path(folder or os.getenv("CITY_DATA_DIR") or DEFAULT_DIR)
        self._zone_centers = zone_centers or {}
        self._anchor: dict[str, LatLng] = {}
        self.available = False
        self._districts: list[dict] = []
        self._facilities: list[CityFacility] = []
        self._env: dict[tuple[str, str], dict] = {}
        self._months: list[str] = []
        self._mapping: dict[str, dict] = {}
        self._source: CitySource | None = None
        try:
            self._load()
            self.available = True
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            self.available = False

    def _load(self) -> None:
        d = self.folder
        self._districts = _rows(d / "districts.csv")
        self._facilities = [CityFacility(facility_id=r["facility_id"], kind=r["fclass"], name_ar=r["facility_name"], district_key=r["district_key"],
                                         position=LatLng(lat=float(r["lat"]), lng=float(r["lon"]))) for r in _rows(d / "healthcare_facilities.csv")]
        for r in _rows(d / "environment_monthly.csv"):
            self._env[(r["district_key"], r["month"])] = r
        self._months = sorted({m for _, m in self._env})
        self._mapping = {r["district_key"]: r for r in _rows(d.parent / "zone_mapping.csv")}
        src = json.loads((d / "SOURCE.json").read_text(encoding="utf-8"))
        self._source = CitySource(repository=src["source_repository"], commit=src["source_commit"], sha256=src["source_sha256"], simulated=bool(src["simulated"]),
                                  note="Copied unchanged from the healthcare team's master_panel.csv. The twin did not generate these values.")
        if not self._districts or not self._months:
            raise ValueError("empty city data")
        self._anchor = self._place()

    def _place(self) -> dict[str, LatLng]:
        """Where each district is drawn on the twin's map: on the zone it is matched to, not at the coordinates of its own file.

        Their centroids are rounded to three decimals and step evenly from one district to the next, and the nearest of them is about 5 km from the zone of the
        twin it is matched to, so drawing them as given would put every symbol on the wrong ground. A district matched to one zone of its own sits on that zone's
        centre; districts that match the same zone (or a type of zones, taken in turn) are spread on a small ring around it. A district with no match is left out.
        """
        zone_of: dict[str, str] = {}
        turn: dict[str, int] = {}
        for r in self._districts:
            m = self._mapping.get(r["district_key"], {})
            ids = [z for z in m.get("our_zone_ids", "").split(";") if z and z in self._zone_centers]
            if not ids:
                continue
            i = turn.get(m["our_zone_ids"], 0) if m.get("match_kind") == "type" else 0
            if m.get("match_kind") == "type":
                turn[m["our_zone_ids"]] = i + 1
            zone_of[r["district_key"]] = ids[i % len(ids)]
        crowd: dict[str, list[str]] = {}
        for k, z in zone_of.items():
            crowd.setdefault(z, []).append(k)
        out: dict[str, LatLng] = {}
        for z, keys in crowd.items():
            c = self._zone_centers[z]
            for i, k in enumerate(keys):
                if len(keys) == 1:
                    out[k] = c
                else:
                    a = 2 * math.pi * i / len(keys)
                    out[k] = LatLng(lat=c.lat + RING_DEG * math.cos(a), lng=c.lng + RING_DEG * math.sin(a) / math.cos(math.radians(c.lat)))
        return out

    @property
    def months(self) -> list[str]:
        return [m[:7] for m in self._months]

    def _month(self, month: str | None) -> str:
        if not month:
            return self._months[-1]
        if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", month):
            raise CityError(422, "invalid_month", "A month looks like 2026-09.")
        try:
            date.fromisoformat(month[:7] + "-01")
        except ValueError as e:
            raise CityError(422, "invalid_month", "A month looks like 2026-09.") from e
        key = month[:7] + "-01"
        if key not in self._months:
            raise CityError(404, "no_month", f"No data for {month[:7]}; the data covers {self.months[0]} to {self.months[-1]}.")
        return key

    def districts(self, month: str | None = None) -> CityDistricts:
        if not self.available:
            raise CityError(404, "no_city_data", "The healthcare team's city data is not installed.")
        key = self._month(month)
        counts: dict[str, dict[str, int]] = {}
        for f in self._facilities:
            counts.setdefault(f.district_key, {"hospitals": 0, "clinics": 0, "doctors": 0})[KIND[f.kind]] += 1
        out = []
        for r in self._districts:
            e = self._env[(r["district_key"], key)]
            m = self._mapping.get(r["district_key"], {})
            metrics = CityMetrics(month=key[:7], residents=int(float(e["residents_registered"])), stress=CityStress(**{k: float(e[c]) for k, c in STRESS.items()}),
                                  **{k: float(e[c]) for k, c in NUMBERS.items()})
            out.append(CityDistrict(district_key=r["district_key"], code=r["code"], name_ar=r["name"], kind=r["fclass"], area_km2=float(r["area_km2"]),
                                    position=LatLng(lat=float(r["centroid_lat"]), lng=float(r["centroid_lon"])), placed_at=self._anchor.get(r["district_key"]), match_kind=m.get("match_kind", "none"),
                                    our_zone_ids=[z for z in m.get("our_zone_ids", "").split(";") if z],
                                    facilities=FacilityCounts(**counts.get(r["district_key"], {"hospitals": 0, "clinics": 0, "doctors": 0})), metrics=metrics))
        return CityDistricts(month=key[:7], months=self.months, source=self._source, districts=out)  # type: ignore[arg-type]

    def facilities(self) -> list[CityFacility]:
        if not self.available:
            raise CityError(404, "no_city_data", "The healthcare team's city data is not installed.")
        centre = {r["district_key"]: (float(r["centroid_lat"]), float(r["centroid_lon"])) for r in self._districts}
        out = []
        for f in self._facilities:
            a = self._anchor.get(f.district_key)
            if a is None:
                out.append(f)
                continue
            lat0, lng0 = centre[f.district_key]  # keep the facility's offset from its own district's centre, moved onto where that district is drawn
            out.append(f.model_copy(update={"placed_at": LatLng(lat=a.lat + f.position.lat - lat0, lng=a.lng + f.position.lng - lng0)}))
        return out
