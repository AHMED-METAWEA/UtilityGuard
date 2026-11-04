"""The morning briefing: composed by code, every figure traced, repeats counted not repeated, delivery that fails quietly."""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.checker import check
from app.briefing import Fmt, MONTHS_AR, BriefingState
from app.main import app, briefing_service
from app.schemas.briefing import Briefing


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """A test never reads or writes the real history of what the briefing reported, and the daily loop never sends anything on its own."""
    monkeypatch.setattr(briefing_service, "state", BriefingState(tmp_path / "state.json"))
    monkeypatch.setenv("BRIEFING_ENABLED", "0")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        time.sleep(25)  # the twin has to tick before the model scores the assets and the history has an hour in it
        yield c


def burst(client):
    r = client.post("/api/v1/scenarios/run", json={"scenarioId": "scn-pipe-burst", "parameters": {"severity": 0.7}})
    assert r.status_code == 200
    for _ in range(60):  # the agent needs a few seconds to price it
        incs = client.get("/api/v1/incidents").json()
        findings = client.get("/api/v1/findings").json()
        if incs and findings:
            return incs, findings
        time.sleep(1)
    raise AssertionError("no incident with a finding")


def allowed_for(b: Briefing) -> set[float]:
    date = b.generated_at.astimezone(__import__("app.sim.engine", fromlist=["CAIRO"]).CAIRO)
    return {f.value for f in b.figures} | {float(date.day), float(date.year), 24.0}  # the date, and the "24 hours" in a section title


def test_a_quiet_twin_says_so_in_both_languages(client):
    client.post("/api/v1/scenarios/reset")
    time.sleep(3)
    for lang, phrase in (("en", "No open incidents"), ("ar", "لا توجد حوادث مفتوحة")):
        b = Briefing.model_validate(client.get(f"/api/v1/briefing?lang={lang}").json())
        assert phrase in b.text and b.lang == lang
        assert any(s.id == "outlook" for s in b.sections)  # the next day is worth saying even when nothing is wrong


def test_an_incident_is_reported_with_its_calculated_costs_in_arabic_and_english(client):
    incs, findings = burst(client)
    f = findings[-1]
    act_now, delay = round(f["cost"]["actNow"]), round(f["cost"]["delayPerHour"])
    en = Briefing.model_validate(client.get("/api/v1/briefing?lang=en").json())
    ar = Briefing.model_validate(client.get("/api/v1/briefing?lang=ar").json())
    assert "Pipe burst" in en.text and f"{act_now:,} EGP" in en.text and f"{delay:,} EGP" in en.text
    assert "تسريب أو كسر في ماسورة" in ar.text and f"{act_now:,} جنيه" in ar.text
    for b in (en, ar):
        assert "{{" not in b.text and "[figure removed]" not in b.text
        # every number in the text, in either language, is a registered figure, a date part, or a clock time and an asset name
        cleaned, removed = check(b.text, allowed_for(b))
        assert removed == [], (b.lang, removed)
    assert any(s.id == "now" and s.items[0].link and s.items[0].link.startswith("#/incidents/") for s in ar.sections)
    # the same figures, whichever language reads them. The twin is live: a forecast peak can move in the last digits when a tick lands between the two requests
    en_figs, ar_figs = {f.key: f.value for f in en.figures}, {f.key: f.value for f in ar.figures}
    assert en_figs.keys() == ar_figs.keys()
    assert all(ar_figs[k] == pytest.approx(v, rel=1e-3, abs=1e-9) for k, v in en_figs.items())


def test_the_arabic_text_has_arabic_month_names_and_no_english_month(client):
    ar = Briefing.model_validate(client.get("/api/v1/briefing?lang=ar").json())
    assert any(m in ar.headline for m in MONTHS_AR)
    assert not re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b", ar.headline)


def test_a_decision_waiting_for_a_person_comes_first(client):
    client.post("/api/v1/scenarios/reset")
    time.sleep(2)
    client.post("/api/v1/scenarios/run", json={"scenarioId": "scn-compound", "parameters": {}})
    for _ in range(150):  # the board meets by itself once the set of open problems stops changing
        r = client.get("/api/v1/board/latest")
        if r.status_code == 200 and r.json().get("decision"):
            break
        time.sleep(1)
    else:
        pytest.skip("the board did not meet in time")
    b = Briefing.model_validate(client.get("/api/v1/briefing?lang=en").json())
    assert b.sections[0].id == "decision" and "waiting for your approval" in b.sections[0].items[0].text
    ar = Briefing.model_validate(client.get("/api/v1/briefing?lang=ar").json())
    assert ar.sections[0].id == "decision" and "ينتظر موافقتك" in ar.sections[0].items[0].text


def test_repeats_are_counted_after_a_send_and_a_worse_problem_is_news_again(client, tmp_path):
    client.post("/api/v1/scenarios/reset")
    time.sleep(2)
    burst(client)
    state = BriefingState(tmp_path / "state.json")
    svc = briefing_service
    old, svc.state = svc.state, state
    try:
        now = datetime.now(timezone.utc)
        first = svc.compose("en", suppress_repeats=True, now=now)
        assert first.repeated == 0
        state.mark([it for s in first.sections for it in s.items], now)
        again = svc.compose("en", suppress_repeats=True, now=now + timedelta(hours=1))
        assert again.repeated >= 1 and "already reported and still open" in again.text
        assert not any("Pipe burst" in it.text for s in again.sections for it in s.items if it.kind == "incident")
        later = svc.compose("en", suppress_repeats=True, now=now + timedelta(hours=13))  # past the cooldown: reported again
        assert later.repeated == 0 and "Pipe burst" in later.text
        assert BriefingState(tmp_path / "state.json").sent  # kept in a file, so a restart does not repeat the morning
    finally:
        svc.state = old


def test_telegram_is_skipped_when_not_configured_and_sent_when_it_is(client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    r = client.post("/api/v1/briefing/send", json={"lang": "ar", "channel": "telegram"}).json()
    assert r["delivery"]["state"] == "skipped" and "TELEGRAM_BOT_TOKEN" in r["delivery"]["detail"]
    assert [c for c in client.get("/api/v1/briefing/channels").json() if c["channel"] == "telegram"][0]["configured"] is False

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    d, b = asyncio.run(briefing_service.send("ar", "telegram", transport=httpx.MockTransport(handler)))
    assert d.state == "sent" and seen["body"]["chat_id"] == "42" and seen["body"]["text"] == b.text and len(seen["body"]["text"]) <= 4096
    assert "secret-token" in seen["url"]  # it is the request that carries the token ...
    assert "secret-token" not in d.detail and "secret-token" not in b.model_dump_json()  # ... and nothing we return or store does


def test_a_telegram_failure_never_leaks_the_token_or_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect to https://api.telegram.org/bot123:secret-token/sendMessage")

    d, _ = asyncio.run(briefing_service.send("en", "telegram", transport=httpx.MockTransport(boom)))
    assert d.state == "failed" and "secret-token" not in d.detail
    refused = lambda r: httpx.Response(400, json={"ok": False, "description": "chat not found"})  # noqa: E731
    d, _ = asyncio.run(briefing_service.send("en", "telegram", transport=httpx.MockTransport(refused)))
    assert d.state == "failed" and "chat not found" in d.detail


def test_a_language_the_briefing_does_not_have_is_a_clean_error(client):
    r = client.get("/api/v1/briefing?lang=fr")
    assert r.status_code == 422 and r.json()["code"] == "bad_language"


def test_the_daily_send_is_due_once_a_day_at_the_set_time(tmp_path):
    from app.briefing import BriefingService
    svc = BriefingService.__new__(BriefingService)
    svc.state = BriefingState(tmp_path / "s.json")
    cairo = __import__("app.sim.engine", fromlist=["CAIRO"]).CAIRO
    before = datetime(2026, 11, 2, 6, 30, tzinfo=cairo)
    after = datetime(2026, 11, 2, 7, 5, tzinfo=cairo)
    assert not svc.due(before) and svc.due(after)
    svc.state.last_date = "2026-11-02"
    assert not svc.due(after) and svc.due(after + timedelta(days=1))


def test_formats_carry_their_unit_in_each_language():
    en, ar = Fmt("en"), Fmt("ar")
    assert en.money(36020.4) == "36,020 EGP" and ar.money(36020.4) == "36,020 جنيه"
    assert en.mw(184.94) == "184.9 MW" and ar.mw(184.94) == "184.9 ميجاوات"
    assert en.flow(3787.2) == "3,787 m3/h" and ar.flow(3787.2) == "3,787 م³/ساعة"
