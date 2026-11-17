"""The siting what-if: put a hospital, an expansion, a clinic or any facility in a zone and see what it does to the water, electricity and cooling networks.

Everything is computed by running the twin's own physics (see `app/siting.py`); the loads of a facility are planning assumptions, listed with their range in
every answer, and the probabilities are shares of simulated days under those assumptions, not forecasts of the real world.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas import Model

FacilityKind = Literal["hospital", "hospital_expansion", "clinic", "custom"]
Network = Literal["electricity", "water", "cooling"]
Condition = Literal["normal", "heat", "substation_loss"]


class SitingRequest(Model):
    zone_id: str
    kind: FacilityKind = "hospital"
    units: float = Field(default=300, gt=0, le=5000, description="Beds for a hospital or an expansion, number of clinics for a clinic, number of identical facilities for a custom load")
    water_m3h: float | None = Field(default=None, ge=0, le=5000, description="Custom load only: design water demand of one facility, m3/h")
    electricity_mw: float | None = Field(default=None, ge=0, le=500, description="Custom load only: design electrical demand of one facility, MW")
    cooling_mw: float | None = Field(default=None, ge=0, le=500, description="Custom load only: design cooling demand of one facility, MW thermal")
    capex_m_egp: float | None = Field(default=None, ge=0, le=100000, description="Cost of building the facility, M EGP. Left empty, a new hospital and an expansion use the healthcare team's placeholder")
    runs: int = Field(default=150, ge=20, le=600)


class SitingAssumption(Model):
    name: str
    value: float
    low: float
    high: float
    unit: str
    basis: str


class SitingLoad(Model):
    water_m3h: float
    electricity_mw: float
    cooling_mw: float
    on_site_chillers_mw: float = Field(description="Electrical draw of the facility's own chillers when the zone has no district cooling (already inside electricity_mw)")


class SitingZone(Model):
    id: str
    name: str
    type: str
    substation: str
    pump: str
    cooling_plant: str | None


class NetworkRisk(Model):
    network: Network
    asset: str | None = Field(description="The asset that limits most often after the facility is built")
    limit: str = Field(description="What counts as a breach for this network, in words")
    probability_before: float = Field(description="Share of simulated days with at least one breach, without the facility")
    probability_after: float
    watch_before: float = Field(description="Share of days that reach the warning level (85% of the limit for electricity and cooling, a pipe under its normal pressure for water)")
    watch_after: float
    utilisation_before_p95: float | None = None
    utilisation_after_p95: float | None = None
    utilisation_after_max: float | None = None


class ConditionResult(Model):
    condition: Condition
    label: str
    days: int
    risks: list[NetworkRisk]
    any_before: float
    any_after: float
    extra_unserved_mwh_per_day: float = Field(description="Electricity above the firm capacity that the facility adds, MWh per simulated day (mean over the days)")
    extra_unserved_cost_egp_per_day: float
    extra_unserved_cost_low_egp_per_day: float
    extra_unserved_cost_high_egp_per_day: float


class Mitigation(Model):
    network: Network
    package: str
    assets: list[str]
    cost_m_egp: float
    reason: str


class SitingCost(Model):
    capex_m_egp: float | None
    capex_basis: str
    mitigations: list[Mitigation]
    mitigation_m_egp: float
    total_m_egp: float | None = Field(description="Facility cost plus the utility upgrades the simulation says are needed. Empty when the facility has no cost")
    note: str


class SitingDistrict(Model):
    district_key: str
    district_code: str
    match_kind: str
    hospitals: int
    their_pressure_baseline: float | None = None
    their_over_capacity: bool | None = None
    esi_overall: float | None = None


class SitingResult(Model):
    zone: SitingZone
    kind: FacilityKind
    units: float
    unit: str
    load: SitingLoad
    assumptions: list[SitingAssumption]
    conditions: list[ConditionResult]
    cost: SitingCost
    districts: list[SitingDistrict]
    verdict: str
    runs: int
    seed: int
    simulated: bool = True
