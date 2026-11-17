"""The morning briefing: what needs the owner's attention today, and where it was sent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import Model

Lang = Literal["en", "ar"]


class BriefingFigure(Model):
    key: str
    label: str
    display: str = Field(description="How the figure reads in the text, with its unit")
    value: float


class BriefingItem(Model):
    kind: Literal["decision", "incident", "warning", "outlook", "money", "data", "repeat"]
    severity: Literal["critical", "warning", "info"]
    text: str
    fingerprint: str = Field(description="Names the thing, never its value, so the same problem is recognised tomorrow at a different number")
    link: str | None = Field(default=None, description="Where in the app this can be opened, e.g. #/incidents/inc-0002")


class BriefingSection(Model):
    id: Literal["decision", "now", "watch", "outlook", "money"]
    title: str
    items: list[BriefingItem]


class BriefingDelivery(Model):
    channel: Literal["telegram", "in_app"]
    state: Literal["sent", "failed", "skipped"]
    detail: str
    at: datetime


class Briefing(Model):
    generated_at: datetime
    lang: Lang
    headline: str
    sections: list[BriefingSection]
    text: str = Field(description="The whole briefing as plain text, the form a messaging channel receives")
    figures: list[BriefingFigure] = Field(description="Every figure in the text, each the same value the dashboard shows")
    repeated: int = Field(default=0, description="Items left out of the sent text because the same problem was reported within the cooldown")
    deliveries: list[BriefingDelivery] = []


class BriefingChannel(Model):
    channel: Literal["telegram", "in_app"]
    configured: bool
    detail: str


class BriefingSendRequest(Model):
    lang: Lang = "ar"
    channel: Literal["telegram", "in_app"] = "telegram"


class BriefingSendResult(Model):
    delivery: BriefingDelivery
    briefing: Briefing
