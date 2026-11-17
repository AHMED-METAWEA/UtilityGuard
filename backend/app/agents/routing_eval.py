"""How often the router picks the right agents, on questions labelled by hand (tests/data/routing_eval.json).

A route is right when it holds every agent the question needs (`must`) and nothing outside `must` + `may`. Four routers are scored on the
same questions: the rules alone (keywords and network links), the language model alone, the model with the network links as a floor
(what the orchestrator runs), and that floor alone. The model is asked live, without the recorded fallback, so a failed call counts as a miss.

    PYTHONPATH=. .venv/bin/python -m app.agents.routing_eval            # rules only, offline
    PYTHONPATH=. .venv/bin/python -m app.agents.routing_eval --live     # also the language model (needs GROQ_API_KEY)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.agents.llm import llm
from app.agents.orchestrator import model_route, rule_route, topology_route, with_floor
from app.agents.tools import find_asset

SET = Path(__file__).resolve().parents[2] / "tests" / "data" / "routing_eval.json"


def questions() -> list[dict]:
    return json.loads(SET.read_text())["questions"]


def verdict(q: dict, agents: list[str]) -> str:
    got, must, ok = set(agents), set(q["must"]), set(q["must"]) | set(q["may"])
    if not must <= got:
        return "missing"
    return "extra" if not got <= ok else "right"


def score(rows: list[dict], key: str) -> dict:
    v = [r[key]["verdict"] for r in rows]
    n = len(v)
    return {"right": v.count("right"), "missing": v.count("missing"), "extra": v.count("extra"), "n": n}


async def run(rt, live: bool, pause: float = 0.0) -> list[dict]:
    rows = []
    if live:  # ask the model itself: no recorded answer stands in, and nothing is recorded
        saved_cache, saved_save = llm.cache, llm._save
        llm.cache, llm._save = {}, lambda: None
    try:
        for q in questions():
            ar = q["lang"] == "ar"
            asset = find_asset(q["question"], rt)
            floor = topology_route(rt, asset, ar)
            row = {"id": q["id"], "lang": q["lang"], "question": q["question"], "must": q["must"], "may": q["may"]}
            rules = [r.agent for r in rule_route(q["question"], rt, asset, ar)]
            row["rules"] = {"agents": rules, "verdict": verdict(q, rules)}
            if live:
                await asyncio.sleep(pause)  # the free tier allows a few thousand tokens a minute
                steps, fb = await model_route(q["question"], q["lang"], rt)
                model = [] if fb else [r.agent for r in steps]
                merged = [r.agent for r in with_floor(steps, floor)] if model else rules
                row["model"] = {"agents": model, "verdict": verdict(q, model), "failed": fb}
                row["model_with_links"] = {"agents": merged, "verdict": verdict(q, merged)}
            rows.append(row)
    finally:
        if live:
            llm.cache, llm._save = saved_cache, saved_save
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--pause", type=float, default=8.0, help="seconds between two model calls, to stay under the rate limit")
    args = ap.parse_args()
    from app.runtime import Runtime
    rows = asyncio.run(run(Runtime(42), args.live, args.pause))
    keys = ["rules"] + (["model", "model_with_links"] if args.live else [])
    summary = {k: {lang: score([r for r in rows if lang == "all" or r["lang"] == lang], k) for lang in ("all", "en", "ar")} for k in keys}
    for k in keys:
        s = summary[k]["all"]
        print(f"{k:18s} right {s['right']}/{s['n']}  missing an agent {s['missing']}  extra agent {s['extra']}"
              f"   (en {summary[k]['en']['right']}/{summary[k]['en']['n']}, ar {summary[k]['ar']['right']}/{summary[k]['ar']['n']})")
    for r in rows:
        bad = [k for k in keys if r[k]["verdict"] != "right"]
        if bad:
            print(f"  {r['id']} {r['question']}  must={r['must']} may={r['may']}  " + "  ".join(f"{k}={r[k]['agents']}({r[k]['verdict']})" for k in bad))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
