"""The morning briefing: what needs the owner's attention today, composed by code in English and Arabic.

No language model is in the path. The text is assembled from templates over figures the dashboard already shows, each figure is registered with its value
and the way it reads (`agents/figures.py`), and the same registry is returned with the briefing so every number in it can be traced. The message therefore
goes out at 07:00 whether or not a model is reachable or in quota.

Three rules decide what is said:
- events and exposures are kept apart: an open incident is money being lost now, an early warning or a forecast peak is something that may cost money;
- what needs a person comes first: a board decision waiting for approval is above everything else;
- a problem reported in a recent briefing is not reported again as news. It is counted ("2 problems already reported are still open"), so the channel
  is not muted by repetition. The fingerprint names the problem and never its value, so the same leak is recognised tomorrow at a different cost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from app.agents.figures import FigureSet, slug
from app.dashboard import SUB_CAPACITY_MW
from app.i18n import NETWORK_AR, ZONE_AR
from app.schemas.briefing import (
    Briefing, BriefingChannel, BriefingDelivery, BriefingFigure, BriefingItem, BriefingSection, Lang,
)
from app.sim.engine import CAIRO
from app.sim.topology import SUBSTATION_BACKUP

log = logging.getLogger("utilityguard.briefing")

DATA = Path(__file__).resolve().parents[1] / "data"
COOLDOWN_HOURS = float(os.getenv("BRIEFING_COOLDOWN_HOURS", "12"))
SEND_AT = os.getenv("BRIEFING_AT", "07:00")  # Cairo time
PEAK_WARN = 0.85  # electrical peak as a share of firm capacity above which the outlook is a warning
MAX_ITEMS = 3  # per section: a briefing that lists everything is read as nothing
TELEGRAM_LIMIT = 4096

MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
KIND_EN = {"leak": "Pipe burst", "pump_failure": "Pump failure", "substation_trip": "Substation trip", "cooling_fault": "Cooling plant fault",
           "energy_waste": "Substation overload", "sensor_fault": "Sensor fault"}
KIND_AR = {"leak": "تسريب أو كسر في ماسورة", "pump_failure": "عطل في طلمبة", "substation_trip": "فصل محطة كهرباء", "cooling_fault": "عطل في محطة التبريد",
           "energy_waste": "حمل زائد على محطة كهرباء", "sensor_fault": "عطل في حساس"}


class Fmt:
    """How a figure reads in each language. The unit is part of the figure, so a sentence never types one beside it."""

    def __init__(self, lang: Lang):
        self.ar = lang == "ar"

    def money(self, v: float) -> str:
        return f"{v:,.0f} " + ("جنيه" if self.ar else "EGP")

    def flow(self, v: float) -> str:
        return f"{v:,.0f} " + ("م³/ساعة" if self.ar else "m3/h")

    def mw(self, v: float) -> str:
        return f"{v:,.1f} " + ("ميجاوات" if self.ar else "MW")

    def pct(self, v: float, digits: int = 0) -> str:
        return f"{v:.{digits}%}"

    def count(self, v: float) -> str:
        return f"{v:,.0f}"


class BriefingState:
    """What has been reported and when, kept in a file so a restart does not repeat the morning."""

    def __init__(self, path: Path):
        self.path = path
        self.sent: dict[str, dict] = {}
        self.last_date: str | None = None
        try:
            raw = json.loads(path.read_text())
            self.sent, self.last_date = raw.get("sent", {}), raw.get("last_date")
        except Exception:  # noqa: BLE001 - a missing or damaged file is an empty history, not an error
            pass

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"sent": self.sent, "last_date": self.last_date}))
        except OSError:
            log.warning("could not write the briefing state")

    def recent(self, fingerprint: str, severity: str, now: datetime, hours: float) -> bool:
        """Reported within the cooldown at the same severity or worse? A problem that got worse is news again."""
        row = self.sent.get(fingerprint)
        if not row:
            return False
        rank = {"info": 0, "warning": 1, "critical": 2}
        age = (now - datetime.fromisoformat(row["at"])).total_seconds() / 3600
        return age < hours and rank[severity] <= rank.get(row["severity"], 0)

    def mark(self, items: list[BriefingItem], now: datetime) -> None:
        for it in items:
            if it.kind in ("incident", "warning"):
                self.sent[it.fingerprint] = {"at": now.isoformat(), "severity": it.severity}
        self.last_date = now.astimezone(CAIRO).date().isoformat()
        self.save()


# ---------------------------------------------------------------- composition
class BriefingService:
    def __init__(self, rt, board, mls, hist, savings_total: Callable[[], float | None], state_path: Path | None = None):
        self.rt, self.board, self.mls, self.hist, self.savings_total = rt, board, mls, hist, savings_total
        self.state = BriefingState(state_path or Path(os.getenv("BRIEFING_STATE", DATA / "briefing_state.json")))
        self.deliveries: deque[BriefingDelivery] = deque(maxlen=20)

    # ----- the pieces
    def _incidents(self) -> list[tuple]:
        rt = self.rt
        out = []
        for inc in rt.incidents.values():
            if inc.state == "resolved":
                continue
            findings = [f for f in rt.findings.values() if f.incident_id == inc.id]
            f = findings[-1] if findings else None
            kind = rt.detector.state.incident_kind.get(inc.id, "") if getattr(rt, "detector", None) else ""
            out.append((inc, f, kind))
        out.sort(key=lambda t: (-(t[1].cost.delay_per_hour if t[1] else 0), t[0].id))
        return out

    def _peaks(self) -> dict[str, tuple[float, datetime]]:
        from app import dashboard
        out = {}
        for net in ("water", "electricity", "cooling"):
            try:
                series = dashboard.demand_series(self.rt, self.hist, self.mls, net, "all", 24)
            except Exception:  # noqa: BLE001 - an outlook line is optional
                continue
            pts = [p for p in series.forecast if p.timestamp > self.rt.now][:48]
            if pts:
                top = max(pts, key=lambda p: p.predicted)
                out[net] = (top.predicted, top.timestamp)
        return out

    def _bad_sensors(self) -> int:
        return sum(1 for r in self.rt.latest.values() if r.quality != "ok")

    # ----- composition
    def compose(self, lang: Lang = "ar", suppress_repeats: bool = False, now: datetime | None = None) -> Briefing:
        rt, ar, fmt = self.rt, lang == "ar", Fmt(lang)
        now = now or datetime.now(timezone.utc)
        fs = FigureSet()
        sections: dict[str, list[BriefingItem]] = {"decision": [], "now": [], "watch": [], "outlook": [], "money": []}
        zone_name = lambda z: ZONE_AR.get(z, z) if ar else rt.engine.g.zone_index[z].name  # noqa: E731

        # a decision waiting for a person comes first
        session = self.board.latest() if self.board else None
        dec = session.decision if session else None
        if dec and dec.status == "proposed":
            alarm = next((a for a in dec.alternatives if a.id == "alarm"), None)
            fs.add("board_tasks", len(dec.tasks), fmt.count(len(dec.tasks)), "problems the board ordered")
            fs.add("board_cost", dec.plan.expected_cost, fmt.money(dec.plan.expected_cost), "expected cost of the board's order")
            text = ("The board proposes an order for {{board_tasks}} problems with an expected cost of {{board_cost}}"
                    if not ar else "اقترح المجلس ترتيباً لإرسال الأطقم على {{board_tasks}} مشكلات بتكلفة متوقعة {{board_cost}}")
            if alarm and alarm.extra_cost > 0:
                fs.add("board_saving", alarm.extra_cost, fmt.money(alarm.extra_cost), "saving against working in alarm order")
                text += (", which is {{board_saving}} less than working in the order the alarms arrived. It is waiting for your approval."
                         if not ar else "، أي أقل بـ {{board_saving}} من العمل بترتيب وصول الإنذارات. القرار ينتظر موافقتك.")
            else:
                text += ". It is waiting for your approval." if not ar else ". القرار ينتظر موافقتك."
            sections["decision"].append(BriefingItem(kind="decision", severity="warning", text=text, fingerprint=f"decision:{dec.session_id}",
                                                     link=f"#/incidents/{dec.incident_ids[0]}" if dec.incident_ids else None))
        elif dec and dec.status == "executing":
            sections["decision"].append(BriefingItem(
                kind="decision", severity="info", fingerprint=f"executing:{dec.session_id}",
                text="An approved plan is being carried out." if not ar else "خطة معتمدة قيد التنفيذ الآن.",
                link=f"#/incidents/{dec.incident_ids[0]}" if dec.incident_ids else None))

        # what is going wrong now
        incidents = self._incidents()
        for inc, f, kind in incidents[:MAX_ITEMS]:
            zone = zone_name(inc.zone_ids[0]) if inc.zone_ids else ""
            name = (KIND_AR.get(kind) if ar else KIND_EN.get(kind)) or inc.title
            where = f" في {zone}" if ar and zone else f" in {zone}" if zone else ""
            k = slug(inc.id)
            sev = "critical" if (f and f.cost.delay_per_hour > 0 and inc.severity == "critical") or inc.severity == "critical" else "warning"
            if f:
                fs.add(f"{k}_act_now", f.cost.act_now, fmt.money(f.cost.act_now), f"{inc.id}: cost of acting now")
                fs.add(f"{k}_delay", f.cost.delay_per_hour, fmt.money(f.cost.delay_per_hour), f"{inc.id}: extra cost of each hour of delay")
                text = (f"{name}{where}: acting now costs {{{{{k}_act_now}}}}, and each hour of delay adds {{{{{k}_delay}}}}."
                        if not ar else f"{name}{where}: التحرك الآن يكلف {{{{{k}_act_now}}}}، وكل ساعة تأخير تضيف {{{{{k}_delay}}}}.")
            else:
                text = f"{name}{where}: the agents are still working out the cost." if not ar else f"{name}{where}: الوكلاء يحسبون التكلفة الآن."
            sections["now"].append(BriefingItem(kind="incident", severity=sev, text=text, fingerprint=f"incident:{kind}:{inc.id}", link=f"#/incidents/{inc.id}"))
        if len(incidents) > MAX_ITEMS:
            fs.add("more_incidents", len(incidents) - MAX_ITEMS, fmt.count(len(incidents) - MAX_ITEMS), "further open incidents")
            sections["now"].append(BriefingItem(kind="incident", severity="info", fingerprint="incident:more",
                                                text="Further open incidents: {{more_incidents}}." if not ar else "حوادث مفتوحة أخرى: {{more_incidents}}."))
        if incidents:
            total = sum(f.cost.delay_per_hour for _, f, _ in incidents if f)
            fs.add("open_incidents", len(incidents), fmt.count(len(incidents)), "open incidents")
            fs.add("delay_total", total, fmt.money(total), "extra cost per hour of delay across every open incident")
            head = ("Open incidents: {{open_incidents}}. Every hour without action adds {{delay_total}}."
                    if not ar else "الحوادث المفتوحة: {{open_incidents}}. كل ساعة بلا تحرك تضيف {{delay_total}}.")
        else:
            head = "No open incidents. All networks are within their normal range." if not ar else "لا توجد حوادث مفتوحة. كل الشبكات ضمن النطاق الطبيعي."
            sections["now"].append(BriefingItem(kind="incident", severity="info", text=head, fingerprint="incident:none"))

        # what may go wrong: early warnings
        warnings = sorted((w for w in self.mls.warnings.listing() if w.state == "active"), key=lambda w: -w.cost.expected_saving)
        for w in warnings[:MAX_ITEMS]:
            k = slug(w.id)
            fs.add(f"{k}_planned", w.cost.planned, fmt.money(w.cost.planned), f"{w.asset_name}: planned maintenance")
            fs.add(f"{k}_unplanned", w.cost.unplanned, fmt.money(w.cost.unplanned), f"{w.asset_name}: cost if it fails")
            fs.add(f"{k}_break_even", w.cost.break_even * 100, fmt.pct(w.cost.break_even, 1), f"{w.asset_name}: break-even chance of failing, in percent")
            text = (f"{w.asset_name} is still running but its condition is drifting away from its peers. Planned maintenance costs {{{{{k}_planned}}}} against "
                    f"{{{{{k}_unplanned}}}} if it fails; maintenance pays above a {{{{{k}_break_even}}}} chance of failing."
                    if not ar else f"{w.asset_name} ما زالت تعمل لكن حالتها تبتعد عن نظيراتها. الصيانة المخططة تكلف {{{{{k}_planned}}}} مقابل {{{{{k}_unplanned}}}} إن تعطلت؛ "
                                   f"والصيانة تفيد متى زاد احتمال العطل عن {{{{{k}_break_even}}}}.")
            sections["watch"].append(BriefingItem(kind="warning", severity="warning", text=text, fingerprint=f"warning:{w.asset_id}", link="#/executive"))
        bad = self._bad_sensors()
        if bad:
            fs.add("bad_sensors", bad, fmt.count(bad), "sensors with suspect or missing readings")
            sections["watch"].append(BriefingItem(
                kind="data", severity="info", fingerprint="data:sensors",
                text="Sensors with suspect or missing readings: {{bad_sensors}}." if not ar else "حساسات بقراءات مشكوك فيها أو مفقودة: {{bad_sensors}}."))

        # the next day
        peaks = self._peaks()
        local = lambda t: t.astimezone(CAIRO).strftime("%H:%M")  # noqa: E731
        if "water" in peaks:
            v, t = peaks["water"]
            fs.add("water_peak", v, fmt.flow(v), "forecast water demand peak")
            sections["outlook"].append(BriefingItem(kind="outlook", severity="info", fingerprint="outlook:water", text=(
                f"Water demand is forecast to peak at {{{{water_peak}}}} around {local(t)}." if not ar else f"يُتوقع أن يبلغ الطلب على المياه ذروته {{{{water_peak}}}} نحو الساعة {local(t)}.")))
        if "electricity" in peaks:
            v, t = peaks["electricity"]
            cap = SUB_CAPACITY_MW * len(SUBSTATION_BACKUP)
            share = v / cap if cap else 0.0
            fs.add("elec_peak", v, fmt.mw(v), "forecast electrical load peak")
            fs.add("elec_cap", cap, fmt.mw(cap), "firm capacity of the substations")
            fs.add("elec_share", share * 100, fmt.pct(share), "forecast peak as a share of firm capacity, in percent")
            sections["outlook"].append(BriefingItem(kind="outlook", severity="warning" if share >= PEAK_WARN else "info", fingerprint="outlook:electricity", text=(
                f"Electrical load is forecast to peak at {{{{elec_peak}}}} around {local(t)}, {{{{elec_share}}}} of the {{{{elec_cap}}}} firm capacity."
                if not ar else f"يُتوقع أن يبلغ الحمل الكهربائي ذروته {{{{elec_peak}}}} نحو الساعة {local(t)}، أي {{{{elec_share}}}} من السعة المضمونة {{{{elec_cap}}}}.")))
        if "cooling" in peaks:
            v, t = peaks["cooling"]
            fs.add("cool_peak", v, fmt.mw(v), "forecast cooling demand peak")
            sections["outlook"].append(BriefingItem(kind="outlook", severity="info", fingerprint="outlook:cooling", text=(
                f"Cooling demand is forecast to peak at {{{{cool_peak}}}} around {local(t)}." if not ar else f"يُتوقع أن يبلغ الطلب على التبريد ذروته {{{{cool_peak}}}} نحو الساعة {local(t)}.")))

        total = self.savings_total()
        if total:
            fs.add("savings_month", total, fmt.money(total), "savings opportunities available each month")
            sections["money"].append(BriefingItem(kind="money", severity="info", fingerprint="money:savings", text=(
                "Savings available: {{savings_month}} a month. Open the Savings page to see where." if not ar else "الوفورات المتاحة: {{savings_month}} شهرياً. افتح صفحة التوفير لتعرف أين.")))

        # repeated problems are counted, not repeated
        repeated = 0
        if suppress_repeats:
            for sec in ("now", "watch"):
                keep = []
                for it in sections[sec]:
                    if it.kind in ("incident", "warning") and self.state.recent(it.fingerprint, it.severity, now, COOLDOWN_HOURS):
                        repeated += 1
                    else:
                        keep.append(it)
                sections[sec] = keep
            if repeated:
                fs.add("repeated", repeated, fmt.count(repeated), "problems already reported within the cooldown")
                sections["now"].append(BriefingItem(kind="repeat", severity="info", fingerprint="repeat", text=(
                    "Problems already reported and still open: {{repeated}}." if not ar else "مشكلات سبق الإبلاغ عنها وما زالت مفتوحة: {{repeated}}.")))

        titles = {"decision": ("Needs your decision", "بانتظار قرارك"), "now": ("Happening now", "يحدث الآن"), "watch": ("Worth watching", "تستحق المتابعة"),
                  "outlook": ("The next 24 hours", "الـ 24 ساعة القادمة"), "money": ("Money", "المال")}
        date = now.astimezone(CAIRO)
        day = f"{date.day} {MONTHS_AR[date.month - 1]} {date.year}" if ar else date.strftime("%d %B %Y")
        headline = f"ملخص UtilityGuard الصباحي، {day}" if ar else f"UtilityGuard morning briefing, {day}"
        out_sections = []
        for sid in ("decision", "now", "watch", "outlook", "money"):
            if not sections[sid]:
                continue
            items = []
            for it in sections[sid]:
                text, unknown = fs.render(it.text)
                if unknown:
                    log.warning("briefing item cited an unknown figure: %s", unknown)
                items.append(it.model_copy(update={"text": text}))
            out_sections.append(BriefingSection(id=sid, title=titles[sid][1 if ar else 0], items=items))
        lead, _ = fs.render(head)
        lines = [headline, lead]
        for sec in out_sections:
            lines += ["", sec.title] + [f"• {it.text}" for it in sec.items]
        text = "\n".join(lines)
        figures = [BriefingFigure(key=f.key, label=f.label, display=f.display, value=f.value) for f in fs.items.values()]
        return Briefing(generated_at=now, lang=lang, headline=headline, sections=out_sections, text=text, figures=figures, repeated=repeated,
                        deliveries=list(self.deliveries))

    # ----- delivery
    def channels(self) -> list[BriefingChannel]:
        ok = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        return [BriefingChannel(channel="in_app", configured=True, detail="Always available: the briefing is kept in the app."),
                BriefingChannel(channel="telegram", configured=ok,
                                detail="Ready." if ok else "Not configured. Create a bot with @BotFather and set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")]

    async def send(self, lang: Lang, channel: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[BriefingDelivery, Briefing]:
        now = datetime.now(timezone.utc)
        b = self.compose(lang, suppress_repeats=True, now=now)
        if channel == "in_app":
            d = BriefingDelivery(channel="in_app", state="sent", detail="Kept in the app.", at=now)
        else:
            d = await self._telegram(b, now, transport)
        if d.state == "sent":
            self.state.mark([it for s in b.sections for it in s.items], now)
        self.deliveries.appendleft(d)
        return d, b.model_copy(update={"deliveries": list(self.deliveries)})

    async def _telegram(self, b: Briefing, now: datetime, transport: httpx.AsyncBaseTransport | None) -> BriefingDelivery:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return BriefingDelivery(channel="telegram", state="skipped", at=now,
                                    detail="Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
                r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                      json={"chat_id": chat, "text": b.text[:TELEGRAM_LIMIT], "disable_web_page_preview": True})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and body.get("ok"):
                return BriefingDelivery(channel="telegram", state="sent", at=now, detail="Delivered to Telegram.")
            return BriefingDelivery(channel="telegram", state="failed", at=now, detail=f"Telegram refused it: {body.get('description') or r.status_code}.")
        except Exception as e:  # noqa: BLE001 - never let a network error, or the token inside its text, reach the caller
            return BriefingDelivery(channel="telegram", state="failed", at=now, detail=f"Could not reach Telegram ({type(e).__name__}).")

    # ----- the daily send
    def due(self, now: datetime) -> bool:
        hh, mm = (int(x) for x in SEND_AT.split(":"))
        local = now.astimezone(CAIRO)
        return (local.hour, local.minute) >= (hh, mm) and self.state.last_date != local.date().isoformat()

    async def loop(self, lang: Lang = "ar") -> None:
        """Sends the briefing once a day at BRIEFING_AT (Cairo) when Telegram is configured. The twin runs on the wall clock, so this is the real morning."""
        while True:
            try:
                if os.getenv("BRIEFING_ENABLED", "1") == "1" and self.channels()[1].configured and self.due(datetime.now(timezone.utc)):
                    d, _ = await self.send(os.getenv("BRIEFING_LANG", lang), "telegram")
                    log.info("daily briefing: %s (%s)", d.state, d.detail)
                    if d.state != "sent":
                        self.state.last_date = datetime.now(timezone.utc).astimezone(CAIRO).date().isoformat()  # do not retry every half minute
                        self.state.save()
            except Exception:  # noqa: BLE001
                log.exception("daily briefing failed")
            await asyncio.sleep(30)
