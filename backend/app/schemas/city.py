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


Group = Literal["both_higher", "outage_higher", "environment_higher", "both_lower", "no_outage_data"]


class DecisionRow(Model):
    """One district with the twin's outage figures and the healthcare team's environment and hospitals side by side. Nothing from the two sides is added together."""
    district_key: str
    district_code: str
    name_ar: str
    match_kind: MatchKind
    our_zone_ids: list[str]
    hospitals: int
    clinics: int
    specialists: int
    scenarios_affecting: float | None = Field(default=None, description="How many failed-asset scenarios touch it (an average over the zones when match_kind is type)")
    worst_cost_of_delay_egp: float | None = Field(default=None, description="The twin's largest cost of delay among the scenarios that touch it. Absent when it has no counterpart")
    worst_unserved_mw: float | None = None
    worst_scenario_id: str | None = None
    electricity_worst_cost_egp: float | None = Field(default=None, description="The district's own part of the worst substation trip that reaches it, by the same rule as worst_cost_of_delay_egp")
    water_worst_cost_egp: float | None = Field(default=None, description="The same for the worst water failure (a district pump or main). Only the cost lines that belong to the district; the water lost and the repair are shared by the incident")
    cooling_worst_cost_egp: float | None = Field(default=None, description="The same for the worst cooling-plant fault. Absent when no cooling plant serves the district")
    hospital_scenarios_sure: int = Field(description="Scenarios that hit every zone the district is matched to, so its hospitals are reached")
    hospital_scenarios_possible: int = Field(description="Scenarios that hit only some of its matched zones: an upper bound, the district may not be affected")
    esi_overall: float = Field(description="Their score, 0 to 100")
    esi_air: float
    esi_waste: float
    esi_water: float
    esi_heat: float
    esi_band: Literal["low", "moderate", "high"] = Field(description="Their bands: below 20 low, 20 to under 35 moderate, 35 and above high")
    highest_score: Literal["air", "waste", "water", "heat"]
    pressure_baseline: float | None = Field(default=None, description="Their hospital pressure ratio (demand over capacity) in their engine's default stress-calibrated view; above 1 the district is over 1.0 in that view; their app calls this view a scenario visualization, not observed utilization")
    over_capacity: bool | None = None
    pressure_growth_only: float | None = Field(default=None, description="The same district after their default population growth and no package")
    best_package: str | None = Field(default=None, description="The one of their four packages that leaves the lowest pressure when this district is the only target")
    best_package_pressure: float | None = None
    best_package_cost_m_egp: float | None = Field(default=None, description="Their 5-year cost of that package: a placeholder of their own configuration")
    outage_vs_median: Literal["higher", "lower"] | None = None
    environment_vs_median: Literal["higher", "lower"]
    group: Group


class Decision(Model):
    env_month: str
    source: str = Field(description="Where the table comes from and how it was made")
    simulated: bool
    median_cost_of_delay_egp: float
    median_esi_overall: float
    rows: list[DecisionRow]


Network = Literal["any", "electricity", "water", "cooling"]


class CostLine(Model):
    label: str
    cause: str
    amount: float


class CostInput(Model):
    name: str
    value: float
    unit: str


class CostStep(Model):
    name: str
    formula: str
    inputs: list[CostInput]
    result: float
    unit: str


class CostAssumption(Model):
    name: str
    key: str
    value: float
    unit: str
    status: Literal["official", "benchmark", "engineering", "planning"]
    basis: str


class CostPart(Model):
    """One matched zone of the district: the worst failure that reaches it and how the cost engine priced it."""
    zone_id: str
    scenario_id: str
    asset_name: str
    kind: str
    delay_hours: float
    response_hours: float
    zone_cost_of_delay_egp: float = Field(description="The cost lines that belong to this zone: the district's own part of the incident. It is the sum of own_lines")
    incident_cost_of_delay_egp: float = Field(description="The whole incident, with the lines shared by every zone it reaches (repair, water lost, cooling, knock-on)")
    own_lines: list[CostLine]
    shared_lines: list[CostLine]
    other_zones_egp: float = Field(description="The cost lines of the other zones the same incident reaches. own lines + other zones + shared lines = the whole incident")
    own_steps: list[CostStep]
    shared_steps: list[CostStep]
    total_step: CostStep | None = None
    assumptions: list[CostAssumption]


class DecisionCost(Model):
    """How one figure of the joint table was made, from the cost engine's own record of the scenario (nothing here is recomputed)."""
    district_key: str
    network: Network
    match_kind: MatchKind
    figure_egp: float | None = None
    how: str
    parts: list[CostPart]
