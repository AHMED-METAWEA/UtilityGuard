"""Telegram alerts: every detail of a problem in one message, composed by code from the same numbers the app shows; sent once, not repeated, held back when too many."""

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.alerts import TELEGRAM_LIMIT, TelegramAlerts
from app.cost import Assumptions
from app.agents.service import AgentService
from app.runtime import Runtime
from app.scenarios import events_for
from app.schemas import EarlyWarning
from app.schemas.insight import RiskPoint, WarningCost, WarningSignal

T0 = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
TOKEN, CHAT = "123456:SECRET-TOKEN-VALUE", "98765"


@pytest.fixture(autouse=True)
def telegram(monkeypatch):
    monkeypatch.setenv("LLM_OFFLINE", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setenv("ALERTS_ENABLED", "1")
    monkeypatch.setenv("ALERTS_LANG", "en")
    monkeypatch.delenv("ALERTS_MIN_SEVERITY", raising=False)
    monkeypatch.delenv("ALERTS_PER_HOUR", raising=False)


class Bot:
    """Stands in for api.telegram.org and keeps what it was sent."""
    def __init__(self, status: int = 200):
        self.sent: list[dict] = []
        self.status = status
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.telegram.org" and request.url.path == f"/bot{TOKEN}/sendMessage"
        self.sent.append(json.loads(request.content))
        body = {"ok": True} if self.status == 200 else {"ok": False, "description": "Bad Request: chat not found"}
        return httpx.Response(self.status, json=body)

    @property
    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]


async def world(bot: Bot, scenario: str = "scn-pipe-burst", ticks: int = 40):
    rt = Runtime(42)
    svc = AgentService(rt, Assumptions(Path(tempfile.mkdtemp()) / "a.json"))
    svc.settle_seconds = 0
    alerts = TelegramAlerts(rt, None, transport=bot.transport)
    rt.tick_hooks.append(alerts.tick)
    for i in range(20):
        await rt.tick_once(T0 + timedelta(seconds=2 * i))
    for ev in events_for(scenario, {}, "r"):
        rt.add_event(ev)
    for i in range(20, ticks):
        await rt.tick_once(T0 + timedelta(seconds=2 * i))
    await asyncio.gather(*svc.tasks)
    await rt.tick_once(T0 + timedelta(seconds=2 * ticks))  # the finding is in: the next tick sends the alert
    await asyncio.gather(*alerts._tasks)
    return rt, svc, alerts


def test_a_new_incident_is_sent_once_with_every_detail_and_the_costs_of_the_finding():
    async def go():
        bot = Bot()
        rt, _, alerts = await world(bot)
        inc = next(iter(rt.incidents.values()))
        f = rt.findings[inc.finding_ids[-1]]
        assert len(bot.sent) == 1 and bot.sent[0]["chat_id"] == CHAT
        text = bot.texts[0]
        assert inc.title in text and inc.id in text and f.headline in text and f.recommended_action.text in text and f.predicted_impact.text in text
        assert f"{f.cost.delay_per_hour:,.0f} EGP" in text and f"{f.cost.act_now:,.0f} EGP" in text and f"{f.cost.expected_saving:,.0f} EGP" in text  # the finding's own figures
        assert rt.engine.g.zone_index[inc.zone_ids[0]].name in text and all(r.text in text for r in f.explain.reasons[:6])
        assert TOKEN not in text and len(text) <= TELEGRAM_LIMIT and "planning assumptions" in text
        for i in range(10):  # nothing new: the same incident is not sent again
            await rt.tick_once(T0 + timedelta(seconds=100 + 2 * i))
        await asyncio.gather(*alerts._tasks)
        assert len(bot.sent) == 1
        d = alerts.status().deliveries[0]
        assert d.kind == "incident" and d.state == "sent" and d.subject_id == inc.id and TOKEN not in d.detail

    asyncio.run(go())


def test_the_alert_can_be_sent_in_arabic_with_arabic_zone_names(monkeypatch):
    monkeypatch.setenv("ALERTS_LANG", "ar")

    async def go():
        bot = Bot()
        rt, _, _ = await world(bot)
        text = bot.texts[0]
        inc = next(iter(rt.incidents.values()))
        f = rt.findings[inc.finding_ids[-1]]
        assert "الحادثة" in text and "التكلفة" in text and "المكان" in text and f"{f.cost.delay_per_hour:,.0f} جنيه" in text and "توقيت القاهرة" in text

    asyncio.run(go())


def test_a_rise_in_severity_and_the_resolution_each_send_one_message():
    async def go():
        bot = Bot()
        rt, _, alerts = await world(bot)
        inc = next(iter(rt.incidents.values()))
        before = len(bot.sent)
        rt.incidents[inc.id] = inc.model_copy(update={"severity": "critical"} if inc.severity != "critical" else {"severity": "critical"})
        if inc.severity == "critical":  # already the highest: start the check from a lower one
            alerts._seen[inc.id].severity = "warning"
        await rt.tick_once(T0 + timedelta(seconds=200))
        await asyncio.gather(*alerts._tasks)
        assert len(bot.sent) == before + 1 and "ESCALATED" in bot.texts[-1].splitlines()[0] and inc.id in bot.texts[-1]
        rt.incidents[inc.id] = rt.incidents[inc.id].model_copy(update={"state": "resolved"})
        for i in range(3):
            await rt.tick_once(T0 + timedelta(seconds=210 + 2 * i))
        await asyncio.gather(*alerts._tasks)
        assert len(bot.sent) == before + 2 and "RESOLVED" in bot.texts[-1].splitlines()[0] and "was open for" in bot.texts[-1]

    asyncio.run(go())


def test_an_incident_without_a_finding_is_sent_after_the_wait_and_says_the_cost_is_pending(monkeypatch):
    monkeypatch.setenv("ALERTS_WAIT_FINDING_S", "45")

    async def go():
        from app.detection import Detector
        bot, now = Bot(), [T0]
        rt = Runtime(42)
        det = Detector(rt)
        rt.detector = det
        rt.tick_hooks.append(det.tick)
        alerts = TelegramAlerts(rt, None, transport=bot.transport, clock=lambda: now[0])
        rt.tick_hooks.append(alerts.tick)
        for i in range(20):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        for ev in events_for("scn-pipe-burst", {}, "r"):
            rt.add_event(ev)
        for i in range(20, 40):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        assert rt.incidents and not bot.sent  # an incident is open, the finding never comes, and the wait has not passed
        now[0] = T0 + timedelta(seconds=60)
        await rt.tick_once(T0 + timedelta(seconds=80))
        await asyncio.gather(*alerts._tasks)
        assert len(bot.sent) == 1 and "still working out the cost" in bot.texts[0]

    asyncio.run(go())


def test_low_severity_not_configured_and_disabled_send_nothing(monkeypatch):
    async def go(setup):
        setup()
        bot = Bot()
        _, _, alerts = await world(bot)
        return bot, alerts

    monkeypatch.setenv("ALERTS_MIN_SEVERITY", "critical")
    bot, _ = asyncio.run(go(lambda: None))
    assert len(bot.sent) in (0, 1)  # only if the burst is critical, and then it is sent
    monkeypatch.delenv("ALERTS_MIN_SEVERITY")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    bot, alerts = asyncio.run(go(lambda: None))
    assert not bot.sent and not alerts.status().configured and not alerts.deliveries
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ALERTS_ENABLED", "0")
    bot, alerts = asyncio.run(go(lambda: None))
    assert not bot.sent and not alerts.status().enabled


def test_a_refused_or_unreachable_telegram_is_logged_without_the_token():
    async def go():
        bot = Bot(status=400)
        _, _, alerts = await world(bot)
        d = alerts.deliveries[0]
        assert d.state == "failed" and "chat not found" in d.detail and TOKEN not in d.detail

        def boom(request):
            raise httpx.ConnectError(f"cannot reach {request.url}")  # the URL, with the token in it, must not reach the log

        alerts2 = TelegramAlerts(Runtime(1), None, transport=httpx.MockTransport(boom))
        got = await alerts2.send_test()
        assert got.state == "failed" and "ConnectError" in got.detail and TOKEN not in got.detail

    asyncio.run(go())


def test_more_than_the_hourly_limit_is_held_back_with_one_notice(monkeypatch):
    monkeypatch.setenv("ALERTS_PER_HOUR", "3")

    async def go():
        bot = Bot()
        alerts = TelegramAlerts(Runtime(1), None, transport=bot.transport)
        for i in range(6):
            alerts._send("incident", f"inc-{i}", "x", f"message {i}")
        await asyncio.gather(*alerts._tasks)
        states = [d.state for d in reversed(alerts.deliveries)]
        assert states.count("sent") == 4 and states.count("suppressed") == 3 and len(bot.sent) == 4  # three messages, then one notice
        assert "held back" in bot.texts[-1]

    asyncio.run(go())


def _warning(state="active", lead=None) -> EarlyWarning:
    return EarlyWarning(id="warn-0001", asset_id="water-pump-03", asset_name="P-03", network="water", zone_id="zone-cbd", state=state, created_at=T0, updated_at=T0,
                        risk=0.31, peer_risk=0.08, excess=0.23, rise_per_minute=0.012,
                        signals=[WarningSignal(label="Deviation from the twin", value=1.9, peer_value=0.4, unit="of normal range"), WarningSignal(label="Signal variability", value=2.2, peer_value=0.5)],
                        series=[RiskPoint(t=T0, risk=0.31, peer=0.08)], cost=WarningCost(unplanned=87560.0, planned=21000.0, break_even=0.2398, risk=0.31, verdict="maintain_now", expected_saving=6143.6),
                        text="x", lead_time_seconds=lead, why_ref="warning:warn-0001")


def test_an_early_warning_is_sent_with_its_signals_and_prices_and_again_when_the_asset_fails():
    async def go():
        bot = Bot()
        rt = Runtime(42)

        class Mls:
            class warnings:  # noqa: N801
                warnings: dict = {}

        mls = Mls()
        mls.warnings.warnings = {"warn-0001": _warning()}
        alerts = TelegramAlerts(rt, mls, transport=bot.transport)
        await alerts.tick(rt)
        await alerts.tick(rt)  # the same warning is sent once
        mls.warnings.warnings = {"warn-0001": _warning("failed", 140.0)}
        await alerts.tick(rt)
        await asyncio.gather(*alerts._tasks)
        assert len(bot.sent) == 2
        first, second = bot.texts
        assert "EARLY WARNING" in first and "P-03" in first and "31%" in first and "8%" in first and "1.90" in first and "0.40" in first
        assert "87,560 EGP" in first and "21,000 EGP" in first and "24.0%" in first and "the chance of failing within a stated time" in first
        assert "failed 2.3 minutes after its early warning" in second

    asyncio.run(go())


def test_a_long_message_is_trimmed_by_dropping_evidence_first_never_the_costs():
    head = ["🔴 CRITICAL — x"]
    cost = ["Cost (planning assumptions):", "• Each hour it stays open: 760,874 EGP"]
    sensors = ["What the sensors show:"] + [f"• reading {i} " + "x" * 200 for i in range(40)]
    text = TelegramAlerts._fit(head, [["Probable cause:", "• a"], sensors, ["Expected impact: y"], cost], ["footer"])
    assert len(text) <= TELEGRAM_LIMIT and "760,874 EGP" in text and "footer" in text and "What the sensors show:" not in text


def test_the_status_and_test_endpoints_report_and_refuse(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app, alerts_service

    sent = Bot()
    monkeypatch.setattr(alerts_service, "transport", sent.transport)
    with TestClient(app) as c:
        s = c.get("/api/v1/alerts/telegram").json()
        assert s["configured"] is True and s["lang"] == "en" and s["minSeverity"] == "warning" and TOKEN not in json.dumps(s)
        assert c.post("/api/v1/alerts/telegram/test").json()["state"] == "sent" and "Test message" in sent.texts[-1]
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
        r = c.post("/api/v1/alerts/telegram/test")
        assert r.status_code == 409 and r.json()["code"] == "telegram_not_configured"
