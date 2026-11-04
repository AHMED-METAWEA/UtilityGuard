"""Early warning, "why this number?" records and the guardrail test."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import CalcInput, Model, Network, Reason


class RiskPoint(Model):
    t: datetime
    risk: float = Field(description="Failure risk score of the asset (0 to 1) at this moment")
    peer: float = Field(description="The same score with the condition signals set to the peer median: what the asset's age and load alone explain")


class WarningSignal(Model):
    label: str
    value: float
    peer_value: float = Field(description="Median of the same signal over the other assets of the same kind")
    unit: str = ""


class WarningCost(Model):
    unplanned: float = Field(description="EGP if it fails: losses, disruption, late repair (cost model, pump failure proxy)")
    planned: float = Field(description="EGP to repair it on a planned date: planned_factor x emergency repair")
    break_even: float = Field(description="planned / unplanned. Maintenance pays when the chance of failing is above this")
    risk: float
    verdict: Literal["maintain_now", "watch"]
    expected_saving: float = Field(description="risk x unplanned - planned, EGP; positive favours maintaining now")


class WarningEvidence(Model):
    """What the twin's own test runs showed when a wearing pump was left alone (`scripts/stress/earlywarn_matrix.py --write-evidence`). The text is written by code from these figures."""
    runs: int = Field(description="Wears of `wear_minutes_min` minutes or more that were run")
    warned: int = Field(description="Of those, the wears the warning came for before the pump stopped")
    lead_min_s: float
    lead_median_s: float
    lead_max_s: float
    risk_at_warning_min: float = Field(description="The risk score at the moment the warning opened, lowest over the runs")
    risk_at_warning_max: float
    wear_minutes_min: float
    wear_minutes_max: float
    fast_runs: int = Field(description="Wears shorter than `wear_minutes_min` (3 minutes), which the warning is too slow for")
    fast_warned: int
    clean_hours: float = Field(description="Simulated hours without any fault in which no warning was raised")
    false_warnings: int
    measured_on: str
    text: str
    text_ar: str


class EarlyWarning(Model):
    """A condition signal drifting away from its peers, raised while the asset is still delivering.

    The risk model is trained on a synthetic hazard, so `risk` is a score read against that history, not the chance of failing within a stated time.
    `drivers` says why the warning opened and `evidence` what the test runs showed about waiting."""
    id: str
    asset_id: str
    asset_name: str
    network: Network
    zone_id: str
    state: Literal["active", "cleared", "failed"] = Field(description="failed: the asset then stopped and an incident followed")
    created_at: datetime
    updated_at: datetime
    risk: float
    peer_risk: float
    excess: float = Field(description="risk - peer_risk")
    rise_per_minute: float | None = Field(default=None, description="Change of the excess per minute over the last two minutes")
    signals: list[WarningSignal]
    series: list[RiskPoint]
    cost: WarningCost
    text: str
    drivers: list[Reason] = Field(default=[], description="Why it opened, largest first: how many points of the gap each condition signal explains, and what age and load alone give")
    evidence: WarningEvidence | None = Field(default=None, description="What the twin's test runs showed about waiting; absent when the measurement file is missing")
    lead_time_seconds: float | None = Field(default=None, description="Set when it failed: seconds between the warning and the failure alarm")
    why_ref: str


class WhyRecord(Model):
    """Everything behind one number on screen: formula, inputs with their source, and what can be changed."""
    ref: str
    title: str
    value: float | None = None
    unit: str = ""
    evidence: Literal["measured", "calculated", "model", "assumed", "forecast"]
    formula: str
    steps: list[str] = []
    inputs: list[CalcInput] = []
    notes: list[str] = []
    links: list[dict[str, str]] = Field(default=[], description="[{label, href}] to the page where this number can be explored or changed")
    computed_at: datetime


class GuardrailRequest(Model):
    prompt: str = Field(min_length=1, max_length=600)
    incident_id: str | None = Field(default=None, description="The incident whose calculation the answer must agree with; defaults to the latest")


class GuardrailFigure(Model):
    text: str
    value: float
    verdict: Literal["verified", "removed"]
    matched: str | None = Field(default=None, description="Name of the calculated value it matches, when verified")
    matched_value: float | None = None


class GuardrailResult(Model):
    prompt: str
    incident_id: str
    calculation_id: str
    source: Literal["llm", "planted"] = Field(description="planted: no model was available, so a draft with invented figures was used to show the check")
    raw: str
    checked: str
    figures: list[GuardrailFigure]
    removed: int
    allowed_count: int
