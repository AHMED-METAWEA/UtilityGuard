"""The agents, the detector, the board and the report in Arabic: every Arabic sentence carries the same figures as its English twin, nothing is left in English by accident, and
the language is a setting that the person chooses."""

import asyncio
import re
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app.agents.checker import check
from app.agents.figures import FigureSet
from app.board import session as bs
from app.board.analysis import analyse
from app.board.plan import tasks_from_alerts
from app.board.session_ar import compose_ar
from app.cost import Assumptions
from app.detection import Detector
from app.main import app
from app.report_ar import assumption_name_ar, calc_name_ar, wrap
from app.runtime import Runtime
from app.scenarios import events_for
from tests.test_board import T0, three_failures

ARABIC = re.compile(r"[؀-ۿ]")
NUM = re.compile(r"\d+(?:\.\d+)?")
CODE = re.compile(r"\b[A-Za-z]{1,4}-[A-Za-z0-9-]+\b|m3|م³")  # SS-02, WP-GOV-01 and the 3 of m3 are not figures of the sentence
# what may stay in Latin letters in an Arabic sentence: codes painted on the equipment, ids of records, a time standard
LATIN_OK = re.compile(r"\b(?:P\d{2}|[A-Z]{1,4}-\d{1,2}|[A-Z]{2,4}(?:-[A-Z0-9]+)+|calc-[\w-]+|inc-\d+|alert-\d+|UTC|[A-Z])\b")


def numbers(text: str) -> list[float]:
    return sorted(float(x) for x in NUM.findall(CODE.sub(" ", text)))


def latin_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z\-_0-9]*", LATIN_OK.sub(" ", text)) if len(w) > 1]


@pytest.fixture(autouse=True)
def default_language():
    i18n.settings.agent_lang = "en"
    yield
    i18n.settings.agent_lang = "en"


# ---------------------------------------------------------------- helpers
def test_the_helpers_follow_arabic_grammar_and_keep_codes_and_numbers():
    assert [i18n.ar_hours(x) for x in (1, 2, 4, 12, 0.5)] == ["ساعة واحدة", "ساعتان", "4 ساعات", "12 ساعة", "0.5 ساعة"]
    assert i18n.asset_ar("SS-02") == "المحطة SS-02" and i18n.asset_ar("Main WP-CBD-03") == "الخط الرئيسي WP-CBD-03" and i18n.asset_ar("P-04") == "المضخة P-04"
    assert i18n.unit_ar("22,508 EGP and 3.0 MW") == "22,508 جنيه and 3.0 ميجاوات"
    assert i18n.unit_text_ar("EGP/h") == "جنيه/ساعة" and i18n.unit_text_ar("EGP/customer-h") == "جنيه/عميل-ساعة" and i18n.num_text(23650.4) == "23,650" and i18n.num_text(0.15) == "0.15"
    f = FigureSet()
    f.add("act_now", 22508, "22,508 EGP", "x")
    assert f.render("التكلفة {{act_now}} جنيه", ar=True)[0] == "التكلفة 22,508 جنيه" and f.render("cost {{act_now}} EGP")[0] == "cost 22,508 EGP"


def test_the_settings_are_kept_in_a_file_and_only_two_languages_are_allowed(tmp_path):
    s = i18n.Settings(tmp_path / "s.json")
    assert s.agent_lang == "en"
    s.set_agent_lang("ar")
    assert i18n.Settings(tmp_path / "s.json").agent_lang == "ar"
    with pytest.raises(ValueError):
        s.set_agent_lang("fr")
    (tmp_path / "bad.json").write_text("{not json")
    assert i18n.Settings(tmp_path / "bad.json").agent_lang == "en"


def test_the_names_the_report_translates_are_the_cost_engines_own_keys():
    from app.cost import DEFAULTS

    untranslated = [k for k, v in DEFAULTS.items() if assumption_name_ar(k, v["name"]) == v["name"]]
    # what stays English is listed here on purpose: the cost model's own provenance sentences are not translated, and these are the few names without an Arabic form
    assert set(untranslated) <= {"maintenance_leak_m3h", "maintenance_unserved_fraction", "maintenance_cooling_fraction", "maintenance_cooling_fallback_mw", "pm_target_pressure",
                                 "pm_n1", "pm_invest", "reset_delta_c", "reset_pct_per_c", "reset_invest", "clean_invest", "setback_reduction", "setback_invest", "discount_rate",
                                 "cooling_price_unused"}, untranslated
    assert calc_name_ar("Energy not served, zone-cult") == "طاقة غير مخدومة، مدينة الفنون والثقافة" and calc_name_ar("Delayed cost: Water loss") == "تكلفة التأخير: فقد المياه"


def test_a_line_is_cut_before_it_is_shaped_so_that_no_line_is_wider_than_the_page():
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from app.report_ar import REG, _fonts

    _fonts()
    text = "جهد المحطة SS-02 هبط من 21.7 إلى 0.6 كيلوفولت، وتوقفت 2 مضخة تغذيها المحطة وضغطها يهبط، والأحمال الحرجة بقيت على المحطة الاحتياطية " * 3
    lines = wrap(text, REG, 10.5, 300)
    assert len(lines) > 3 and all(stringWidth(x, REG, 10.5) <= 300 for x in lines)


# ---------------------------------------------------------------- the detector
def detections(scenario: str, params: dict | None = None, seconds: int = 260):
    rt = Runtime(42)
    det = Detector(rt)
    rt.tick_hooks.append(det.tick)

    async def go():
        for i in range(30):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))
        for ev in events_for(scenario, params or {}, "run-ar"):
            rt.add_event(ev)
        for i in range(30, 30 + seconds // 2):
            await rt.tick_once(T0 + timedelta(seconds=2 * i))

    asyncio.run(go())
    return rt, det


@pytest.mark.parametrize("scenario,params", [("scn-pipe-burst", {}), ("scn-hidden-leak", {}), ("scn-pump-failure", {}), ("scn-substation-trip", {}), ("scn-cooling-fault", {}),
                                             ("scn-stuck-sensor", {}), ("scn-heat-wave", {"temperature": 47})])
def test_every_reason_and_title_the_detector_writes_has_an_arabic_twin_with_the_same_figures(scenario, params):
    rt, det = detections(scenario, params, 400 if scenario == "scn-hidden-leak" else 260)
    opened = list(det.state.open.values())
    assert opened, f"{scenario} opened nothing"
    for o in opened:
        a = rt.alerts[o.alert.id]
        assert a.title_ar and ARABIC.search(a.title_ar) and a.summary_ar and ARABIC.search(a.summary_ar), a.title
        assert numbers(a.summary_ar) == numbers(a.summary), (a.summary, a.summary_ar)
        assert not latin_words(a.title_ar + " " + a.summary_ar), (a.title_ar, a.summary_ar)
        for r in o.facts.reasons:
            assert r.text_ar and ARABIC.search(r.text_ar), r.text
            assert numbers(r.text_ar) == numbers(r.text), (r.text, r.text_ar)
            assert not latin_words(r.text_ar), r.text_ar
    for inc in rt.incidents.values():
        assert inc.title_ar and ARABIC.search(inc.title_ar)


# ---------------------------------------------------------------- the board
@pytest.fixture(scope="module")
def compound():
    rt, det = three_failures()
    A = Assumptions(path=Path("/nonexistent/assumptions.json"))
    tasks = tasks_from_alerts(rt, det, A)
    return analyse(tasks, A, "board-ar")


def test_the_arabic_meeting_has_the_same_speakers_rounds_and_figures_as_the_english_one(compound):
    en, ar = bs.compose(compound), compose_ar(compound, bs.fact, bs.Draft)
    assert [(d.round, d.agent, d.kind) for d in en] == [(d.round, d.agent, d.kind) for d in ar]
    for e, a in zip(en, ar):
        assert a.lang == "ar" and ARABIC.search(a.template)
        assert bs.same_figures(a.template, e.template), (e.template, a.template)
        assert bs.numbers_ok(a.template, a.facts) and bs.assets_ok(a.template, a.names)
        assert [(f.value, f.source) for f in a.facts] == [(f.value, f.source) for f in e.facts]  # the facts are the same numbers
        assert not latin_words(a.template), a.template
        assert not latin_words(" ".join(f.label for f in a.facts if "WP-" not in f.label and "SS-" not in f.label and "DCP-" not in f.label)), [f.label for f in a.facts]


def test_the_arabic_summary_and_its_rewrite_are_held_to_the_same_rule(compound):
    from app.board.session import summary_draft
    from app.board.session_ar import summary_draft_ar

    e, a = summary_draft(compound), summary_draft_ar(compound, bs.fact, bs.Draft)
    assert bs.same_figures(a.template, e.template) and not latin_words(a.template)
    text, source = asyncio.run(bs.voice(a, "s", 0))  # offline: the model is out of reach, so the computed Arabic sentence is what is said
    assert source == "template" and text == a.template


# ---------------------------------------------------------------- the whole app
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_for(fn, seconds=90):
    for _ in range(seconds):
        v = fn()
        if v:
            return v
        time.sleep(1)
    raise AssertionError("timed out")


def chat(c: TestClient, message: str, lang: str | None) -> str:
    with c.websocket_connect("/ws/stream") as ws:
        body = {"message": message, **({"lang": lang} if lang else {})}
        c.post("/api/v1/chat", json=body)
        text = ""
        for _ in range(600):
            m = ws.receive_json()
            if m.get("type") == "chat_chunk":
                text += m["payload"]["text"]
            if m.get("type") == "chat_done":
                return text.strip()
    raise AssertionError("no answer")


def test_the_language_is_a_setting_and_a_bad_one_is_refused(client):
    assert client.get("/api/v1/settings").json()["agentLang"] == "en"
    assert client.put("/api/v1/settings", json={"agentLang": "ar"}).json()["agentLang"] == "ar"
    assert client.get("/api/v1/settings").json()["agentLang"] == "ar"
    assert client.put("/api/v1/settings", json={"agentLang": "fr"}).status_code == 422
    client.put("/api/v1/settings", json={"agentLang": "en"})


def test_a_finding_comes_in_both_languages_and_the_arabic_is_clean(client):
    client.post("/api/v1/scenarios/reset")
    time.sleep(2)
    client.post("/api/v1/scenarios/run", json={"scenarioId": "scn-substation-trip", "parameters": {}})
    findings = wait_for(lambda: client.get("/api/v1/findings").json())
    time.sleep(1)
    for f in client.get("/api/v1/findings").json():
        ar = f["ar"]
        parts = [ar["headline"], ar["explanation"], ar["impact"], ar["action"], *ar["probableCauses"], *ar["reasoningSteps"]]
        assert all(ARABIC.search(p) for p in parts), parts
        assert not [w for p in parts for w in latin_words(p)], parts
        assert "[figure removed]" not in " ".join(parts) and "{{" not in " ".join(parts)
        # every figure the Arabic text carries is a figure of the calculation or of the data the agent read (the same check the English text passes)
        c = f["cost"]
        allowed = {abs(x) for x in (c["actNow"], c["delayPerHour"], c["expectedSaving"], c["costOfDelay"], c["delayHours"])}
        for p in (ar["explanation"], ar["impact"]):
            _, removed = check(p, allowed | {abs(float(n)) for r in f["explain"]["reasons"] for n in NUM.findall(r["text"])} | {abs(float(n)) for r in f["explain"]["reasons"] for n in NUM.findall(r.get("textAr", ""))})
            assert not removed, (p, removed)
        assert numbers(ar["explanation"]) and "جنيه" in ar["explanation"]
    inc = client.get("/api/v1/incidents").json()[0]
    assert inc["titleAr"] and ARABIC.search(inc["titleAr"]) and findings


def test_the_chat_answers_in_the_chosen_language_and_the_hours_asked_about_survive(client):
    client.post("/api/v1/scenarios/reset")
    time.sleep(2)
    client.post("/api/v1/scenarios/run", json={"scenarioId": "scn-pipe-burst", "parameters": {"severity": 0.7}})
    wait_for(lambda: client.get("/api/v1/findings").json())
    time.sleep(2)
    ar = chat(client, "what is happening?", "ar")
    en = chat(client, "what is happening?", "en")
    assert ARABIC.search(ar) and not ARABIC.search(en) and "[figure removed]" not in ar + en
    for q in ("لو استنينا 12 ساعة يحصل ايه؟", "what if we wait 12 hours?"):  # a horizon above ten hours used to lose its own figure
        a12 = chat(client, q, "ar")
        assert "[figure removed]" not in a12 and "12" in a12 and "ساعة" in a12, a12
    assert "[figure removed]" not in chat(client, "what if we wait 24 hours?", "en")
    client.put("/api/v1/settings", json={"agentLang": "ar"})  # without `lang` the chosen language answers, whatever language the question is in
    assert ARABIC.search(chat(client, "what is happening?", None))
    client.put("/api/v1/settings", json={"agentLang": "en"})


def test_the_report_is_made_in_the_language_asked_and_the_font_is_in_the_repository(client):
    assert (Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Amiri-Regular.ttf").exists()
    client.post("/api/v1/scenarios/reset")
    time.sleep(2)
    client.post("/api/v1/scenarios/run", json={"scenarioId": "scn-pipe-burst", "parameters": {"severity": 0.7}})
    wait_for(lambda: client.get("/api/v1/findings").json())
    inc = client.get("/api/v1/incidents").json()[0]["id"]
    ar = client.get(f"/api/v1/incidents/{inc}/report", params={"lang": "ar"})
    en = client.get(f"/api/v1/incidents/{inc}/report", params={"lang": "en"})
    assert ar.status_code == en.status_code == 200 and ar.content[:5] == en.content[:5] == b"%PDF-"
    assert ar.headers["content-disposition"].endswith('-ar.pdf"') and en.headers["content-disposition"].endswith('-en.pdf"')
    assert b"Amiri" in ar.content and b"Amiri" not in en.content  # the Arabic report embeds the Arabic font, the English one does not
    assert client.get(f"/api/v1/incidents/{inc}/report", params={"lang": "fr"}).status_code == 422
    client.put("/api/v1/settings", json={"agentLang": "ar"})  # no `lang`: the language of the agents
    assert b"Amiri" in client.get(f"/api/v1/incidents/{inc}/report").content
    client.put("/api/v1/settings", json={"agentLang": "en"})


def test_the_arabic_telegram_message_is_arabic_all_through(client):
    from app.main import alerts_service, runtime

    client.post("/api/v1/scenarios/reset")
    time.sleep(2)
    client.post("/api/v1/scenarios/run", json={"scenarioId": "scn-substation-trip", "parameters": {}})
    wait_for(lambda: client.get("/api/v1/findings").json())
    inc = next(i for i in runtime.incidents.values())
    f = alerts_service._finding(inc)
    ar = alerts_service.incident_message(inc, f, "incident", "ar")
    en = alerts_service.incident_message(inc, f, "incident", "en")
    assert ARABIC.search(ar) and not latin_words(ar), latin_words(ar)
    assert f.ar.headline in ar and (inc.title_ar or "") in ar and f.ar.action in ar
    assert not ARABIC.search(en) and f.headline in en
