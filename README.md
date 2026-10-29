# UtilityGuard: Multi-Agent Digital Twin

A live 2D digital twin of a district of the New Administrative Capital (Egypt) with three utility networks: **water, electricity and district cooling**. Specialist AI agents watch the networks, an orchestrator coordinates them, and a **board of agents** proposes who gets a repair crew first. A person approves, and the approved plan runs inside the twin.

The one rule the whole project is built on:

> **Every number comes from code, from a named formula and named assumptions. The language model only explains.** Any figure in a model's sentence that is not in the calculation record is removed before it reaches the screen.

The network is **simulated** on the capital's real geography (OpenStreetMap). No real ACUD data is used. Prices are planning assumptions, each with its basis, range and (where one exists) a public reference. Nothing in this project claims to be an official tariff or a measured result on real equipment.

Arabic walkthrough for the committee: [docs/showcase.md](docs/showcase.md). What was built and what is still open: [docs/status.md](docs/status.md).

---

## What it does

| Area | What you get |
| --- | --- |
| **Live twin** | Map of 9 districts with 48 assets and 89 sensors, a reading every 2 s over WebSocket, switchable water / electricity / cooling layers, asset health and failure risk. |
| **Scenarios** | 14 scenarios from a pipe burst to a cascade storm, run and stopped from the Scenario Lab (table below). |
| **Detection** | Rule-based detectors with shared-cause grouping, one incident per root cause that escalates when it turns out to be bigger, creeping-leak rule, and settle time after a restore. Measured on 62 injected runs: precision 1.00, recall 0.89. |
| **Agents** | A water, an electricity and a cooling specialist (LLM) plus an orchestrator (LangGraph). They explain what happened, why they are sure, what it costs and what to do. |
| **Cost and evidence** | Each finding carries a calculation record: formula, inputs, every assumption with basis, range and public reference. Prices can be edited in the app and every finding recomputes. Monte Carlo over the registered ranges gives P10 / P50 / P90. |
| **Leak localization** | Ranks the suspected pipes and highlights them on the map (rank 1 pulses, the others are dashed, click one to fly to it). |
| **Decision Studio** | "Act now or wait?": fix now, wait 6, 24 or 72 hours, with regret, break-even and a decision map over the two assumptions that matter most. |
| **Board of agents** | When two or more networks have an open problem and there are fewer crews than problems, the agents meet. The order is the cheapest of every possible order (exhaustive search up to 6 problems), checked against 300 price scenarios. |
| **Human approval** | The board only proposes. A person approves, approves a different order (priced first, logged as an override) or rejects with a reason. |
| **Crew dispatch** | The approved plan runs in the twin on a demo clock. When a crew finishes, the fault ends and the detector, not the crew, confirms the alert is clear. |
| **Decision log** | Append-only, hash-chained log of every proposal, decision and crew action. One click verifies the chain. CSV export. |
| **Forecast** | Demand forecast per network and district (LightGBM) with a held-out backtest against naive baselines, a coverage-checked uncertainty band, weather and scenario inputs, live correction and a comparison with four other models. |
| **AI lab** | Four detectors benchmarked on the same injected faults, localization accuracy, sensor value and data-quality flags, all measured by running the twin. |
| **Savings advisor** | Monthly savings opportunities, each a named formula over 30 days of baseline history with P10 / P50 / P90, investment and payback: after-hours cooling setback for government buildings, night pressure management (FAVAD), raising the chilled-water supply temperature on mild days, condenser cleaning and planned maintenance of the assets most at risk. |
| **Chat** | Ask the team; the orchestrator routes to the agents it needs. |
| **PDF report** | Per-incident report built on the server. |
| **Works offline** | `LLM_OFFLINE=1` answers from recorded responses; the board and every number work unchanged. |

### Scenarios

| Id | What happens |
| --- | --- |
| `scn-pipe-burst` | A water main bursts. |
| `scn-hidden-leak` | A joint weeps and grows over minutes, too small for a pressure alarm; only the flow balance shows it. |
| `scn-stuck-sensor`, `scn-sensor-drift`, `scn-sensor-dropout` | Telemetry faults: frozen, drifting, silent. |
| `scn-substation-trip` | A substation trips; pumps and cooling plants on it stop. |
| `scn-load-shedding` | The grid cuts a substation for a window, then restores it. |
| `scn-pump-failure`, `scn-cooling-fault` | Equipment faults. |
| `scn-heat-wave`, `scn-holiday`, `scn-iftar-surge` | Demand and weather scenarios (Ramadan evening peak). |
| `scn-storm` | Cascade storm: a heat wave at the working-day peak, cooling hits its limit, protection relays trip the worst-loaded substations one after another. One incident that escalates. |
| `scn-compound` | A main bursts, a chiller fails and a substation trips within the same minute, with fewer crews than problems. The best showcase for the board. |

---

## Architecture

```
                    ┌──────────────────────────── backend (FastAPI) ────────────────────────────┐
 seeded simulator   │  Engine ──tick 2 s──► Runtime ──► Detector ──► incidents / alerts          │
 (physics + events) │     ▲                    │            │                                    │
                    │  events (scenarios,      │            ▼                                    │
 browser ◄──WS──────┤  crews repair faults)    │   Cost engine ◄── Assumptions (editable)        │
 (React, MapLibre)  │     ▲                    │            │                                    │
        │           │     │                    ▼            ▼                                    │
        │           │  Dispatcher ◄── approval ── Board ◄── Specialists + Orchestrator (Groq LLM) │
        │ REST      │     │             (human)    │            │ text only, numbers checked    │
        └───────────┤  Audit log (hash chain)      └──► Decision + provenance record             │
                    └────────────────────────────────────────────────────────────────────────────┘
```

- **Simulation** (`backend/app/sim`): a seeded, deterministic engine with demand profiles (Egyptian Friday and Saturday weekend, holidays), hydraulics, electrical load, chiller physics, weather, protection relays and event propagation. Same seed, same run.
- **Detection** (`backend/app/detection`): turns readings into alerts and incidents; one root cause is one incident.
- **ML** (`backend/app/ml`): LightGBM demand forecast and its evidence, gradient-boosting failure risk (shown as a *risk score*, not a calibrated probability), model comparison.
- **Cost** (`backend/app/cost`): named formulas, the assumption registry with provenance and public references, calculation records, Monte Carlo and sensitivity.
- **Agents** (`backend/app/agents`): Groq models behind a retrying wrapper with a recorded-answer fallback, tools, specialists, orchestrator and the number checker.
- **Board** (`backend/app/board`): the crew scheduling search (`plan.py`), the analysis and provenance record (`analysis.py`), the meeting (`session.py`), human approval and crew dispatch (`dispatch.py`) and the decision log (`audit.py`).
- **Lab** (`backend/app/lab`): fault-injection harness, detector benchmark, localization, sensor value, quality flags.
- **Frontend** (`frontend/src`): React 18, TypeScript, Vite, Zustand, MapLibre, SVG charts, CSS-variable themes (light and dark), English with partial Arabic.

### How a decision is made

1. Each open alert is priced by the cost model: `C_i` EGP per hour it stays open (loss + disruption + secondary effects) and a work time `h_i` (`fix_hours_<kind>`).
2. Crews are free after `response_hours` and need `crew_switch_hours` between jobs. The cost of an order is the sum over problems of `C_i x finish hour + repair cost x late-repair multiplier`.
3. Every order of up to 6 problems is evaluated (vectorised, 720 orders) and the cheapest is chosen. A problem another repair makes moot (an overload on a backup substation relieved by restoring the tripped one) stops costing when that repair finishes.
4. The chosen order is compared with the alarm order, each agent's work first and the worst order, run against 300 price scenarios drawn from the registered ranges (win share, regret, P10 / P50 / P90), and the board reports what hourly cost would change its mind.
5. The agents "speak" in turn. The sentences are composed by code from the figures; a language model may rewrite them in the agent's voice, but the rewrite is rejected if a number or asset differs from the draft. Each message says whether the wording is `llm` or `template`.
6. A person approves, changes the order or rejects. Only then do crews move.

---

## Run it

Requirements: Python 3.12, Node 20+. No GPU, no database.

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # once
cp .env.example .env            # put GROQ_API_KEY in it; without it the agents answer from recorded responses
# frontend (the backend serves the built app)
cd ../frontend && npm install && npm run build                         # once, and after frontend changes
# run
cd ../backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000
```

The first start trains the forecast models (about 45 s) and caches them in `backend/data/models.pkl`. Wait for `http://localhost:8000/api/v1/health` to answer, then open http://localhost:8000.

For frontend development run `npm run dev` in `frontend/` (port 5173, proxies `/api` and `/ws` to 8000). Everything the map needs (buildings, roads, parks, the Green River) is stored in `frontend/public/geo`, so the map works offline.

**Offline mode:** `LLM_OFFLINE=1` makes the agents answer from `backend/data/fallback_cache.json` (a recorded answer is stored the first time a question is answered live). Detection, costs, the board, approval and the log do not need the model.

### Try the best path in two minutes

1. Open the Scenario Lab, run **Three failures at once**. Go to Incidents.
2. Watch the board meet (about 30 s after the failures settle), read the transcript and the crew timeline.
3. Type your name, press **Change the order**, move a job and see the exact extra cost before committing.
4. Press **Approve plan**. Crews start on the demo clock (one planned hour is 20 s), faults clear in the twin, the detector confirms.
5. Open **Decision log** and press **Verify now**.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GROQ_API_KEY` | none | Groq key (`backend/.env`, git-ignored). Never commit it. |
| `GROQ_MODEL_ORCHESTRATOR` / `GROQ_MODEL_SPECIALIST` | `openai/gpt-oss-120b` / `openai/gpt-oss-20b` | Models. |
| `LLM_OFFLINE` | unset | `1` answers from recorded responses. |
| `SIM_SEED` | `42` | Simulation seed. |
| `BOARD_AUTO` | `1` | `0` stops the board from meeting by itself (the button still works). |
| `DISPATCH_SECONDS_PER_HOUR` | `20` | Demo clock: seconds of twin time per planned hour of crew work. |
| `AUDIT_PATH` | `backend/data/audit.jsonl` | Where the decision log is written. |

The Groq free tier allows 200,000 tokens per day. Repeated test runs can use it up; the app then falls back to recorded answers and says so (`Agents: fallback` in the header).

---

## API in short

REST under `/api/v1`, WebSocket at `/ws/stream`. The full contract, every field and every error code is in [docs/contract.md](docs/contract.md).

| Group | Endpoints |
| --- | --- |
| Twin | `GET /health`, `/topology`, `/alerts`, `/incidents`, `/findings`, `/kpis`, `/series/*`, `/sensors/{id}/history`, `POST /alerts/{id}/acknowledge` |
| Scenarios | `GET /scenarios`, `POST /scenarios/run`, `/scenarios/stop`, `/scenarios/reset` |
| Cost | `GET /assumptions`, `PUT /assumptions`, `GET /calculations/{id}`, `GET /incidents/{id}/decision`, `GET /compare`, `GET /savings` |
| Forecast | `GET /forecast`, `/forecast/evidence`, `/metrics` |
| AI lab | `GET /lab/benchmark`, `/lab/localization`, `/lab/quality`, `/lab/sensors`, `POST /lab/trial`, `GET /incidents/{id}/localization`, `GET /quality` |
| Board | `POST /board/convene`, `GET /board/latest`, `/board/sessions/{id}`, `POST /board/sessions/{id}/preview`, `/approve`, `/reject` |
| Decision log | `GET /audit`, `/audit/verify`, `/audit/export` |
| Other | `POST /chat`, `GET /incidents/{id}/report` (PDF), `/sensor-placement`, `/drift` |

WebSocket messages: `snapshot`, `reading`, `asset_status`, `alert`, `incident`, `agent_finding`, `scenario_state`, `forecast_update`, `chat_route`, `chat_chunk`, `chat_done`, `board_state`, `board_message`, `board_decision`, `audit_entry`.

---

## Tests and checks

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q          # 236 tests, about 3.5 minutes
cd frontend && npm run lint && npm test && npm run build          # 47 tests
cd backend && PYTHONPATH=. .venv/bin/python scripts/demo_check.py --runs 1   # the whole demo path against a running server
```

- Every fixture in `fixtures/` is validated against the Pydantic schemas and round-trips losslessly, and the frontend parses the same files. Regenerate them with `scripts/make_fixtures.py` (flags `--approval-only`, `--forecast-evidence-only` rewrite only part).
- `scripts/demo_check.py` drives a running server through the dashboard, a pipe burst, the AI lab, a price change, a substation trip, chat, the board and the approval with the decision log. For a live run set the key; for an offline run start the server with `LLM_OFFLINE=1`.
- The board's vectorised scheduler is tested against a slow reference implementation, and the decision log against tampering in memory and on disk.

---

## Repository layout

```
backend/app/        sim, detection, ml, cost, agents, board, lab, schemas, main (API), runtime
backend/scripts/    demo_check.py, make_fixtures.py
backend/tests/      236 tests
backend/data/       assumptions.json, fallback_cache.json, models.pkl, lab_benchmark.json, audit.jsonl (all git-ignored)
frontend/src/       pages, ui, panels, charts, map, store, api, lib, i18n, design
frontend/public/geo OpenStreetMap layers (ODbL, see ATTRIBUTION.txt)
fixtures/           one JSON sample for every message and endpoint (shared contract)
docs/               spec, contract, cost model, decisions, design system, showcase, status
```

## Documents

| File | What it is |
| --- | --- |
| [docs/status.md](docs/status.md) | What was built, what is measured, what still needs review, and the roadmap. **Start here.** |
| [docs/showcase.md](docs/showcase.md) | Arabic: the features and numbers that will impress the committee, expected questions and a demo script. |
| [docs/contract.md](docs/contract.md) | The API and message contract, section by section. |
| [docs/cost-model.md](docs/cost-model.md) | Every formula, worked examples, the public price check and the assumption table. |
| [docs/wow-ideas.md](docs/wow-ideas.md) | Ranked ideas with their status. |
| [docs/design-system.md](docs/design-system.md) | Visual language and components. |
| [docs/spec.md](docs/spec.md), [docs/plan.md](docs/plan.md), [docs/decisions.md](docs/decisions.md) | The original specification, build plan and decision log (written early; see the status document for what has changed since). |
| [docs/ceo-playbook.md](docs/ceo-playbook.md) | How to review the project as the owner of the utility would. |

## Honest limits

- The data is simulated. Physics is simplified (relations between demand, flow, pressure, temperature and load), and the measured accuracy is measured on that simulation.
- Most prices are planning figures. Electricity and the discount rate have published references (flagged where they differ); water and cooling have no published 2026 figure. All are editable in the app.
- The board decides the order in which to send crews. It does not dispatch real crews, and it does not re-plan in the middle of execution.
- The approver's name is whatever is typed; there is no sign-in. The hash chain shows tampering inside the log file but cannot stop someone with file access from rewriting the whole file.
- Storage is in memory plus JSON files; one simulation runs at a time.
- Map data © OpenStreetMap contributors (ODbL).
