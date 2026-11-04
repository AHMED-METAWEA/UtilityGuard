# UtilityGuard: the whole project in one page

*A live digital twin of the water, electricity and cooling networks of the New Administrative Capital (NAC), Egypt, and its link to a healthcare-investment project. Read this first; every section points to the file that has the detail.*

## 1. What it is

UtilityGuard is a software model of a city district's three utility networks (**water, electricity and district cooling**) that runs live in a browser. It simulates the networks on the real map of the capital, detects faults, prices what each fault costs per hour, and proposes **which repair crew should go where first**. A person approves the plan, and the approved plan runs inside the model.

It is built from the point of view of the **operator** of the city's utilities: the person who has to keep services running and spend repair money well.

**The one rule the project is built on:**

> Every number comes from code, from a named formula and named assumptions. The language model only explains. Any figure in a model's sentence that is not in the calculation record is removed before it reaches the screen.

**Everything is simulated.** No real ACUD data is used; the networks run on OpenStreetMap geography with physics-style relations between demand, flow, pressure, load and temperature. Prices are planning assumptions with their basis and range. Nothing here is a measured result on real equipment or an official tariff.

## 2. The problem it answers

An operator with a few crews and several things breaking at once has three questions: *what is happening, what does waiting cost, and what do I do first?* UtilityGuard answers them with numbers that can be checked:

1. **Detect.** Readings arrive every 2 seconds from 89 sensors on 48 assets; detectors turn them into alerts, and one root cause becomes one incident.
2. **Price.** A cost model turns each incident into EGP per hour of delay, with a P10 / P50 / P90 range from 800 runs over the registered assumption ranges.
3. **Decide.** When there are fewer crews than problems, a board of AI agents meets. The order it proposes is the cheapest of every possible order (exhaustive search), compared against the alarm order and the worst order over 300 price scenarios.
4. **Approve.** The board only proposes. A person approves, changes the order (priced first, logged as an override) or rejects with a reason.
5. **Execute and prove.** Crews run on a demo clock, the detector (not the crew) confirms the fault is gone, and every step is written to a tamper-evident, hash-chained decision log.

## 3. What is in it

| Area | What you get |
| --- | --- |
| Live twin | Map of 9 districts, 48 assets, 89 sensors; water / electricity / cooling layers; asset health and failure risk |
| Scenarios | 15 scenarios from a pipe burst to a cascade storm and a three-failure compound, run from the Scenario Lab |
| Detection | Rule-based detectors; measured recall 93% over 212 injected faults, no false alarm in 172.5 clean hours; the one fault it mostly misses is a leak below about 24 m3/h |
| Agents | A water, an electricity and a cooling specialist plus an orchestrator (LangGraph); they explain what happened, how sure they are, what it costs and what to do |
| Cost and evidence | Every finding carries a calculation record (formula, inputs, assumptions with basis, range and public reference); prices are editable and everything recomputes |
| Decision Studio | "Act now or wait?": fix now or wait 6, 24 or 72 hours, with regret and break-even |
| Board of agents | Crew-scheduling meeting, human approval, dispatch, decision log |
| Early warning | A wearing pump is flagged minutes before it fails, priced as planned maintenance against an unplanned failure |
| Forecast | Demand per network and district (LightGBM, 270 days of history); error 2.5% water, 1.0% electricity, 3.2% cooling on held-out days, with an uncertainty band and a reliability verdict |
| AI lab | Four detectors benchmarked on the same faults, plus leak localization and data-quality flags |
| Savings advisor | Monthly savings opportunities, each a named formula with range, investment and payback |
| Morning briefing | What needs attention today, in English and Arabic, composed by code and sent to Telegram |
| Why this number? | Every tile and score opens its formula, inputs and editable assumptions |
| Try to fool the AI | Send an unsupported claim and watch the checker mark each figure verified or removed |
| Arabic or English | The agents speak the language the person chooses (findings, chat, board meeting, Telegram alerts), and the incident report is a PDF in either language; every Arabic figure passes the same number check as the English one |
| Build here | Put a hospital, an expansion or a clinic in a zone; the twin is read with and without it on summer weekdays, in a heat wave and with a substation lost, and the page shows the share of days in breach per network and the cost with the utility upgrades needed |
| Healthcare link | The twin's outage costs next to the healthcare project's environment and hospital figures (section 6) |

## 4. Why the numbers can be trusted

- **Code decides, the model explains.** Detection, costs, the crew order, approval and the log do not need a language model. With `LLM_OFFLINE=1` the app runs from recorded answers and every number is unchanged.
- **A number checker** reads every model sentence (including Arabic digits and spelled-out numbers) and removes any figure that is not in the calculation record.
- **Provenance.** Each assumption states its basis, range and, where one exists, a public reference; defaults that disagree with a reference are flagged, not silently changed.
- **Measured, not claimed.** Detection, forecast accuracy and the detector benchmark are measured by running the twin (matched pairs: same seed with and without the fault) and published with intervals under `docs/measurements`.
- **Reproducible.** The simulator is seeded; the same seed gives the same run, and the cost engine's results are re-derived by an independent script in the stress tests.
- **Tamper-evident log.** Each line carries the SHA-256 of the line before it plus a separate head anchor; changing, deleting or truncating the log is detected.
- **Tests.** 397 backend and 84 frontend tests pass; the shared JSON fixtures are validated against the schemas and parsed by the frontend.

## 5. Architecture

```
                 backend (FastAPI, Python 3.12)
 seeded    ┌───────────────────────────────────────────────────────────┐
 simulator │ Engine ─2 s tick─► Runtime ─► Detector ─► alerts/incidents │
 (physics, │    ▲                                │                      │
 events)   │ crews repair faults                 ▼                      │       browser
           │    ▲                          Cost engine ◄─ Assumptions   │ REST/WS (React, TypeScript,
           │ Dispatcher ◄─ human approval ─ Board ◄─ Specialists +      ├──────── MapLibre, Zustand)
           │    │                            │       Orchestrator (LLM) │
           │ Audit log (hash chain)          └─► Decision + provenance  │
           └───────────────────────────────────────────────────────────┘
```

Backend: simulation, detection, ML (LightGBM forecast, failure risk), cost, agents (Groq models behind a retrying wrapper), board, lab. Frontend: React 18, Vite, SVG charts, light and dark themes, English with partial Arabic. No database, no GPU. Detail: `README.md`, `docs/contract.md`, `docs/cost-model.md`.

## 6. The link with the healthcare-investment project

### Two projects, two points of view

The doctor combined this project with another team's: *AI-powered utilization of environmental measurements on healthcare investment planning* (a Streamlit dashboard, simulated data, 15 districts, January 2025 to September 2026). The two answer different questions about the same city:

| | UtilityGuard | Healthcare-investment project |
| --- | --- | --- |
| Point of view | The **operator** who keeps utilities running | The **investor** deciding where to put healthcare capital |
| Question | What breaks, what does a delay cost, who goes first? | Where is health demand highest, where is capacity short, which package should be funded? |
| Measures | Outage cost (EGP), unserved load, leak rate, cooling deficit | Environmental stress (air, waste, water pollution, heat), health demand, hospital pressure, package costs |

Neither can answer the other's question. The twin does not model air, waste or water chemistry; their model assumes every hospital is always working.

### The rules of the link

1. **Nothing is deleted and neither project edits the other's files.** The link is a set of CSV tables with a manifest (seed, commit, SHA-256 of every file) in `integration/health/`.
2. **Their data is shown as theirs.** Their files are copied unchanged with the SHA-256 of the source; the twin did not generate or alter any of it.
3. **Numbers from the two sides are never added together.** A combined score would need weights of our own between EGP and a 0 to 100 score, which cannot be defended. They sit side by side, and each side is read against the median district.
4. **The key is `district_key` (1001 to 1015).** Matching to the twin's zones is by name and kind: 4 districts match one of our zones directly (R2, CBD, Government, Arts and Culture), 10 are read as "the same kind of district", 1 (the central park) has no counterpart.

### What was built

- **Map layers.** Their 15 districts and 81 facilities (30 hospitals) on the live map, coloured by their own stress scores (overall, air, waste, water pollution, heat) and sized by residents, month selectable. Their coordinates are approximate, so each district is drawn on the centre of the zone it is matched to and the tooltip says the position is approximate.
- **Failure tables for them** (`integration/health/out/`): for each of 24 failed assets, what the detector measured and what it costs (`scenarios.csv`), which districts are hit, which hospitals a failure reaches and how sure that match is (`hospital_exposure.csv`), ranked reliability packages with placeholder costs (`reliability_packages.csv`), and their districts file moved onto the twin's zones (`districts_on_twin_map.csv`).
- **Their engine, unchanged.** Their own what-if engine is run read-only once per district with their default settings (`integration/health/extract_packages.py`), giving hospital pressure and the effect and 5-year cost of their four packages (A new centre plus prevention, B expand capacity, C transport and triage, D prevention only). The result was checked against their dashboard (district R1: pressure 1.23 before and 0.96 after package A (their commit 9179942), 40.5 M EGP).
- **The joint table** (Executive page, `joint_decision.csv`): per district, the twin's outage cost, their environmental stress and hospitals, their hospital pressure and best package. Each district falls in one of four groups relative to the median district (outage cost higher, environment higher, both higher, both lower), or has no counterpart in the twin.
- **One-command run.** `./run_all.sh` starts the twin (port 8000) and their Streamlit dashboard (port 8501); the **Healthcare** tab shows their dashboard inside the twin. Their code is cloned into a git-ignored folder and never edited.
- **Workflow impact** (`integration/health/workflow-impact.md`): where each of the twin's three pillars enters their five-level KPI workflow, what the twin can give, and four questions for them.

### What the first results say

On the current data, 5 districts have an outage cost above the median, 6 have environmental stress above it, 3 are below on both, 1 has no counterpart, and none is above on both. All overall stress scores sit in the moderate band (20 to 29), which is why the groups use medians. An outage alone adds at most 3 points to their stress index, so the larger channel is hospital capacity (for example a trip of one substation surely reaches 13 hospitals), which their scored KPIs do not yet see.

### What flows each way

- **To them:** failure costs, hospitals exposed to each failure, reliability packages.
- **To us:** district environment and facilities (map layers, joint table), hospital pressure and package figures from their engine, and, as a possible next step, their temperature and heat-day series to drive our cooling and electricity demand scenarios.

## 7. Run it

```bash
# the twin alone
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
cp .env.example .env            # optional: GROQ_API_KEY; without it agents answer from recorded responses
cd ../frontend && npm install && npm run build                                    # once
cd ../backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000          # then open http://localhost:8000

# both projects together (clones their repository on first run)
./run_all.sh                    # twin on :8000, their dashboard on :8501, also inside the twin's Healthcare tab
```

The first start trains the forecast models (about 3 minutes) and caches them. Tests: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q` (about 8 minutes) and `cd frontend && npm run lint && npm test && npm run build`.

## 8. Where things are

| Path | What it is |
| --- | --- |
| `backend/app/` | sim, detection, ml, cost, agents, board, lab, city (the healthcare data), schemas, API |
| `backend/scripts/` | demo check, fixtures, `export_for_health.py`, stress and measurement scripts |
| `frontend/src/` | pages, map, panels, charts, store, API client, i18n, design |
| `integration/health/` | the link: mapping, copies of their data with source hashes, generated `out/` tables, workflow impact |
| `fixtures/` | one JSON sample for every message and endpoint (the shared contract) |
| `docs/` | contract, cost model, decisions (D1 to D28), status, stress-test plan, measurements |
| `README.md` | the detailed README of the twin; this file is the short version of everything |

## 9. Honest limits

- The data of both projects is simulated, so accuracy is accuracy on a simulation, and their health figures are simulator output, not clinical measurements.
- Most prices are planning figures. The package costs in the healthcare link are placeholders (theirs, in their own configuration, and ours in `integration/health/packages.csv`); no failure rate is assumed, so tables say "if it happens" or "how many events to break even", never "how often".
- Matching the two projects is by name and kind, not by place, and their coordinates are approximate; a hospital is linked to a failure through its district's zone, because the twin links a zone, not a hospital, to a substation, pump or plant. Rows that are only an upper bound say so.
- Their hospital pressure comes from the default "stress-calibrated" view of their engine, which their own page calls a scenario visualization, not observed utilization.
- Detection precision is computed at an assumed fault rate; a leak below about 24 m3/h is mostly missed. The board orders crews; it does not dispatch real ones or re-plan mid-execution.
- No sign-in: the approver's name is what is typed. The hash chain shows tampering but cannot stop someone with file access from rewriting the whole log.
- Storage is in memory plus JSON files, and one simulation runs at a time.

## 10. Glossary

| Term | Meaning |
| --- | --- |
| EGP | Egyptian pound |
| P10 / P50 / P90 | Low, middle and high value of a result when every assumption varies inside its registered range |
| Cost of delay | What waiting costs: loss and disruption per hour plus the secondary effects |
| NRW | Non-revenue water: produced but not billed (leaks, metering) |
| DCP | District cooling plant |
| SS-01 to SS-04 | The four substations (`elec-sub-01` to `04`) |
| ESI | Environmental Stress Index of the healthcare project, 0 to 100 (below 20 low, 20 to 35 moderate, 35 and above high) |
| CPR / pressure | Demand over capacity of a district's health services; above 1.0 demand exceeds capacity |
| WPS, HA4, CPS | Their water-pollution sub-index, primary-care CPR and capacity pressure score |
| Direct / type / none | How a district of theirs matches a zone of ours: the same district, the same kind of district, no counterpart |
| Group | Where a district falls against the median on outage cost and environmental stress |
