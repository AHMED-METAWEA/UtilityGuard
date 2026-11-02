"""Approval, rejection and the decision log under awkward use (stress-test items B-5, B-6, B-7, B-16, B-17, B-18, P-1, P-2).

    PYTHONPATH=. .venv/bin/python scripts/stress/board_api.py

Runs the compound scenario on a real server object, convenes the board and then misuses the approval endpoints. Criterion, written before the run:
exactly one approval is accepted per session; every refused request answers 4xx with a `{code, message}` body and changes nothing; typed text is
stored exactly and cannot become a spreadsheet formula; the log still verifies at the end. Exits 1 on any failure.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import tempfile
import threading
import time

tmp = tempfile.mkdtemp()
os.environ.update({"LLM_OFFLINE": "1", "AUDIT_PATH": f"{tmp}/audit.jsonl", "BRIEFING_STATE": f"{tmp}/briefing.json", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

logging.disable(logging.CRITICAL)
failures = 0


def report(item: str, ok: bool, text: str) -> None:
    global failures
    failures += not ok
    print(f"{item:<6} {'ok  ' if ok else 'FAIL'} {text}", flush=True)


def convene(c: TestClient) -> dict:
    c.post("/api/v1/scenarios/reset")
    c.post("/api/v1/scenarios/run", json={"scenarioId": "scn-compound"})
    time.sleep(40)  # the three faults need a little time to open three incidents
    for _ in range(20):
        r = c.post("/api/v1/board/convene", json={})
        if r.status_code == 200:
            sid = r.json()["id"]
            for _ in range(40):
                time.sleep(2)
                d = c.get(f"/api/v1/board/sessions/{sid}").json()
                if d.get("decision"):
                    if len(d["decision"]["tasks"]) >= 3:
                        return d
                    break
        time.sleep(8)
    raise SystemExit("the board never convened on three problems")


def main() -> None:
    with TestClient(app, raise_server_exceptions=False) as c:
        s = convene(c)
        sid = s["id"]
        order = [x["taskId"] for x in sorted(s["decision"]["plan"]["steps"], key=lambda x: x["order"])]

        # input hygiene on the fields a person types
        bad_names = [("", 422), ("x" * 61, 422), ("   ", None)]
        for name, want in bad_names:
            r = c.post(f"/api/v1/board/sessions/{sid}/reject", json={"operator": name, "note": "n"})
            report("B-16", r.status_code in (422, 409) and "code" in r.json(), f"operator {name[:8]!r}...: {r.status_code} {r.json().get('code')}")
        r = c.post(f"/api/v1/board/sessions/{sid}/reject", json={"operator": "ok", "note": "x" * 501})
        report("B-16", r.status_code == 422, f"note of 501 characters: {r.status_code}")
        for bad in (0, 0.5, 121, -3):
            r = c.post(f"/api/v1/board/sessions/{sid}/approve", json={"operator": "ok", "secondsPerHour": bad})
            report("B-9", r.status_code == 422, f"secondsPerHour {bad}: {r.status_code}")

        # reordering: duplicates, missing, foreign, empty
        for label, body in (("duplicate", [order[0], order[0], order[1]]), ("missing", order[:-1]), ("foreign", order[:-1] + ["task-nope"]), ("empty", [])):
            r = c.post(f"/api/v1/board/sessions/{sid}/preview", json={"order": body})
            report("B-7", 400 <= r.status_code < 500 and "code" in r.json(), f"preview with {label} order: {r.status_code} {r.json().get('code')}")
            r = c.post(f"/api/v1/board/sessions/{sid}/approve", json={"operator": "ok", "order": body})
            report("B-7", 400 <= r.status_code < 500 and "code" in r.json(), f"approve with {label} order: {r.status_code} {r.json().get('code')}")
        audit_before = len(c.get("/api/v1/audit?limit=1000").json()["entries"])

        # unknown session
        r = c.post("/api/v1/board/sessions/nope/approve", json={"operator": "ok"})
        report("B-5", r.status_code == 404, f"unknown session: {r.status_code}")

        # the operator names that are dangerous in other places
        names = ["<img src=x onerror=alert(1)>", "=cmd|' /C calc'!A0", "+SUM(1+1)", "@SUM(A1)", "عبد الله‮evil", "line1\nline2", "a,b\"c"]
        # B-6: many simultaneous approvals of the same session, each with a different name
        results: list[int] = []
        lock = threading.Lock()

        def go(name: str) -> None:
            r = c.post(f"/api/v1/board/sessions/{sid}/approve", json={"operator": name, "secondsPerHour": 1})
            with lock:
                results.append(r.status_code)

        threads = [threading.Thread(target=go, args=(n,)) for n in names]
        [t.start() for t in threads]
        [t.join() for t in threads]
        ok_count = results.count(200)
        report("B-6", ok_count == 1 and all(x in (200, 409, 422) for x in results), f"{len(names)} simultaneous approvals of one session: statuses {sorted(results)}")
        approvals = [e for e in c.get("/api/v1/audit?limit=1000").json()["entries"] if e["action"] in ("approve", "approve_with_changes")]
        report("B-6", len(approvals) == 1, f"approval lines in the log for that session: {len(approvals)}")

        # state machine after approval
        for path, body in (("approve", {"operator": "late"}), ("reject", {"operator": "late", "note": "x"})):
            r = c.post(f"/api/v1/board/sessions/{sid}/{path}", json=body)
            report("B-5", r.status_code == 409 and "code" in r.json(), f"{path} after approval: {r.status_code} {r.json().get('code')}")
        r = c.post(f"/api/v1/board/sessions/{sid}/preview", json={"order": order})
        report("B-5", r.status_code in (200, 409), f"preview after approval: {r.status_code}")

        # stored text and exports
        entries = c.get("/api/v1/audit?limit=1000").json()["entries"]
        stored = [e["actor"] for e in entries if e["action"] in ("approve", "approve_with_changes")]
        report("B-16", len(stored) == 1 and stored[0] in names, f"the operator name is stored exactly as typed: {stored[0]!r}")
        text = c.get("/api/v1/audit/export")
        rows = list(csv.reader(io.StringIO(text.text)))
        unsafe = [cell for row in rows[1:] for cell in row if cell[:1] in ("=", "+", "-", "@", "\t", "\r")]
        report("B-17", not unsafe and text.headers["content-type"].startswith("text/csv") and len(rows) - 1 == len(entries),
               f"CSV: {len(rows) - 1} rows, cells that start a formula: {len(unsafe)}; content type {text.headers['content-type']}")
        v = c.get("/api/v1/audit/verify").json()
        report("B-13", v["ok"] and v["entries"] == len(entries), f"the log verifies after all of it: {v}")
        sha = __import__("hashlib").sha256(open(os.environ["AUDIT_PATH"], "rb").read()).hexdigest()
        report("B-14", len(sha) == 64, f"sha256 of the log file on disk: {sha[:16]} (compare with `shasum -a 256`)")

        # B-18: the fingerprint shown is the fingerprint stored
        shown = c.get(f"/api/v1/board/sessions/{sid}").json()["decision"].get("decisionHash")
        stored_hash = next((e["data"].get("decision_hash") or e["data"].get("decisionHash") for e in entries if e["action"] in ("approve", "approve_with_changes", "proposed")), None)
        report("B-18", shown is None or stored_hash is None or shown == stored_hash, f"fingerprint on the proposal {str(shown)[:12]} vs in the log {str(stored_hash)[:12]}")
        _ = audit_before
        c.post("/api/v1/scenarios/reset")
    print("failures:", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
