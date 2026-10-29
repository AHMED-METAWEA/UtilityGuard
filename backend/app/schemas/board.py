"""Board of agents: the messages of a meeting and the decision it reaches."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import Model

Agent = Literal["water", "electricity", "cooling", "orchestrator"]


class BoardFact(Model):
    label: str
    value: float
    unit: str
    source: Literal["measured", "calculated", "assumed"]


class BoardMessage(Model):
    id: str
    session_id: str
    round: int = Field(description="0 opening, 1 positions, 2 prices of waiting, 3 decision, 4 replies")
    agent: Agent
    kind: Literal["opening", "position", "dependent", "challenge", "decision", "concession"]
    text: str
    facts: list[BoardFact]
    source: Literal["llm", "template"] = Field(description="template: the wording was written by code, because the model was unavailable or quoted a figure that was not in the facts")


class BoardTask(Model):
    id: str
    agent: Literal["water", "electricity", "cooling"]
    asset_id: str
    asset_name: str
    kind: str
    action: str
    crew: str
    hours_to_fix: float
    hourly_cost: float = Field(description="EGP per hour the problem stays open")
    repair_cost: float
    return_per_crew_hour: float = Field(description="hourly_cost / hours_to_fix: the EGP per hour a crew hour buys back")
    relieved_by: list[str] = []


class BoardStep(Model):
    order: int
    task_id: str
    agent: Literal["water", "electricity", "cooling"]
    asset_name: str
    action: str
    crew_slot: int
    start_hour: float
    end_hour: float
    hourly_cost: float
    loss_until_fixed: float
    repair_cost: float
    repair_multiplier: float
    dropped: bool = False
    relief_hour: float | None = None


class BoardAlternative(Model):
    id: str
    name: str
    order: list[str]
    expected_cost: float
    extra_cost: float = Field(description="Expected cost minus the chosen plan's, in EGP")


class BoardFlip(Model):
    task_id: str
    asset_name: str
    current: float
    threshold: float
    direction: Literal["below", "above"]


class BoardRisk(Model):
    kind: Literal["overload", "queue", "tail"]
    text: str


class BoardPlan(Model):
    steps: list[BoardStep]
    expected_cost: float
    p10: float
    p50: float
    p90: float
    win_share: float = Field(ge=0, le=1)
    regret_mean: float
    regret_p90: float
    hourly_bleed: float
    runs: int


class BoardDecision(Model):
    session_id: str
    incident_ids: list[str]
    created_at: datetime
    crews: int
    tasks: list[BoardTask]
    plan: BoardPlan
    alternatives: list[BoardAlternative]
    flips: list[BoardFlip]
    risks: list[BoardRisk]
    extra_crew_saving: float | None = Field(default=None, description="EGP saved by adding one crew, if it helps")
    conflict: bool = Field(description="False when there are enough crews to start everything at once")
    summary: str
    summary_source: Literal["llm", "template"]
    calculation_id: str | None = None


class BoardSession(Model):
    id: str
    incident_id: str | None = None
    state: Literal["running", "done"]
    started_at: datetime
    messages: list[BoardMessage]
    decision: BoardDecision | None = None


class BoardState(Model):
    session_id: str
    incident_id: str | None = None
    state: Literal["running", "done"]
    started_at: datetime


class ConveneRequest(Model):
    incident_id: str | None = None
