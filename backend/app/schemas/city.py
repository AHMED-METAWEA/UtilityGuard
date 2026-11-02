"""The districts, health facilities and environment of the city as the healthcare-investment project describes them (contract 4.22).

Read-only. The twin did not generate any of these values: they are copied from the healthcare team's files (see `source`), and every figure of the
environment is theirs. The map shows them next to the twin's own water, electricity and cooling networks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas import LatLng, Model

MatchKind = Literal["direct", "type", "none"]


class CityStress(Model):
    """The healthcare team's 0..100 stress scores for one district and month (100 is the worst case)."""
    air: float
    waste: float
    water: float
    heat: float
    overall: float = Field(description="Their Environmental Stress Index. Below 20 is low, 20 to 35 moderate, 35 and above high")


class CityMetrics(Model):
    month: str = Field(description="The month the figures are for, YYYY-MM")
    residents: int
    # air
    aqi_mean: float
    aqi_max: float
    pm25_ugm3: float
    pm10_ugm3: float
    days_aqi_over_150: float
    # waste
    bin_fill_pct: float
    bin_hours_over_85_pct: float
    overflow_incidents: float
    collection_efficiency_pct: float
    uncollected_tons: float
    odor_pest_risk: float
    # water pollution and supply
    water_pollution_index: float
    ecoli_positive_pct: float
    chlorine_residual_mg_l: float
    turbidity_ntu: float
    supply_interruption_hours: float
    leak_incidents: float
    non_revenue_water_pct: float
    # weather
    avg_temp_c: float
    heat_days: float
    # their scores and the health counts
    stress: CityStress
    visits_demand: float
    respiratory_visits: float
    waterborne_visits: float
    heat_related_visits: float


class FacilityCounts(Model):
    hospitals: int
    clinics: int
    doctors: int


class CityDistrict(Model):
    district_key: str
    code: str
    name_ar: str
    kind: str = Field(description="The district type in their file, e.g. residential_district")
    area_km2: float
    position: LatLng = Field(description="Centroid only: their file has no boundary, so the map draws a point, not an outline")
    placed_at: LatLng | None = Field(default=None, description="Where the map draws it: on the zone of the twin it is matched to (their own coordinates are approximate and far from it). Absent when it has no match")
    match_kind: MatchKind = Field(description="How it relates to a zone of the twin: direct (the same district), type (the same kind of district), none")
    our_zone_ids: list[str]
    facilities: FacilityCounts
    metrics: CityMetrics


class CitySource(Model):
    repository: str
    commit: str
    sha256: str = Field(description="SHA-256 of the source panel the values were copied from")
    simulated: bool
    note: str


class CityDistricts(Model):
    month: str
    months: list[str] = Field(description="Every month the data covers, oldest first")
    source: CitySource
    districts: list[CityDistrict]


class CityFacility(Model):
    facility_id: str
    kind: Literal["hospital", "clinic", "doctors"]
    name_ar: str
    district_key: str
    position: LatLng = Field(description="As in their file")
    placed_at: LatLng | None = Field(default=None, description="Their offset from their district's centre, moved onto where that district is drawn. Absent when the district has no match")
