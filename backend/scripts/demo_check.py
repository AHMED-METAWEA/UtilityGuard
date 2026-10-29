"""Run the demo path against a running backend, several times in a row, and check every number on the way.

    .venv/bin/python -m scripts.demo_check [--runs 3] [--base http://localhost:8000]

Path per run: reset, KPIs and charts, pipe burst (alert, incident, finding), calculation record and PDF,
price change recomputes the saving, substation trip groups several networks, chat routes to three agents, reset.
Exit code 0 only if every run passes. Running it with the LLM live also records the answers into the fallback cache.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx
import websockets

from app import schemas as S
from app.board.session import numbers_ok
from app.schemas import decision as SD
from app.schemas import lab as SL

TIMEOUT = 90


class Check(Exception):
    pass


def ok(cond: bool, msg: str) -> None:
    if not cond:
        raise Check(msg)


class Stream:
    """Collects WebSocket messages so steps can wait for specific ones."""

    def __init__(self, url: str):
        self.url, self.msgs, self.task, self.ws = url, [], None, None

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        self.task = asyncio.create_task(self._pump())
        await self.wait(lambda m: m["type"] == "snapshot", 10)
        return self

    async def __aexit__(self, *_):
        self.task.cancel()
        await self.ws.close()

    async def _pump(self):
        async for raw in self.ws:
            self.msgs.append(json.loads(raw))

    async def wait(self, pred, timeout=TIMEOUT, since=0):
        end = time.time() + timeout
        while time.time() < end:
            for m in self.msgs[since:]:
                if pred(m):
                    return m
            await asyncio.sleep(0.2)
        raise Check(f"timed out waiting for a message ({timeout}s)")


async def one_run(base: str, n: int) -> None:
    ws_url = base.replace("http", "ws") + "/ws/stream"
    async with httpx.AsyncClient(base_url=base + "/api/v1", timeout=60) as c:
        async def get(path, model=None):
            r = await c.get(path)
            ok(r.status_code == 200, f"GET {path} -> {r.status_code}")
            data = r.json()
            if model:
                (model.model_validate(data) if not isinstance(data, list) else [model.model_validate(x) for x in data])
            return data

        await c.post("/scenarios/reset")
        async with Stream(ws_url) as st:
            print(f"  [{n}] dashboard")
            kpis = await get("/kpis", S.Kpi)
            ok({k["id"] for k in kpis} >= {"nrw", "cooling_efficiency", "savings_available"}, "missing KPIs")
            await get("/series/demand?network=water&zoneId=all", S.DemandSeries)
            await get("/series/substations", S.SubstationSeries)
            sav = await get("/savings", S.SavingsReport)
            ok(sav["totalMonthly"] > 0 and sav["opportunities"], "no savings opportunities")

            print(f"  [{n}] pipe burst")
            mark = len(st.msgs)
            run = (await c.post("/scenarios/run", json={"scenarioId": "scn-pipe-burst", "parameters": {}})).json()
            await st.wait(lambda m: m["type"] == "incident", since=mark)
            finding = (await st.wait(lambda m: m["type"] == "agent_finding", since=mark))["payload"]
            S.AgentFinding.model_validate(finding)
            cost = finding["cost"]
            ok(abs(cost["costOfDelay"] - cost["actNow"] - cost["expectedSaving"]) <= 1, "saving is not cost of delay minus cost of acting")
            ok(sum(b["amount"] for b in cost["breakdown"]) - cost["costOfDelay"] < 2, "breakdown does not add up")
            ok(cost["savingRange"]["p10"] <= cost["expectedSaving"] * 1.1 and cost["savingRange"]["p90"] >= cost["expectedSaving"] * 0.9, "range does not bracket the saving")
            ok(finding["numberCheck"] in ("passed", "corrected") and len(finding["reasoningSteps"]) >= 4, "agent steps missing")
            ok(len(finding["explain"]["reasons"]) >= 3, "fewer than 3 reasons")
            rec = await get(f"/calculations/{finding['explain']['calculationId']}", S.CalculationRecord)
            ok(rec["cost"]["expectedSaving"] == cost["expectedSaving"], "calculation record differs from the finding")
            pdf = await c.get(f"/incidents/{finding['incidentId']}/report")
            ok(pdf.status_code == 200 and pdf.content[:4] == b"%PDF", "PDF report failed")
            h = await get(f"/sensors/{finding['explain']['reasons'][0]['evidenceRef']}/history?minutes=5")
            ok(len(h["points"]) > 3, "no sensor history for the key sensor")

            print(f"  [{n}] AI lab: localization, decision, benchmark")
            loc = SL.Localization.model_validate(await get(f"/incidents/{finding['incidentId']}/localization"))
            ok(loc.candidates[0].asset_id == "water-pipe-01" and loc.candidates[0].score > 0.5, f"leak localized to {loc.candidates[0].asset_id}")
            dec = SD.DecisionReport.model_validate(await get(f"/incidents/{finding['incidentId']}/decision"))
            ok({o.id for o in dec.options} >= {"fix_now", "isolate_fix", "wait_6h", "wait_24h", "wait_72h"} and dec.recommended_option_id == "fix_now", "decision options")
            bench = SL.LabBenchmark.model_validate(await get("/lab/benchmark"))
            best = next(d for d in bench.detectors if d.id == bench.best_detector_id)
            ok(best.detection_rate >= 0.9 and best.false_alarms_per_100h <= 5, "benchmark best detector is not good enough")
            SL.QualityNow.model_validate(await get("/quality"))

            print(f"  [{n}] price change recomputes")
            mark = len(st.msgs)
            before = cost["expectedSaving"]
            await c.put("/assumptions", json={"water_price": 14})
            again = (await st.wait(lambda m: m["type"] == "agent_finding" and m["payload"]["id"] == finding["id"], 15, since=mark))["payload"]
            ok(again["cost"]["expectedSaving"] > before, "raising the water price did not raise the saving")
            await c.post("/scenarios/stop", json={"runId": run["runId"]})
            await c.post("/scenarios/reset")

            print(f"  [{n}] substation trip")
            mark = len(st.msgs)
            await c.post("/scenarios/run", json={"scenarioId": "scn-substation-trip", "parameters": {}})
            inc = (await st.wait(lambda m: m["type"] == "incident" and len(m["payload"]["networks"]) >= 2, since=mark))["payload"]
            ok({"electricity", "water"} <= set(inc["networks"]), f"trip incident networks {inc['networks']}")
            await st.wait(lambda m: m["type"] == "agent_finding", since=mark)

            print(f"  [{n}] chat")
            mark = len(st.msgs)
            mid = (await c.post("/chat", json={"message": "What happens if SS-02 trips?"})).json()["messageId"]
            done = (await st.wait(lambda m: m["type"] == "chat_done" and m["payload"]["messageId"] == mid, since=mark))["payload"]
            ok(len(done["agentsConsulted"]) == 3, f"expected 3 agents, got {done['agentsConsulted']}")
            text = "".join(m["payload"]["text"] for m in st.msgs[mark:] if m["type"] == "chat_chunk" and m["payload"]["messageId"] == mid)
            ok(len(text) > 60, "chat answer too short")
            ok("[figure removed]" not in text, "the number checker removed figures from the demo answer")

            print(f"  [{n}] board of agents")
            await c.post("/scenarios/reset")
            await asyncio.sleep(2)
            mark = len(st.msgs)
            await c.post("/scenarios/run", json={"scenarioId": "scn-compound", "parameters": {}})
            dmsg = (await st.wait(lambda m: m["type"] == "board_decision" and len({t["agent"] for t in m["payload"]["tasks"]}) == 3, 240, since=mark))["payload"]
            decision = S.BoardDecision.model_validate(dmsg)
            ok({t.agent for t in decision.tasks} == {"water", "electricity", "cooling"} and decision.conflict, "the board should see three networks competing for two crews")
            ok(all(a.extra_cost >= -0.01 for a in decision.alternatives), "an alternative order beats the chosen one")
            first = sorted(decision.plan.steps, key=lambda x: x.order)[0]
            ok(first.agent == "electricity" and first.start_hour == 1.0, f"the substation trip should be first, got {first.asset_name}")
            rec = await get(f"/calculations/{decision.calculation_id}", S.CalculationRecord)
            ok(abs(rec["cost"]["actNow"] - decision.plan.expected_cost) < 0.01, "board calculation record differs from the plan")
            sess = S.BoardSession.model_validate(await get(f"/board/sessions/{decision.session_id}"))
            for msg in sess.messages:
                ok(numbers_ok(msg.text, msg.facts) and "[" not in msg.text, f"board message quotes a figure that is not in its facts: {msg.text[:80]}")
            ok({m.agent for m in sess.messages} >= {"orchestrator", "water", "electricity", "cooling"}, "not every board member spoke")

            print(f"  [{n}] human approval, crews and the decision log")
            sid = decision.session_id
            bad = await c.post(f"/board/sessions/{sid}/reject", json={"operator": "Eng. Check", "note": " "})
            ok(bad.status_code == 422 and bad.json()["code"] == "reason_required", "a rejection without a reason was accepted")
            mark = len(st.msgs)
            r = await c.post(f"/board/sessions/{sid}/approve", json={"operator": "Eng. Check", "note": "demo check", "secondsPerHour": 2})
            ok(r.status_code == 200 and r.json()["status"] == "executing", f"approval failed: {r.text[:120]}")
            again = await c.post(f"/board/sessions/{sid}/approve", json={"operator": "Eng. Check"})
            ok(again.status_code == 409 and again.json()["code"] == "already_executing", "a second approval was accepted")
            done_msg = (await st.wait(lambda m: m["type"] == "board_decision" and m["payload"]["sessionId"] == sid and m["payload"]["status"] == "completed", 180, since=mark))["payload"]
            final = S.BoardDecision.model_validate(done_msg)
            ok(all(x.state in ("done", "skipped") for x in final.execution), "not every step finished")
            ok(final.review and final.review.decision_hash and not final.review.override, "the approval is not recorded as the board's plan")
            clean = await st.wait(lambda m: m["type"] == "board_decision" and m["payload"]["sessionId"] == sid and all(x.get("confirmedAt") for x in m["payload"]["execution"]), 90, since=mark)
            ok(clean is not None, "the detector did not confirm every repair")
            log = await get(f"/audit?sessionId={sid}")
            acts = [e["action"] for e in log["entries"]]
            for want in ("proposed", "approve", "crew_dispatched", "repair_done", "alert_cleared", "plan_completed"):
                ok(want in acts, f"decision log has no '{want}' line")
            ok(log["verification"]["ok"], "decision log chain is broken")
            ok((await get("/audit/verify"))["ok"], "audit verify failed")
            ok((await c.get("/audit/export")).text.startswith("seq,at,run"), "audit export is not CSV")

            await c.post("/scenarios/reset")
            await asyncio.sleep(1)
            h = await get("/health", S.Health)
            ok(h["activeRuns"] == [], "scenarios still running after reset")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    for n in range(1, a.runs + 1):
        t = time.time()
        print(f"run {n}/{a.runs}")
        try:
            await one_run(a.base, n)
        except Check as e:
            print(f"FAILED on run {n}: {e}")
            return 1
        print(f"run {n} passed in {time.time() - t:.0f}s")
    print(f"all {a.runs} runs passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
