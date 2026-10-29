"""The meeting: agents speak in turn, every figure they quote comes from `analysis.py`.

For each message, code writes the facts and a complete sentence in plain words. A language model may re-voice it in the
agent's own register, but its text is accepted only if every number in it is one of the facts and every asset it names is
one of the assets in play. Otherwise the code's own sentence is used. So a figure on screen is always a computed figure,
and `source` says which of the two wrote the words.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
from datetime import datetime, timezone

from app.agents.llm import MODEL_FAST, MODEL_ORCH, llm
from app.board.analysis import AGENTS, NAMES, Analysis, analyse, calculation_record, decision_object, hours, money
from app.board.audit import AuditLog, decision_hash
from app.board.dispatch import Dispatcher
from app.board.plan import tasks_from_alerts
from app.schemas import envelope
from app.schemas.board import BoardFact, BoardMessage, BoardSession, BoardState

log = logging.getLogger("utilityguard.board")

PACE = 0.9  # seconds between messages on screen
NUM = re.compile(r"(?<![A-Za-z0-9_-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)")
ASSET = re.compile(r"\b[A-Z]{1,4}-\d{1,2}\b")


def numbers_ok(text: str, facts: list[BoardFact]) -> bool:
    """Every figure in the text is a fact (to the rounding a person would use), and no small count slips through unchecked."""
    allowed = {abs(f.value) for f in facts}
    for m in NUM.finditer(text):
        if text[m.end():m.end() + 1] == ":":
            return False
        x = abs(float(m.group(1).replace(",", "")))
        if not any(abs(x - a) <= max(0.011 * a, 0.051) or (a >= 1000 and abs(x * 1000 - a) <= 0.011 * a) for a in allowed):
            return False
    return True


def assets_ok(text: str, names: set[str]) -> bool:
    allowed = set(names)
    for n in names:
        allowed |= set(ASSET.findall(n))
    return all(t in allowed for t in ASSET.findall(text))


ORD = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]
crew_letter = lambda slot: chr(65 + int(slot))  # noqa: E731  crews are named A, B, C so no stray digits appear in the wording


def fact(label: str, value: float, unit: str, source: str = "calculated") -> BoardFact:
    return BoardFact(label=label, value=round(float(value), 2), unit=unit, source=source)


class Draft:
    def __init__(self, round_: int, agent: str, kind: str, facts: list[BoardFact], template: str, brief: str, names: set[str], chair: bool = False):
        self.round, self.agent, self.kind, self.facts, self.template, self.brief, self.names, self.chair = round_, agent, kind, facts, template, brief, names, chair


SYSTEM = """You are the {who} in a board meeting of the New Administrative Capital's utilities, with agents for water, electricity and cooling.
You receive a DRAFT sentence set that is already correct. REWRITE it in the first person, like a calm senior engineer, so it sounds like you talking to colleagues.
Rules: keep EVERY number and EVERY asset name from the DRAFT exactly as written, in digits; add no number, no asset, no cause, no time and no claim that is not in the DRAFT;
keep its meaning, including who goes first and who waits; no markdown, no lists, no emoji; about the same length as the DRAFT, never more than {limit} words.
Return JSON: {{"text": str}}"""


def figures(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in NUM.finditer(text)]


def same_figures(text: str, draft: str) -> bool:
    """The rewrite carries exactly the draft's numbers: none added, none dropped."""
    close = lambda x, y: abs(x - y) <= max(0.011 * abs(y), 0.051)  # noqa: E731
    a, b = figures(text), figures(draft)
    return all(any(close(x, y) for y in b) for x in a) and all(any(close(y, x) for x in a) for y in b)


async def voice(d: Draft, session_id: str, i: int) -> tuple[str, str]:
    who = "chair (the orchestrator)" if d.chair else f"{NAMES.get(d.agent, d.agent)} agent"
    n = len(d.template.split())
    user = json.dumps({"draft": d.template, "may_name": sorted(d.names)}, ensure_ascii=False)
    key = llm.key("board2", d.agent, d.kind, d.template)
    data, fallback = await llm.json(key, SYSTEM.format(who=who, limit=int(n * 1.4) + 5), user, model=MODEL_ORCH if d.chair else MODEL_FAST, temperature=0.4)
    text = (data or {}).get("text") if isinstance(data, dict) else None
    if (isinstance(text, str) and 0.5 * n <= len(text.split()) <= 1.4 * n + 5 and same_figures(text, d.template) and numbers_ok(text, d.facts)
            and assets_ok(text, d.names) and "[" not in text):
        return text.strip(), "llm"
    if text:
        log.info("board message %s replaced by the template: the rewrite changed figures, assets or length", d.kind)
    return d.template, "template"


# ---------- composing the meeting ----------

def compose(a: Analysis) -> list[Draft]:
    T, p = a.tasks, a.params
    names = {t.asset_name for t in T}
    drafts: list[Draft] = []
    plan, steps = a.plan, sorted(a.plan.order, key=lambda i: (a.plan.start[i], i))
    owners = [g for g in AGENTS if a.agent_tasks(g)]
    bleed = float(p.rate.sum())

    # round 0: the chair opens
    quiet = [NAMES[g] for g in AGENTS if g not in owners]
    facts = [fact("Open problems", len(T), "problems"), fact("Networks involved", len(owners), "networks"), fact("Crews available", p.crews, "crews", "assumed"),
             fact("Cost per hour while all stay open", bleed, "EGP/h")]
    tmpl = (f"The board is in session. {len(T)} problem{'s' if len(T) > 1 else ''} {'are' if len(T) > 1 else 'is'} open across {len(owners)} network{'s' if len(owners) > 1 else ''}, "
            f"and {p.crews} crew{'s are' if p.crews > 1 else ' is'} available. Left open, they cost {money(bleed)} EGP every hour. "
            f"{'There are fewer crews than problems, so the order matters.' if len(T) > p.crews else 'There are enough crews to start everything at once.'}")
    drafts.append(Draft(0, "orchestrator", "opening", facts, tmpl, "Open the meeting: how many problems, networks and crews, what they cost per hour together, and whether the order of work matters.", names, True))

    # round 1: positions
    for g in owners:
        i = a.primary(g)
        t = T[i]
        mine = a.agent_tasks(g)
        own = a.own_first[g]
        facts = [fact(f"{t.asset_name} cost per hour open", p.rate[i], "EGP/h"), fact("Crew time needed", p.hours[i], "h", "assumed"),
                 fact("Return per crew hour", p.rate[i] / p.hours[i], "EGP/h"), fact("Done by hour if I go first", own.end[i], "h")]
        extra = ""
        if len(mine) > 1:
            facts.append(fact("My other open problems", len(mine) - 1, "problems"))
            extra = f" I have {len(mine) - 1} more open problem{'s' if len(mine) > 2 else ''} behind it."
        pay = a.others_pay.get(g, 0.0)
        pay_txt = ""
        if a.conflict and pay > 0.5:
            facts.append(fact("Extra cost to the others if I go first", pay, "EGP"))
            pay_txt = f" If I go first, the others together pay {money(pay)} EGP more."
        tmpl = (f"{NAMES[g]} agent. {t.asset_name}: {t.action[0].lower() + t.action[1:]}. Left open it costs {money(p.rate[i])} EGP every hour and needs {hours(p.hours[i])} h of crew time, "
                f"so each crew hour buys back {money(p.rate[i] / p.hours[i])} EGP an hour. If my work goes first it is done by hour {hours(own.end[i])}.{extra}{pay_txt}")
        drafts.append(Draft(1, g, "position", facts, tmpl,
                            "State your problem, what it costs per hour, the crew time you need and what a crew hour buys back. Say when you would be done if you went first." + (" Mention the cost to the others." if pay_txt else ""),
                            names | {t.asset_name}))
    # agents with no repair of their own but with equipment that lost power
    trips = [t for t in T if t.kind == "substation_trip"]
    for g, label, attr in (("water", "pumps", "pumps_stopped"), ("cooling", "cooling plants", "plants_affected")):
        if g in owners:
            continue
        hit = [(t, t.stake[attr]) for t in trips if t.stake.get(attr)]
        if hit:
            t, count = max(hit, key=lambda x: x[1])
            facts = [fact(f"{label.capitalize()} stopped by {t.asset_name}", count, label)]
            tmpl = (f"{NAMES[g]} agent. I have no repair of my own, but {int(count)} {label if count != 1 else label.rstrip('s')} stopped when {t.asset_name} lost power. "
                    f"Their cost is already counted in the electricity harm of that outage, so I am not asking for a crew. Restoring {t.asset_name} brings them back.")
            drafts.append(Draft(1, g, "dependent", facts, tmpl,
                                "You have no repair of your own. Say how much of your equipment stopped because of the substation, that its cost is already counted inside the electricity outage harm so you do not ask for a crew, and that restoring the substation brings it back.",
                                names | {t.asset_name}))

    # round 2: the price of waiting, or the absence of one
    if a.conflict:
        for g in owners:
            price = a.waiting_price.get(g, 0.0)
            i = a.primary(g)
            waits = plan.start[i] - p.response > 1e-9
            if waits and price > 0.5:
                facts = [fact("Extra cost to me if I wait my turn", price, "EGP"), fact(f"{T[i].asset_name} starts at hour", plan.start[i], "h")]
                tmpl = f"{NAMES[g]} agent. If I wait my turn, my work costs {money(price)} EGP more than if I went first. It would start at hour {hours(plan.start[i])}."
                drafts.append(Draft(2, g, "challenge", facts, tmpl, "", names | {T[i].asset_name}))
            elif not waits:
                facts = [fact(f"{T[i].asset_name} starts at hour", plan.start[i], "h")]
                tmpl = f"{NAMES[g]} agent. My work can start at hour {hours(plan.start[i])}, on a crew of its own, so the order costs me nothing."
                drafts.append(Draft(2, g, "challenge", facts, tmpl, "", names | {T[i].asset_name}))

    # round 3: the chair decides
    alarm = next(x for x in a.alternatives if x["id"] == "alarm")
    order_txt = "; ".join(f"{ORD[k]}: {T[i].asset_name} on crew {crew_letter(plan.slot[i])} from hour {hours(plan.start[i])} to hour {hours(plan.end[i])}" for k, i in enumerate(steps))
    facts = [fact("Cost of the chosen order", plan.cost, "EGP"), fact("Saved against working in alarm order", alarm["extra"], "EGP"),
             fact("Cheapest in this share of price scenarios", a.robust.win_share * 100, "%"), fact("Price scenarios tried", a.robust.runs, "scenarios")]
    for k, i in enumerate(steps):
        facts += [fact(f"Step {k + 1} {T[i].asset_name} starts", plan.start[i], "h"), fact(f"Step {k + 1} {T[i].asset_name} ends", plan.end[i], "h")]
    saved = f" That is {money(alarm['extra'])} EGP less than working in the order the alarms arrived." if alarm["extra"] > 0.5 else ""
    proof = (f" I tried every order, and this one is the cheapest in {round(a.robust.win_share * 100)}% of {a.robust.runs} price scenarios."
             if len(T) > 1 else " There is a single job, so there is no order to choose.")
    tmpl = f"Decision. {order_txt}. Expected cost {money(plan.cost)} EGP.{saved}{proof}"
    drafts.append(Draft(3, "orchestrator", "decision", facts, tmpl,
                        "",
                        names, True))

    # round 4: replies
    for g in owners:
        mine = a.agent_tasks(g)
        i = min(mine, key=lambda x: plan.start[x])
        t = T[i]
        wait = plan.start[i] - p.response
        fl = next((f for f in a.flips if f["task"].id == t.id), None)
        if wait > 1e-9:
            facts = [fact(f"{t.asset_name} starts at hour", plan.start[i], "h")]
            tail = ""
            if fl and fl["direction"] == "above":
                facts += [fact("My cost per hour now", fl["current"], "EGP/h"), fact("Cost per hour at which I should go first", fl["threshold"], "EGP/h")]
                tail = f" Call me back if my cost rises above {money(fl['threshold'])} EGP an hour; it is {money(fl['current'])} now."
            tmpl = f"{NAMES[g]} agent. Accepted. My work starts at hour {hours(plan.start[i])}.{tail}"
            drafts.append(Draft(4, g, "concession", facts, tmpl, "Accept the decision, say when your work starts and, if given, the hourly cost at which the board should put you first.", names | {t.asset_name}))
        else:
            facts = [fact(f"{t.asset_name} done at hour", plan.end[i], "h")]
            tmpl = f"{NAMES[g]} agent. Understood. My crew starts at once and I will hand it back at hour {hours(plan.end[i])}."
            drafts.append(Draft(4, g, "concession", facts, tmpl, "Accept, say your crew starts at once and when you hand it back.", names | {t.asset_name}))
    return drafts


def summary_draft(a: Analysis) -> Draft:
    T, p, plan = a.tasks, a.params, a.plan
    steps = sorted(plan.order, key=lambda i: (plan.start[i], i))
    alarm = next(x for x in a.alternatives if x["id"] == "alarm")
    names = {t.asset_name for t in T}
    lines = [f"{ORD[k]}: {T[i].asset_name}, {T[i].action[0].lower() + T[i].action[1:]}, crew {crew_letter(plan.slot[i])}, from hour {hours(plan.start[i])} to hour {hours(plan.end[i])}" for k, i in enumerate(steps)]
    facts = [fact("Cost of the plan", plan.cost, "EGP"), fact("Saved against alarm order", alarm["extra"], "EGP"), fact("Cost per hour of all open problems", float(p.rate.sum()), "EGP/h"),
             fact("Cost in a bad case (1 in 10)", a.robust.p90, "EGP")]
    for k, i in enumerate(steps):
        facts += [fact(f"Step {ORD[k]} {T[i].asset_name} starts", plan.start[i], "h"), fact(f"Step {ORD[k]} {T[i].asset_name} ends", plan.end[i], "h")]
    first = T[steps[0]]
    why = f"{first.asset_name} costs {money(first.rate)} EGP every hour it stays open."
    facts.append(fact(f"{first.asset_name} cost per hour", first.rate, "EGP/h"))
    risk = ""
    if a.flips:
        f = a.flips[0]
        risk = f" The order would change if {f['task'].asset_name} cost per hour were {'below' if f['direction'] == 'below' else 'above'} {money(f['threshold'])} EGP."
        facts += [fact(f"{f['task'].asset_name} flip level", f["threshold"], "EGP/h"), fact(f"{f['task'].asset_name} cost per hour now", f["current"], "EGP/h")]
    crew = ""
    if a.extra_crew_saving:
        crew = f" One more crew would save {money(a.extra_crew_saving)} EGP."
        facts.append(fact("Saved by one more crew", a.extra_crew_saving, "EGP"))
    saved = f" It saves {money(alarm['extra'])} EGP against working in the order the alarms arrived." if alarm["extra"] > 0.5 else ""
    tmpl = (f"Do this. " + "; ".join(lines) + f". Why: {why} The plan is expected to cost {money(plan.cost)} EGP.{saved} "
            f"In a bad case it costs {money(a.robust.p90)} EGP.{risk}{crew}")
    brief = ("Tell the owner what to do, in plain words: the steps in order with crew and hours, why the first one comes first, the expected cost and the saving against alarm order, "
             "what could go wrong (the bad-case cost), what would change the order, and what one more crew would save if given. Steps: " + " ".join(lines))
    return Draft(5, "orchestrator", "decision", facts, tmpl, brief, names, True)


# ---------- running a session ----------

class Board:
    def __init__(self, rt, service):
        self.rt, self.service = rt, service
        self.sessions: dict[str, BoardSession] = {}
        self._ids = itertools.count(1)
        self._msg = itertools.count(1)
        self._running: asyncio.Task | None = None
        self._last_tasks: set[str] = set()
        self._timer: asyncio.Task | None = None
        self.quiet_seconds = 20.0
        self.analyses: dict[str, Analysis] = {}
        self.audit = AuditLog()
        self.run = self._new_run()
        self.dispatch = Dispatcher(self)
        rt.reset_hooks.append(self.reset)

    @staticmethod
    def _new_run() -> str:
        return "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def log(self, **kw):
        """Write one line of the decision log and push it to the screens."""
        entry = self.audit.append(run=self.run, **kw)
        try:
            asyncio.get_running_loop().create_task(self.rt.broadcast("audit_entry", entry))
        except RuntimeError:  # no event loop (a test stepping the engine by hand)
            pass
        return entry

    def reset(self) -> None:
        if self.sessions:
            self.log(actor="system", action="simulation_reset", detail=f"The simulation was reset; {len(self.sessions)} board session(s) were closed.")
        self.sessions.clear()
        self.analyses.clear()
        self._last_tasks.clear()
        self.run = self._new_run()

    def latest(self, incident_id: str | None = None) -> BoardSession | None:
        for s in reversed(list(self.sessions.values())):
            if incident_id is None or s.incident_id == incident_id or (s.decision and incident_id in s.decision.incident_ids):
                return s
        return None

    def raw_tasks(self):
        """Every open problem, including ones crews are already working on."""
        return tasks_from_alerts(self.rt, self.service.detector, self.service.A)

    def open_tasks(self):
        """The problems still waiting for a decision: not the ones an approved plan has already sent crews to."""
        claimed = self.dispatch.claimed()
        return [t for t in self.raw_tasks() if (t.kind, t.asset_id) not in claimed]

    def convene(self, incident_id: str | None = None) -> BoardSession:
        if self._running and not self._running.done():
            running = next((s for s in reversed(list(self.sessions.values())) if s.state == "running"), None)
            if running:
                return running
        s = BoardSession(id=f"board-{next(self._ids):04d}", incident_id=incident_id, state="running", started_at=datetime.now(timezone.utc), messages=[])
        self.sessions[s.id] = s
        self._running = asyncio.create_task(self._run(s))
        return s

    def maybe_convene(self) -> None:
        """Called after each analysis. A meeting is worth having when at least two networks have their own open problem,
        and it waits until the set of problems has stopped changing for a few seconds, so it decides on the whole picture."""
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._debounced())

    async def _debounced(self) -> None:
        await asyncio.sleep(self.quiet_seconds)
        while self._running and not self._running.done():  # a meeting is in progress: wait for it, then look at what changed meanwhile
            await asyncio.wait([self._running])
            await asyncio.sleep(2.0)
        tasks = self.open_tasks()
        if len({t.agent for t in tasks}) < 2:
            return
        ids = {t.id for t in tasks}
        if ids <= self._last_tasks:
            return
        self._last_tasks |= ids
        self.convene(tasks[0].incident_id)

    async def _send(self, s: BoardSession, d: Draft, text: str, source: str) -> None:
        m = BoardMessage(id=f"bmsg-{next(self._msg):04d}", session_id=s.id, round=d.round, agent=d.agent, kind=d.kind, text=text, facts=d.facts, source=source)
        s.messages.append(m)
        await self.rt.broadcast("board_message", m)

    async def _run(self, s: BoardSession) -> None:
        rt = self.rt
        try:
            await rt.broadcast("board_state", BoardState(session_id=s.id, incident_id=s.incident_id, state="running", started_at=s.started_at))
            tasks = self.open_tasks()
            if not tasks:
                s.state = "done"
                await rt.broadcast("board_state", BoardState(session_id=s.id, incident_id=s.incident_id, state="done", started_at=s.started_at))
                return
            a = await asyncio.to_thread(analyse, tasks, self.service.A, s.id, self._overloads())
            drafts = compose(a)
            for rnd in sorted({d.round for d in drafts}):
                group = [d for d in drafts if d.round == rnd]
                voiced = await asyncio.gather(*(voice(d, s.id, i) for i, d in enumerate(group)))
                for d, (text, source) in zip(group, voiced):
                    await self._send(s, d, text, source)
                    await asyncio.sleep(PACE)
            sd = summary_draft(a)
            text, source = await voice(sd, s.id, 0)
            rec = calculation_record(a, self.service.A, f"calc-board-{s.id[-4:]}")
            rt.calculations[rec.id] = rec
            s.decision = decision_object(a, text, source, rec.id)
            s.state = "done"
            await self._propose(s, a)
            await rt.broadcast("board_decision", s.decision)
            await rt.broadcast("board_state", BoardState(session_id=s.id, incident_id=s.incident_id, state="done", started_at=s.started_at))
        except Exception:  # noqa: BLE001
            log.exception("board session %s failed", s.id)
            s.state = "done"
            await rt.broadcast("board_state", BoardState(session_id=s.id, incident_id=s.incident_id, state="done", started_at=s.started_at))

    async def _propose(self, s: BoardSession, a: Analysis) -> None:
        """A new proposal replaces any earlier one nobody has decided on, and goes to the log with a fingerprint of what it said."""
        d = s.decision
        self.analyses[s.id] = a
        busy = self.dispatch.claimed() & {(t.kind, t.asset_id) for t in a.tasks}
        if busy:  # the meeting ran while a plan covering these problems was being approved: nothing left to propose
            d.status = "stale"
            self.log(actor="system", action="proposal_stale", session_id=s.id, subject=d.calculation_id,
                     detail=f"Dropped: crews were already sent to {len(busy)} of its problems by an approved plan.")
            return
        for other in self.sessions.values():
            if other.id != s.id and other.decision and other.decision.status == "proposed":
                other.decision.status = "superseded"
                self.log(actor="system", action="superseded", session_id=other.id, subject=other.decision.calculation_id,
                         detail=f"Replaced by the newer proposal from {s.id} before anyone decided on it.")
                await self.rt.broadcast("board_decision", other.decision)
        self.log(actor="board", action="proposed", session_id=s.id, subject=d.calculation_id,
                 detail=f"The board proposes {len(d.plan.steps)} job(s) with {d.crews} crew(s), expected cost {d.plan.expected_cost:,.0f} EGP. Waiting for a person to decide.",
                 data={"expected_cost": d.plan.expected_cost, "p90": d.plan.p90, "tasks": len(d.tasks), "summary_source": d.summary_source, "decision_hash": decision_hash(d.dump())})

    def _overloads(self) -> list[tuple[str, float]]:
        g, rt = self.rt.engine.g, self.rt
        out = []
        for a in g.topology.assets:
            if a.kind != "substation" or not rt.engine.sub_on.get(a.id, True):
                continue
            r = rt.latest.get(f"{a.id}-load")
            top = g.sensor_index[f"{a.id}-load"].normal_range.max
            if r and r.value and top and r.value / top > 1.0:
                out.append((a.name, 100.0 * r.value / top))
        return sorted(out, key=lambda x: -x[1])
