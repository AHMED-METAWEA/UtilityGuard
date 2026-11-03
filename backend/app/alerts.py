"""Telegram alerts: when a problem opens, grows, is resolved, or an early warning is raised, a message with every detail goes to the chat.

No language model is in the path. Each message is assembled by code from the records the app already holds: the incident, the agent's finding (what the sensors
show, the probable cause, the impact, the recommended action) and the cost engine's figures for it, so every number in a message is a number on the Incidents page.
It goes out whether or not a model is reachable.

When a message is sent:
- a new incident, once the agent's finding is ready (or after `ALERTS_WAIT_FINDING_S` without one, with what the detector knows, so a slow model never delays an alarm);
- the same incident again when its severity rises ("escalated") or the root cause turns out to be a bigger one ("updated");
- when it is resolved, with how long it was open;
- an early warning when it opens, and again if the asset then fails (with the lead time).
At most `ALERTS_PER_HOUR` messages go out in an hour; the first one held back sends a single notice and the rest wait in the delivery log. Nothing is retried: the log
keeps what happened, and the app shows the same incident whether or not the message arrived.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from app.briefing import ZONE_AR
from app.schemas import AgentFinding, EarlyWarning, Incident
from app.schemas.alerts import AlertDelivery, AlertKind, AlertsStatus
from app.sim.engine import CAIRO

log = logging.getLogger("utilityguard.alerts")

TELEGRAM_LIMIT = 4096
RANK = {"info": 0, "warning": 1, "critical": 2}
ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
FOOTER = {"en": "Simulated twin: costs use planning assumptions, not measured prices.", "ar": "التوأم محاكاة: التكاليف مبنية على افتراضات تخطيط، مش أسعار مقاسة."}
SEVERITY = {"en": {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}, "ar": {"critical": "حرج", "warning": "تحذير", "info": "معلومة"}}
URGENCY = {"en": {"immediate": "immediately", "within_hours": "within hours", "scheduled": "scheduled"}, "ar": {"immediate": "فورًا", "within_hours": "خلال ساعات", "scheduled": "مجدول"}}
NETWORK = {"en": {"water": "water", "electricity": "electricity", "cooling": "cooling"}, "ar": {"water": "المياه", "electricity": "الكهرباء", "cooling": "التبريد"}}


def money(x: float) -> str:
    return f"{x:,.0f}"


def clip(text: str, n: int = TELEGRAM_LIMIT) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


@dataclass
class _Seen:
    first_seen: datetime
    severity: str
    title: str
    opened: bool = False
    findings: int = 0
    resolved: bool = False


class TelegramAlerts:
    def __init__(self, rt, mls=None, transport: httpx.AsyncBaseTransport | None = None, clock: Callable[[], datetime] | None = None):
        self.rt, self.mls, self.transport = rt, mls, transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.deliveries: deque[AlertDelivery] = deque(maxlen=60)
        self._seen: dict[str, _Seen] = {}
        self._warned: dict[str, str] = {}  # early warning id -> last state sent
        self._sent_at: deque[datetime] = deque()
        self._notice_sent = False
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()
        rt.reset_hooks.append(self.reset)

    # ---------- settings (read each time, so a changed .env needs only a restart) ----------
    @staticmethod
    def configured() -> bool:
        return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

    @staticmethod
    def lang() -> str:
        v = os.getenv("ALERTS_LANG") or os.getenv("BRIEFING_LANG") or "ar"
        return v if v in ("en", "ar") else "ar"

    @staticmethod
    def min_severity() -> str:
        v = os.getenv("ALERTS_MIN_SEVERITY", "warning")
        return v if v in RANK else "warning"

    @staticmethod
    def per_hour() -> int:
        try:
            return max(1, int(os.getenv("ALERTS_PER_HOUR", "30")))
        except ValueError:
            return 30

    @staticmethod
    def enabled() -> bool:
        return os.getenv("ALERTS_ENABLED", "1") == "1"

    def status(self) -> AlertsStatus:
        return AlertsStatus(configured=self.configured(), enabled=self.enabled(), lang=self.lang(), min_severity=self.min_severity(),  # type: ignore[arg-type]
                            warnings=os.getenv("ALERTS_WARNINGS", "1") == "1", per_hour=self.per_hour(), deliveries=list(self.deliveries))

    def reset(self) -> None:
        self._seen.clear()
        self._warned.clear()
        self._notice_sent = False

    # ---------- the check, once per tick ----------
    async def tick(self, rt) -> None:
        if not (self.enabled() and self.configured()):
            return
        now = self.clock()
        wait = float(os.getenv("ALERTS_WAIT_FINDING_S", "45"))
        for inc in list(rt.incidents.values()):
            st = self._seen.get(inc.id)
            finding = self._finding(inc)
            if st is None:
                if RANK[inc.severity] < RANK[self.min_severity()] and inc.state != "resolved":
                    continue
                st = self._seen[inc.id] = _Seen(first_seen=now, severity=inc.severity, title=inc.title)
            if inc.state == "resolved":
                if st.opened and not st.resolved:
                    st.resolved = True
                    self._send("resolved", inc.id, inc.title, self.resolved_message(inc, now, self.lang()))
                elif not st.opened:
                    st.resolved = True  # resolved before anyone was told: nothing to close
                continue
            if not st.opened:
                if finding or (now - st.first_seen).total_seconds() >= wait:
                    st.opened, st.findings, st.severity, st.title = True, len(inc.finding_ids), inc.severity, inc.title
                    self._send("incident", inc.id, inc.title, self.incident_message(inc, finding, "incident", self.lang()))
                continue
            if RANK[inc.severity] > RANK[st.severity]:
                st.severity, st.findings, st.title = inc.severity, len(inc.finding_ids), inc.title
                self._send("escalated", inc.id, inc.title, self.incident_message(inc, finding, "escalated", self.lang()))
            elif finding and inc.title != st.title and not inc.reanalysing and len(inc.finding_ids) > st.findings:
                st.findings, st.title = len(inc.finding_ids), inc.title
                self._send("updated", inc.id, inc.title, self.incident_message(inc, finding, "updated", self.lang()))
        if self.mls is not None and os.getenv("ALERTS_WARNINGS", "1") == "1":
            for w in list(self.mls.warnings.warnings.values()):
                last = self._warned.get(w.id)
                if last is None and w.state == "active":
                    self._warned[w.id] = "active"
                    self._send("warning", w.id, f"{w.asset_name}", self.warning_message(w, "warning", self.lang()))
                elif last == "active" and w.state == "failed":
                    self._warned[w.id] = "failed"
                    self._send("warning_failed", w.id, f"{w.asset_name}", self.warning_message(w, "warning_failed", self.lang()))
                elif last == "active" and w.state == "cleared":
                    self._warned[w.id] = "cleared"

    def _finding(self, inc: Incident) -> AgentFinding | None:
        return next((self.rt.findings[i] for i in reversed(inc.finding_ids) if i in self.rt.findings), None)

    # ---------- composing ----------
    def _zone(self, z: str, lang: str) -> str:
        return ZONE_AR.get(z, z) if lang == "ar" else self.rt.engine.g.zone_index[z].name

    def _assets(self, inc: Incident) -> list[str]:
        ids: list[str] = []
        for a in (self.rt.alerts.get(i) for i in inc.alert_ids):
            for aid in (a.asset_ids if a else []):
                if aid not in ids:
                    ids.append(aid)
        return [self.rt.engine.g.asset_index[i].name if i in self.rt.engine.g.asset_index else i for i in ids]

    def _app(self, path: str) -> list[str]:
        url = os.getenv("APP_URL", "").rstrip("/")
        return [("🔗 " + f"{url}/#{path}")] if url else []

    def incident_message(self, inc: Incident, f: AgentFinding | None, kind: AlertKind, lang: str) -> str:
        ar = lang == "ar"
        head = {"incident": "", "escalated": ("⬆️ تصعيد: " if ar else "⬆️ ESCALATED: "), "updated": ("🔄 تحديث: " if ar else "🔄 UPDATED: ")}[kind]  # type: ignore[index]
        opened = inc.opened_at.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M:%S")
        zones = "، ".join(self._zone(z, lang) for z in inc.zone_ids) if ar else ", ".join(self._zone(z, lang) for z in inc.zone_ids)
        nets = ("، " if ar else ", ").join(NETWORK[lang][n] for n in inc.networks)
        assets = ("، " if ar else ", ").join(self._assets(inc))
        lines = [f"{ICON[inc.severity]} {head}{SEVERITY[lang][inc.severity]} — {inc.title}",
                 (f"الحادثة {inc.id} · فُتحت {opened} (توقيت القاهرة)" if ar else f"Incident {inc.id} · opened {opened} Cairo time"),
                 (f"المكان: {zones} · الشبكة: {nets}" if ar else f"Where: {zones} · network: {nets}")]
        if assets:
            lines.append((f"الأصول: {assets}" if ar else f"Assets: {assets}"))
        optional: list[list[str]] = []
        if f:
            lines += ["", f.headline]
            if f.probable_causes:
                optional.append([("السبب المحتمل:" if ar else "Probable cause:")] + [f"• {c}" for c in f.probable_causes[:3]])
            reasons = [r.text for r in f.explain.reasons[:6]]
            if reasons:
                optional.append([("ما تُظهره الحساسات:" if ar else "What the sensors show:")] + [f"• {t}" for t in reasons])
            optional.append([("الأثر المتوقع: " if ar else "Expected impact: ") + f.predicted_impact.text])
            c = f.cost
            cost = [("التكلفة (افتراضات تخطيط):" if ar else "Cost (planning assumptions):"),
                    (f"• كل ساعة والمشكلة مفتوحة: {money(c.delay_per_hour)} جنيه" if ar else f"• Each hour it stays open: {money(c.delay_per_hour)} EGP"),
                    (f"• التحرك الآن: {money(c.act_now)} جنيه" + (f" (الطاقم يصل خلال {c.response_hours:g} ساعة)" if c.response_hours else "") if ar
                     else f"• Acting now: {money(c.act_now)} EGP" + (f" (crew in {c.response_hours:g} h)" if c.response_hours else ""))]
            if c.cost_of_delay is not None and c.delay_hours:
                cost.append((f"• الانتظار {c.delay_hours:g} ساعة: {money(c.cost_of_delay)} جنيه" if ar else f"• Waiting {c.delay_hours:g} h: {money(c.cost_of_delay)} EGP"))
            cost.append((f"• الوفر من التحرك الآن: {money(c.expected_saving)} جنيه" if ar else f"• Saving from acting now: {money(c.expected_saving)} EGP"))
            if c.saving_range:
                r = c.saving_range
                cost.append((f"• مدى الوفر (P10–P90): {money(r.p10)} إلى {money(r.p90)} جنيه" if ar else f"• Saving range (P10–P90): {money(r.p10)} to {money(r.p90)} EGP"))
            if c.review:
                cost.append("• ⚠️ " + ("الرقم يحتاج مراجعة: خارج المدى المعقول" if ar else "needs review: outside the plausible range"))
            optional.append(cost)
            a = f.recommended_action
            optional.append([(f"المطلوب: {a.text} — طاقم {a.crew_type}، {URGENCY[lang][a.urgency]}" if ar else f"Recommended: {a.text} — {a.crew_type} crew, {URGENCY[lang][a.urgency]}")])
            if f.fallback:
                optional.append([("(الشرح من القواعد، من غير النموذج اللغوي)" if ar else "(explained by the rules, not the language model)")])
        else:
            lines += ["", ("الوكلاء لسه بيحسبوا التكلفة؛ التفاصيل في التطبيق." if ar else "The agents are still working out the cost; details are in the app.")]
        tail = self._app(f"/incidents/{inc.id}") + [FOOTER[lang]]
        return self._fit(lines, optional, tail)

    def resolved_message(self, inc: Incident, now: datetime, lang: str) -> str:
        ar = lang == "ar"
        mins = max(0.0, (self.rt.now - inc.opened_at).total_seconds() / 60)
        lines = [f"✅ {'انتهت' if ar else 'RESOLVED'} — {inc.title}",
                 (f"الحادثة {inc.id} · ظلّت مفتوحة {mins:.1f} دقيقة (وقت التوأم)" if ar else f"Incident {inc.id} · was open for {mins:.1f} minutes (twin time)")]
        f = self._finding(inc)
        if f:
            lines.append((f"تقدير تكلفة الساعة وقتها: {money(f.cost.delay_per_hour)} جنيه" if ar else f"Its cost estimate was {money(f.cost.delay_per_hour)} EGP per hour while open"))
        return "\n".join(lines + self._app(f"/incidents/{inc.id}") + [FOOTER[lang]])

    def warning_message(self, w: EarlyWarning, kind: AlertKind, lang: str) -> str:
        ar = lang == "ar"
        at = w.created_at.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M:%S")
        c = w.cost
        if kind == "warning_failed":
            lead = f"{(w.lead_time_seconds or 0) / 60:.1f}"
            lines = [(f"⛔ {w.asset_name} وقفت بعد الإنذار المبكر بـ {lead} دقيقة" if ar else f"⛔ {w.asset_name} failed {lead} minutes after its early warning"),
                     (f"الإنذار {w.id} · فُتح {at} (توقيت القاهرة) · المنطقة: {self._zone(w.zone_id, lang)}" if ar else f"Warning {w.id} · raised {at} Cairo time · zone: {self._zone(w.zone_id, lang)}")]
            return "\n".join(lines + self._app("/incidents") + [FOOTER[lang]])
        lines = [(f"🟡 إنذار مبكر — {w.asset_name} لسه شغالة بس حالتها بتنحرف" if ar else f"🟡 EARLY WARNING — {w.asset_name} is still running but drifting"),
                 (f"الإنذار {w.id} · {at} (توقيت القاهرة) · المنطقة: {self._zone(w.zone_id, lang)} · الشبكة: {NETWORK[lang][w.network]}" if ar
                  else f"Warning {w.id} · {at} Cairo time · zone: {self._zone(w.zone_id, lang)} · network: {NETWORK[lang][w.network]}"),
                 "",
                 (f"درجة الخطر {w.risk:.0%} بينما العمر والحمل لوحدهم يدّوا {w.peer_risk:.0%} (الفرق {w.excess:.0%})" if ar
                  else f"Risk score {w.risk:.0%} where age and load alone would give {w.peer_risk:.0%} (excess {w.excess:.0%})"),
                 ("الإشارات:" if ar else "Signals:")]
        lines += [f"• {s.label}: {s.value:.2f} " + ("مقابل" if ar else "against") + f" {s.peer_value:.2f} " + ("للأصول المشابهة" if ar else "for its peers") for s in w.signals]
        if w.rise_per_minute is not None:
            lines.append((f"• معدل الارتفاع: {w.rise_per_minute:+.3f} في الدقيقة" if ar else f"• Rising by {w.rise_per_minute:+.3f} per minute"))
        if w.minutes_to_90 is not None:
            lines.append((f"• لو استمر على نفس المعدل يوصل 90% بعد {w.minutes_to_90:.1f} دقيقة (استقراء مش توقع)" if ar
                          else f"• If the excess keeps rising, the score reaches 90% in {w.minutes_to_90:.1f} minutes (an extrapolation, not a forecast)"))
        lines += ["", (f"الصيانة المخططة {money(c.planned)} جنيه مقابل {money(c.unplanned)} جنيه لو وقفت فجأة. الصيانة تدفع لما احتمال العطل يعدّي {c.break_even:.1%}." if ar
                        else f"Planned maintenance {money(c.planned)} EGP against {money(c.unplanned)} EGP if it fails. Maintenance pays when the chance of failing is above {c.break_even:.1%}."),
                  ("التقدير: " if ar else "Verdict: ") + (("صيانة الآن أرخص" if c.verdict == "maintain_now" else "الانتظار أرخص لكن راقبها") if ar
                                                       else ("maintaining now is cheaper" if c.verdict == "maintain_now" else "waiting is still cheaper, but watch it")),
                  ("(درجة الخطر ترتيب مش احتمال معايَر: النموذج متدرّب على بيانات اصطناعية)" if ar else "(the risk score is a ranking, not a calibrated probability: the model is trained on a synthetic hazard)")]
        return "\n".join(lines + self._app("/incidents") + [FOOTER[lang]])

    @staticmethod
    def _fit(head: list[str], blocks: list[list[str]], tail: list[str]) -> str:
        """The message with every block; when it is over Telegram's limit the blocks are dropped from the last one first, never the head or the cost."""
        keep = list(blocks)
        while True:
            text = "\n".join(head + [x for b in keep for x in [""] + b] + [""] + tail)
            if len(text) <= TELEGRAM_LIMIT or not keep:
                return clip(text)
            drop = next((i for i, b in enumerate(keep) if b and b[0].startswith(("ما تُظهره", "What the sensors", "السبب", "Probable"))), len(keep) - 1)
            keep.pop(drop)

    # ---------- sending ----------
    def _send(self, kind: AlertKind, subject: str, title: str, text: str) -> None:
        now = self.clock()
        while self._sent_at and now - self._sent_at[0] > timedelta(hours=1):
            self._sent_at.popleft()
        if not self._sent_at:
            self._notice_sent = False  # a quiet hour: the next time alerts are held back the notice goes out again
        if len(self._sent_at) >= self.per_hour():
            self._log(kind, subject, title, "suppressed", f"Over {self.per_hour()} messages in an hour: held back.", len(text), now)
            if not self._notice_sent:
                self._notice_sent = True
                self._sent_at.append(now)
                notice = ("⏸️ كتير من التنبيهات في ساعة واحدة؛ باقي التنبيهات محجوزة. شوف التطبيق." if self.lang() == "ar"
                          else "⏸️ Many alerts in one hour; the rest are held back. Open the app.")
                self._spawn("paused", "paused", "paused", notice, now)
            return
        self._sent_at.append(now)
        self._spawn(kind, subject, title, text, now)

    def _spawn(self, kind: AlertKind, subject: str, title: str, text: str, now: datetime) -> None:
        t = asyncio.create_task(self._post(kind, subject, title, text, now))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _post(self, kind: AlertKind, subject: str, title: str, text: str, now: datetime) -> AlertDelivery:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return self._log(kind, subject, title, "skipped", "Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.", len(text), now)
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self.transport) as client:
                r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": clip(text), "disable_web_page_preview": True})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and body.get("ok"):
                return self._log(kind, subject, title, "sent", "Delivered to Telegram.", len(text), now)
            return self._log(kind, subject, title, "failed", f"Telegram refused it: {body.get('description') or r.status_code}.", len(text), now)
        except Exception as e:  # noqa: BLE001 - a network error, or the token inside its text, never reaches the log
            return self._log(kind, subject, title, "failed", f"Could not reach Telegram ({type(e).__name__}).", len(text), now)

    def _log(self, kind: AlertKind, subject: str, title: str, state: str, detail: str, chars: int, at: datetime) -> AlertDelivery:
        d = AlertDelivery(id=next(self._ids), kind=kind, subject_id=subject, title=title, state=state, at=at, detail=detail, characters=chars)  # type: ignore[arg-type]
        self.deliveries.appendleft(d)
        log.info("alert %s %s: %s (%s)", kind, subject, state, detail)
        return d

    async def send_test(self) -> AlertDelivery:
        """One short message, to check the bot and the chat id. It says it is a test and carries no incident data."""
        ar = self.lang() == "ar"
        text = ("✅ رسالة تجربة من UtilityGuard: التنبيهات متوصلة. أي مشكلة هتتبعت هنا بكل تفاصيلها." if ar
                else "✅ Test message from UtilityGuard: alerts are connected. Any problem will be sent here with its details.")
        return await self._post("test", "test", "test", text, self.clock())
