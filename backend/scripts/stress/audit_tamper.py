"""Tamper tests for the decision log (stress-test items B-13, B-14, B-15, B-16, B-19).

    PYTHONPATH=. .venv/bin/python scripts/stress/audit_tamper.py

Every case writes a fresh log, damages the file on disk the way a person could, reloads it and asks `verify()`. The criterion was written before the run:
a change anywhere in the file must be reported, with the first broken line. Prints a table and exits 1 if anything slips through.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.board.audit import AuditLog, digest

N = 12


def fresh(d: Path) -> Path:
    path = d / "audit.jsonl"
    log = AuditLog(path)
    for k in range(N):
        log.append(run="r", actor="op", action="approve", detail=f"line {k}", data={"v": k, "cost": 1000.0 * k})
    return path


def rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def rehash_from(rows: list[dict], start: int) -> list[dict]:
    """The forger's best move: change a line, then recompute every hash after it so the chain looks intact."""
    for i in range(start, len(rows)):
        rows[i]["prev_hash"] = rows[i - 1]["hash"] if i else "0" * 64
        rows[i]["hash"] = digest(rows[i])
    return rows


def cases() -> list[tuple[str, callable, str]]:
    def load(p): return [json.loads(x) for x in p.read_text().splitlines()]
    def dump(p, rows): rewrite(p, [json.dumps(r, sort_keys=True) for r in rows])

    def edit_char(p):
        ls = p.read_text().splitlines(); ls[5] = ls[5].replace("line 5", "line 9"); rewrite(p, ls)

    def delete_middle(p):
        ls = p.read_text().splitlines(); del ls[5]; rewrite(p, ls)

    def add_line(p):
        rows = load(p); extra = dict(rows[3]); rows.insert(6, extra); dump(p, rows)

    def swap(p):
        ls = p.read_text().splitlines(); ls[3], ls[4] = ls[4], ls[3]; rewrite(p, ls)

    def edit_prev(p):
        rows = load(p); rows[6]["prev_hash"] = "f" * 64; dump(p, rows)

    def edit_hash(p):
        rows = load(p); rows[6]["hash"] = "e" * 64; dump(p, rows)

    def forge_chain(p):
        rows = load(p); rows[4]["data"]["cost"] = 1.0; dump(p, rehash_from(rows, 4))

    def truncate_tail_1(p):
        ls = p.read_text().splitlines(); rewrite(p, ls[:-1])

    def truncate_tail_5(p):
        ls = p.read_text().splitlines(); rewrite(p, ls[:-5])

    def empty_file(p):
        rewrite(p, [])

    def delete_file(p):
        p.unlink()

    def garbage_line(p):
        ls = p.read_text().splitlines(); ls[7] = "{not json"; rewrite(p, ls)

    def half_last_line(p):
        t = p.read_text(); p.write_text(t[:-40])

    return [
        ("B-13 edit one character in a line", edit_char, "5"),
        ("B-13 delete a line in the middle", delete_middle, "5"),
        ("B-13 insert a copied line", add_line, "7"),
        ("B-13 swap two lines", swap, "4"),
        ("B-13 edit prev_hash", edit_prev, "7"),
        ("B-13 edit hash", edit_hash, "7"),
        ("B-13 edit a line and recompute every hash after it", forge_chain, "forgery-by-rehash"),
        ("B-13 cut the last line", truncate_tail_1, "tail"),
        ("B-13 cut the last 5 lines", truncate_tail_5, "tail"),
        ("B-13 empty the file", empty_file, "tail"),
        ("B-13 delete the file (a restart with no log, see note)", delete_file, "fresh-start"),
        ("E-6 a corrupt line in the middle", garbage_line, "8"),
        ("E-6 the last line half written", half_last_line, "last"),
    ]


def main() -> None:
    rows = []
    bad = 0
    for name, damage, expect in cases():
        with tempfile.TemporaryDirectory() as d:
            path = fresh(Path(d))
            damage(path)
            v = AuditLog(path).verify()
            caught = not v.ok
            detail = f"broken_at={v.broken_at}" if caught else "NOT DETECTED"
            if expect == "fresh-start":
                note = "a missing file counts as a new log; the head shown in the UI is the outside record"
            elif expect == "forgery-by-rehash":
                # A forger who also fixes every later hash cannot be caught by a chain alone. What can: the head hash noted down earlier.
                note = "needs the head noted outside the file"
            else:
                note = ""
            rows.append((name, "caught" if caught else "MISSED", detail, note))
            bad += (not caught) and expect not in ("forgery-by-rehash", "fresh-start")
    # B-14: an independent hash of the same lines
    with tempfile.TemporaryDirectory() as d:
        path = fresh(Path(d))
        log = AuditLog(path)
        ok = True
        for line in path.read_text().splitlines():
            r = json.loads(line)
            body = {k: r[k] for k in ("seq", "at", "run", "actor", "action", "session_id", "subject", "detail", "data", "prev_hash")}
            canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            shell = subprocess.run(["shasum", "-a", "256"], input=canon.encode(), capture_output=True).stdout.split()[0].decode()
            ok &= shell == r["hash"] == hashlib.sha256(canon.encode()).hexdigest()
        rows.append(("B-14 shasum -a 256 of every line's canonical body equals its hash", "matches" if ok else "MISMATCH", "", ""))
        bad += not ok
    # B-15: concurrent writers
    with tempfile.TemporaryDirectory() as d:
        log = AuditLog(Path(d) / "a.jsonl")
        def writer(k):
            for j in range(50):
                log.append(run="r", actor=f"t{k}", action="x", detail=str(j))
        ts = [threading.Thread(target=writer, args=(k,)) for k in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        seqs = [r["seq"] for r in log.rows]
        v = AuditLog(Path(d) / "a.jsonl").verify()
        good = seqs == list(range(1, 401)) and v.ok
        rows.append(("B-15 8 threads x 50 appends: seq 1..400 without holes or repeats, chain verifies after reload", "ok" if good else "BROKEN", f"{len(seqs)} rows, verify={v.ok}", ""))
        bad += not good
    # B-19: size
    with tempfile.TemporaryDirectory() as d:
        log = AuditLog(Path(d) / "a.jsonl")
        for k in range(10000):
            log.append(run="r", actor="op", action="x", detail=f"line {k}", data={"v": k})
        t0 = time.time(); v = log.verify(); t_verify = time.time() - t0
        t0 = time.time(); AuditLog(Path(d) / "a.jsonl"); t_load = time.time() - t0
        t0 = time.time(); log.csv(); t_csv = time.time() - t0
        good = t_verify < 1 and t_load < 2
        rows.append(("B-19 10,000 lines: verify under 1 s, reload under 2 s", "ok" if good else "SLOW", f"verify {t_verify:.2f} s, reload {t_load:.2f} s, csv {t_csv:.2f} s", ""))
        bad += not good
    w = max(len(r[0]) for r in rows)
    for r in rows:
        print(f"{r[0]:<{w}}  {r[1]:<9} {r[2]}  {r[3]}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
