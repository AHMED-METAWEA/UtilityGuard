# UtilityGuard backend

FastAPI + Pydantic v2 service that simulates the three utility networks, detects faults, prices them, runs the AI agents and the board, and streams everything over WebSocket. Python 3.12.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                   # GROQ_API_KEY=... (never commit it)
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000
```

The first start trains the forecast models (about 45 s, cached in `data/models.pkl`). The server also serves the built frontend from `../frontend/dist`.
Run commands from this folder with `PYTHONPATH=.` and the `.venv` interpreter.

## Modules

| Path | Responsibility |
| --- | --- |
| `app/main.py` | REST routes under `/api/v1`, the `/ws/stream` WebSocket, error format `{code, message}`. |
| `app/runtime.py` | Owns the engine, ticks every 2 s, broadcasts to clients, runs tick hooks (ML, detector, board dispatcher). `clone()` makes an isolated sandbox for what-if runs. |
| `app/sim/` | `topology.py` (9 zones, 48 assets, 89 sensors, 53 links on the real NAC layout) and `engine.py` (seeded physics, events, protection relays, `Engine.repair`). |
| `app/scenarios.py` | The 15 scenarios and the events each one creates. |
| `app/detection/` | Detectors, shared-cause grouping, incidents and alerts, escalation, creeping-leak rule, settle time after a restore. |
| `app/ml/` | `__init__.py` trains the LightGBM demand forecast and the failure model; `service.py` serves forecasts and evidence; `comparison.py` compares five forecast models; `earlywarn.py` the early warning (peer comparison of condition signals, priced with the cost model); `evaluate.py` scores the detector. |
| `app/cost/` | Named formulas, the assumption registry (`DEFAULTS`, provenance, ranges, `references.py` public references), calculation records, Monte Carlo and sensitivity. |
| `app/agents/` | `llm.py` (Groq with retries and a recorded-answer fallback), `specialists.py`, `orchestrator.py` (LangGraph), `tools.py`, `checker.py` (the number checker), `service.py` (analysis pipeline). |
| `app/board/` | `plan.py` crew scheduling search, `analysis.py` robustness and provenance, `session.py` the meeting, `dispatch.py` approval and crew dispatch, `audit.py` the hash-chained decision log. |
| `app/lab/` | Fault-injection harness and the benchmarks behind the AI lab page. |
| `app/why.py` | "Why this number?": `GET /why/{ref}` builds the formula, worked substitution and sourced inputs for dashboard tiles, asset health and risk, warnings, savings and forecast accuracy. |
| `app/agents/guardrail.py` | The "try to fool the AI" probe: real incident data plus the person's claim go to the model; the number checker rules on every figure. |
| `app/decision.py` | Decision Studio: act now or wait, regret, break-even, decision map. |
| `app/savings.py`, `app/dashboard.py`, `app/extras.py` | Savings advisor, KPIs and series, PDF report, compare, sensor placement, drift. |
| `app/schemas/` | Pydantic models for every REST and WebSocket shape. camelCase on the wire. |

## Rules to keep

- **Numbers come from code.** An agent's text is checked by `agents/checker.py`; a figure that is not in the calculation record is removed. The board's sentences are composed by code and a model rewrite is accepted only if it carries exactly the same figures and assets.
- **Contract first.** Change `app/schemas`, then `fixtures/` (`scripts/make_fixtures.py`), then `docs/contract.md`, then the frontend types. `tests/test_fixtures.py` fails if they drift.
- **Determinism.** The engine is seeded (`SIM_SEED`). Tests step the engine with a fixed clock instead of sleeping.
- **pydantic camelCase gotcha:** `leak_m3h` becomes `leakM3H` and `per_100h` becomes `Per100H` on the wire.
- **The board changes nothing without a human.** Only `Dispatcher.approve` starts crews, and every step is written to the decision log.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GROQ_API_KEY` | none | Without it the agents answer from `data/fallback_cache.json`. |
| `GROQ_MODEL_ORCHESTRATOR`, `GROQ_MODEL_SPECIALIST` | `openai/gpt-oss-120b`, `openai/gpt-oss-20b` | Models. |
| `LLM_OFFLINE` | unset | `1` forces recorded answers. Use it for tests and when the daily Groq quota (200k tokens on the free tier) is spent. |
| `SIM_SEED` | `42` | Seed. |
| `BOARD_AUTO` | `1` | `0` disables the automatic meeting (20 s after the set of open problems stops changing). |
| `DISPATCH_SECONDS_PER_HOUR` | `20` | Demo clock for approved plans. |
| `AUDIT_PATH` | `data/audit.jsonl` | Decision log file. Tests point it at a temporary folder. |

## Data files (git-ignored)

`data/assumptions.json` (prices you edit in the app), `data/models.pkl` (trained models, retrained when `MODEL_VERSION` changes or the window is stale), `data/lab_benchmark.json` (AI lab results, about 40 s to rebuild), `data/fallback_cache.json` (recorded LLM answers), `data/audit.jsonl` (decision log).

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q                              # 257 tests, about 8 minutes
PYTHONPATH=. .venv/bin/python -m pytest tests/test_board_approval.py -q # approval, dispatch, audit chain
PYTHONPATH=. .venv/bin/python scripts/demo_check.py --runs 1           # full demo path against a running server
PYTHONPATH=. .venv/bin/python scripts/make_fixtures.py                 # regenerate ../fixtures (also `--approval-only`, `--insight-only`, `--forecast-evidence-only`)
```

| File | Covers |
| --- | --- |
| `test_fixtures.py`, `test_api.py` | Every fixture and live endpoint against the schemas, determinism, burst propagation. |
| `test_cost.py`, `test_cost_provenance.py` | Formulas, worked examples, assumptions with basis and references. |
| `test_detection.py` | Each scenario is detected, grouped, escalated and settles (iftar, hidden leak, drift is not a leak, load shedding, storm). |
| `test_forecast.py` | Forecast evidence, baselines, band coverage, model comparison and its bootstrap. |
| `test_lab.py`, `test_decision.py`, `test_savings.py`, `test_checker.py` | AI lab, Decision Studio, savings advisor, the number checker. |
| `test_board.py` | The vectorised scheduler against a slow reference, Smith's rule, relief, guards on the model's wording, the storm. |
| `test_early_warning.py` | The warning arrives while the pump still runs and before the failure alarm (about four minutes), stays quiet on a quiet twin, a burst and a power cut, prices the choice, and every figure in its text is one of its numbers. |
| `test_why_guardrail.py` | Every KPI, asset, opportunity and network has an explanation; unknown refs are a clean 404; the guardrail catches an invented figure, matches a true one, and says when a planted draft stands in. |
| `test_board_approval.py` | Approve, override and preview, reject, superseded and stale proposals, claimed problems, alert id changes, the hash chain and tampering, CSV safety, reset. |

When running tests in a loop, use `LLM_OFFLINE=1` so the Groq quota is not spent.

## Decision log file format

One JSON object per line: `seq, at, run, actor, action, session_id, subject, detail, data, prev_hash, hash`. `hash` is the SHA-256 of the canonical JSON of the line without `hash`, which includes `prev_hash`; the first line links to 64 zeros. Verify with `GET /api/v1/audit/verify` or `AuditLog.verify()`.
