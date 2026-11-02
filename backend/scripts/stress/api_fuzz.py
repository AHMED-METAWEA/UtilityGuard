"""Throws awkward input at every endpoint of the OpenAPI document (stress-test items API-1, API-2, API-3, K-4, D-15, L-5).

    PYTHONPATH=. .venv/bin/python scripts/stress/api_fuzz.py [--rounds 1]

Criterion (written before the run): no request may answer 5xx, and every 4xx must have the `{code, message}` body. No network, no language model,
no Telegram, and the decision log goes to a temporary file. The assumptions file is put back at the end.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys
import logging
import tempfile
from urllib.parse import quote
from collections import Counter

tmp = tempfile.mkdtemp()
os.environ.update({"LLM_OFFLINE": "1", "AUDIT_PATH": f"{tmp}/audit.jsonl", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "BRIEFING_STATE": f"{tmp}/briefing.json"})

from fastapi.testclient import TestClient  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

from app.main import app, service  # noqa: E402

STRINGS = ["", " ", "x", "abc", "-1", "0", "1e309", "NaN", "Infinity", "null", "true", "[]", "{}", "../../etc/passwd", "%2e%2e%2f", "..\\..", "a" * 4000,
           "ünï©ödé", "مرحبا", "‮\u0000", "<img src=x onerror=alert(1)>", "' OR 1=1 --", "=cmd|' /C calc'!A0", "{{7*7}}", "${jndi:ldap://x}", "999999999999999999999",
           "zone-gov", "water-pipe-01", "water", "all", "scn-pipe-burst", "ar", "en"]
NUMBERS = [0, -1, 1, 2, 0.5, -0.0, 1e308, -1e308, 10**30, 3.14159, 1e-300, 12345678901234567890]
SPECIAL = [None, True, False, [], {}, [1], {"a": 1}, float("nan"), float("inf")]


def values(schema: dict | None) -> list:
    t = (schema or {}).get("type")
    if "anyOf" in (schema or {}):
        return list(itertools.chain.from_iterable(values(s) for s in schema["anyOf"]))[:12]
    if t == "string":
        return STRINGS + SPECIAL
    if t in ("integer", "number"):
        return NUMBERS + SPECIAL
    if t == "boolean":
        return [True, False, "yes", 2] + SPECIAL
    if t == "array":
        return [[], [None], ["x"] * 50, [1, "a", None], {"a": 1}] + SPECIAL
    return STRINGS[:8] + NUMBERS[:4] + SPECIAL


def body_variants(op: dict, spec: dict, rnd: random.Random) -> list:
    out = [None, {}, [], "text", 7, None]
    content = (op.get("requestBody") or {}).get("content", {}).get("application/json")
    if not content:
        return out
    schema = content["schema"]
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
    props = schema.get("properties", {})
    for _ in range(40):
        body = {}
        for name, ps in props.items():
            if "$ref" in ps:
                ps = spec["components"]["schemas"][ps["$ref"].split("/")[-1]]
            if rnd.random() < 0.85:
                body[name] = rnd.choice(values(ps))
        out.append(body)
    if schema.get("additionalProperties"):
        out += [{k: rnd.choice(NUMBERS + SPECIAL[:5]) for k in rnd.sample(["water_price", "elec_tariff", "customers_zone_gov", "response_hours", "planned_factor", "x"], 3)} for _ in range(30)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()
    rnd = random.Random(11)
    results: Counter = Counter()
    bad: list[str] = []
    shapes = 0
    sent = 0
    with TestClient(app, raise_server_exceptions=False) as c:
        spec = c.get("/openapi.json").json()
        skip = {"/api/v1/briefing/send"}
        for path, ops in spec["paths"].items():
            if path in skip:
                continue
            for method, op in ops.items():
                if method not in ("get", "post", "put", "delete", "patch"):
                    continue
                params = op.get("parameters", [])
                path_params = [p["name"] for p in params if p["in"] == "path"]
                query = [p for p in params if p["in"] == "query"]
                for _ in range(args.rounds * 25):
                    url = path
                    for name in path_params:
                        url = url.replace("{" + name + "}", quote(rnd.choice(STRINGS), safe="") if rnd.random() < 0.8 else "x")
                    q = {p["name"]: rnd.choice(values(p.get("schema"))) for p in query if rnd.random() < 0.8}
                    q = {k: v for k, v in q.items() if v is not None and not isinstance(v, (list, dict))}
                    kwargs = {"params": q}
                    if method in ("post", "put", "patch"):
                        body = rnd.choice(body_variants(op, spec, rnd))
                        kwargs["content"] = json.dumps(body, allow_nan=True).encode() if body is not None else b""
                        kwargs["headers"] = {"content-type": "application/json"}
                    r = c.request(method.upper(), url, **kwargs)
                    sent += 1
                    results[r.status_code // 100] += 1
                    if r.status_code >= 500:
                        bad.append(f"{r.status_code} {method.upper()} {path} {str(q)[:80]} {str(kwargs.get('content', b''))[:120]}")
                    elif r.status_code >= 400 and "application/json" in r.headers.get("content-type", ""):
                        j = r.json()
                        if not (isinstance(j, dict) and "code" in j and "message" in j):
                            shapes += 1
                            bad.append(f"shape {r.status_code} {method.upper()} {path} {str(j)[:100]}")
        # traversal against the file-like routes
        for ref in ["../README.md", "%2e%2e/README.md", "..%2f..%2fetc%2fpasswd", "a/b/../../../etc/passwd", "x" * 5000, "%00", "ünï"]:
            r = c.get("/api/v1/why/" + (ref if "\x00" not in ref else quote(ref)))
            sent += 1
            if r.status_code >= 500 or "root:" in r.text:
                bad.append(f"why/{ref[:30]} -> {r.status_code}")
        service.A.reset()
    print(f"sent {sent} requests: " + ", ".join(f"{k}xx {v}" for k, v in sorted(results.items())))
    seen = Counter(b.split(" ", 3)[0] + " " + b.split(" ", 3)[1] + " " + b.split(" ", 3)[2] for b in bad)
    print(f"5xx or wrong error shape: {len(bad)}")
    for k, n in seen.most_common(20):
        print(f"  {n:>3} x {k}")
    for b in bad[:8]:
        print("   e.g.", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
