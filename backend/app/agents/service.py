"""Wires detection, cost and agents into the live runtime."""

from __future__ import annotations

import asyncio
import itertools
import logging

from app.agents.llm import llm
from app.agents.orchestrator import build_orchestrator
from app.agents.specialists import build_specialist, run_specialist
from app.agents.tools import AGENT_FOR_KIND
from app.cost import Assumptions, IncidentFacts, compute
from app.detection import Detector
from app.runtime import Runtime
from app.schemas import ChatChunk, ChatDone, ChatRoute, Incident

log = logging.getLogger("utilityguard.service")


class AgentService:
    def __init__(self, rt: Runtime, assumptions: Assumptions | None = None):
        self.rt = rt
        self.A = assumptions or Assumptions()
        self.detector = Detector(rt)
        rt.detector = self.detector
        rt.tick_hooks.append(self.detector.tick)
        rt.tick_hooks.append(self._sync_llm_mode)
        rt.reset_hooks.append(self.detector.reset)
        self.detector.on_incident.append(self._on_incident)
        self.specialist = build_specialist(rt, self.A)
        self.orchestrator = build_orchestrator(rt, self.A)
        self.tasks: set[asyncio.Task] = set()
        self._msg_ids = itertools.count(1)
        self.settle_seconds = 6.0

    async def _sync_llm_mode(self, rt: Runtime) -> None:
        rt.llm_mode = llm.mode

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    # ---------- incidents -> findings ----------
    def _on_incident(self, inc: Incident, facts: IncidentFacts, root: str) -> None:
        self._spawn(self.analyse(inc, facts, root))

    async def analyse(self, inc: Incident, facts: IncidentFacts, root: str) -> None:
        agent = AGENT_FOR_KIND[facts.kind]
        # let the estimate settle for a few ticks (a burst grows over ~10 s) before costing it
        await asyncio.sleep(self.settle_seconds)
        latest = self.detector.facts_for(inc.id)
        facts = latest[0] if latest else facts
        inc = self.rt.incidents.get(inc.id, inc)
        try:
            finding = await run_specialist(self.specialist, agent, inc, facts, root)
        except Exception:  # noqa: BLE001
            log.exception("specialist failed for %s", inc.id)
            return
        rt = self.rt
        rt.findings[finding.id] = finding
        cur = rt.incidents.get(inc.id, inc)
        current = self.detector.state.incident_kind.get(inc.id) in (None, facts.kind)  # not superseded by a bigger root cause meanwhile
        rt.incidents[inc.id] = cur.model_copy(update={"finding_ids": [*cur.finding_ids, finding.id],
                                                      "calculation_id": finding.explain.calculation_id,
                                                      "reanalysing": cur.reanalysing and not current})
        await rt.broadcast("agent_finding", finding)
        await rt.broadcast("incident", rt.incidents[inc.id])

    async def recompute(self) -> int:
        """Assumptions changed: recompute every open incident's record and re-send its finding (same id)."""
        rt, n = self.rt, 0
        for f in list(rt.findings.values()):
            inc = rt.incidents.get(f.incident_id)
            got = self.detector.facts_for(f.incident_id)
            if not inc or inc.state == "resolved" or not got:
                continue
            facts, _, _ = got
            rec = compute(facts, self.A, inc.id, calc_id=f.explain.calculation_id)
            rt.calculations[rec.id] = rec
            nf = f.model_copy(update={"cost": rec.cost, "explain": rec.explanation,
                                      "reasoning_steps": [*f.reasoning_steps, "Recomputed after an assumption change"]})
            rt.findings[f.id] = nf
            await rt.broadcast("agent_finding", nf)
            n += 1
        return n

    # ---------- chat ----------
    def chat(self, message: str, incident_id: str | None) -> str:
        mid = f"msg-{next(self._msg_ids):04d}"
        self._spawn(self._chat(mid, message, incident_id))
        return mid

    async def _chat(self, mid: str, message: str, incident_id: str | None) -> None:
        rt = self.rt

        async def on_route(steps):
            await rt.broadcast("chat_route", ChatRoute(message_id=mid, routing=steps))

        try:
            out = await self.orchestrator.ainvoke({"question": message, "incident_id": incident_id, "on_route": on_route})
        except Exception:  # noqa: BLE001
            log.exception("orchestrator failed")
            await rt.broadcast("chat_chunk", ChatChunk(message_id=mid, text="The team could not answer right now. Please try again."))
            await rt.broadcast("chat_done", ChatDone(message_id=mid, agents_consulted=[], routing=[], fallback=True))
            return
        words = out["answer"].split(" ")
        for i in range(0, len(words), 3):
            await rt.broadcast("chat_chunk", ChatChunk(message_id=mid, text=" ".join(words[i:i + 3]) + " "))
            await asyncio.sleep(0.04)
        rec = out.get("record")
        await rt.broadcast("chat_done", ChatDone(
            message_id=mid, agents_consulted=[r.agent for r in out["route"]], routing=out["route"],
            fallback=bool(out.get("fallback")), calculation_id=rec.id if rec else None,
            finding_ids=[f.id for f in rt.findings.values() if rt.incidents.get(f.incident_id) and rt.incidents[f.incident_id].state != "resolved"]))
