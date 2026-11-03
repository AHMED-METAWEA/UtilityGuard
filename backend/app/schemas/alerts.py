"""Alerts sent to Telegram when a problem opens, grows, is resolved, or an early warning is raised (contract 4.24)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import Model

AlertKind = Literal["incident", "updated", "escalated", "resolved", "warning", "warning_failed", "test", "paused"]


class AlertDelivery(Model):
    id: int
    kind: AlertKind
    subject_id: str = Field(description="The incident or early warning the message is about")
    title: str
    state: Literal["sent", "failed", "skipped", "suppressed"] = Field(description="skipped: Telegram is not configured. suppressed: over the hourly limit, held back")
    at: datetime
    detail: str
    characters: int = 0


class AlertsStatus(Model):
    configured: bool
    enabled: bool
    lang: Literal["en", "ar"]
    min_severity: Literal["info", "warning", "critical"]
    warnings: bool = Field(description="Early warnings are sent too")
    per_hour: int
    deliveries: list[AlertDelivery]
