"""The decision log: who proposed, approved, overrode, rejected and what then happened, one line per event.

Append-only. Every line carries the SHA-256 of the line before it, so changing or deleting any line breaks the hash of every
line after it and `verify()` says where. It survives a simulation reset on purpose: a log you can clear is not a log.
Lines are also written to a JSON-lines file, so they survive a restart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.board import AuditEntry, AuditVerification

log = logging.getLogger("utilityguard.audit")

GENESIS = "0" * 64
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "audit.jsonl"


def _body(e: dict) -> dict:
    return {k: e[k] for k in ("seq", "at", "run", "actor", "action", "session_id", "subject", "detail", "data", "prev_hash")}


def digest(e: dict) -> str:
    return hashlib.sha256(json.dumps(_body(e), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def decision_hash(payload: dict) -> str:
    """Hash of a decision exactly as the operator saw it."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path | str | None = None):
        env = os.getenv("AUDIT_PATH")
        self.path = Path(path or env) if (path or env) else DEFAULT_PATH
        self.rows: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            if line.strip():
                try:
                    self.rows.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("audit log has an unreadable line; verify() will report it")
                    self.rows.append({"seq": -1, "unreadable": True})

    def append(self, *, run: str, actor: str, action: str, detail: str, session_id: str | None = None, subject: str | None = None,
               data: dict | None = None, at: datetime | None = None) -> AuditEntry:
        prev = self.rows[-1].get("hash", GENESIS) if self.rows else GENESIS
        clean = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in (data or {}).items() if v is not None}
        row = {"seq": (self.rows[-1].get("seq", 0) + 1) if self.rows else 1,
               "at": (at or datetime.now(timezone.utc)).isoformat(timespec="milliseconds"), "run": run, "actor": actor, "action": action,
               "session_id": session_id, "subject": subject, "detail": detail, "data": clean, "prev_hash": prev}
        row["hash"] = digest(row)
        self.rows.append(row)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            log.exception("could not write the audit log to %s", self.path)
        return AuditEntry(**row)

    def entries(self, session_id: str | None = None, limit: int | None = None) -> list[AuditEntry]:
        rows = [r for r in self.rows if not r.get("unreadable") and (session_id is None or r.get("session_id") == session_id)]
        if limit:
            rows = rows[-limit:]
        return [AuditEntry(**r) for r in rows]

    def verify(self) -> AuditVerification:
        prev = GENESIS
        for i, r in enumerate(self.rows):
            if r.get("unreadable") or r.get("prev_hash") != prev or r.get("hash") != digest(r) or r.get("seq") != i + 1:
                return AuditVerification(ok=False, entries=len(self.rows), broken_at=i + 1, head=prev)
            prev = r["hash"]
        return AuditVerification(ok=True, entries=len(self.rows), head=prev if self.rows else None)

    def csv(self) -> str:
        import csv
        import io
        out = io.StringIO()
        w = csv.writer(out)
        safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v  # noqa: E731 - no spreadsheet formulas from typed text
        w.writerow(["seq", "at", "run", "actor", "action", "session_id", "subject", "detail", "data", "hash"])
        for r in self.rows:
            if not r.get("unreadable"):
                w.writerow([safe(c) for c in (r["seq"], r["at"], r["run"], r["actor"], r["action"], r.get("session_id") or "", r.get("subject") or "",
                                              r["detail"], json.dumps(r["data"], sort_keys=True), r["hash"])])
        return out.getvalue()
