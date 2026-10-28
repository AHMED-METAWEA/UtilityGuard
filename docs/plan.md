# UtilityGuard Build Plan

Sources of truth: [spec.md](spec.md) and [contract.md](contract.md). Decisions: [decisions.md](decisions.md).
Owners: **CC** = Claude Code (backend, AI, review). **CX** = Codex (frontend, bulk work).

## 1. Repo structure and stack

```
UtilityGuard/
├── docs/                  spec, contract, plan, decisions, checkpoints/, demo-script.md
├── fixtures/              one JSON sample per message and endpoint (shared truth)
├── backend/               Python 3.12, FastAPI, Pydantic v2
│   ├── app/
│   │   ├── schemas/       Pydantic models for every contract shape
│   │   ├── sim/           topology, demand profiles, seeded engine, events, propagation
│   │   ├── detection/     quality flags, z-score + IsolationForest, leak, faulty sensor, grouping
│   │   ├── ml/            LightGBM forecaster, failure classifier, metrics, importances
│   │   ├── cost/          assumptions config, formulas, calculation records, sensitivity
│   │   ├── agents/        LLM wrapper (Groq, retry, timeout, fallback), tools, specialists, orchestrator (LangGraph), number checker
│   │   ├── api/           REST routers, WebSocket hub, snapshot
│   │   └── extras/        PDF report, compare, sensor placement, drift
│   ├── data/              assumptions.json, fallback_cache.json, trained models
│   └── tests/             fixture schema tests, cost formula tests, sim determinism, demo run x3
├── frontend/              React + TS + Vite, Tailwind, MapLibre GL + deck.gl, ECharts, Zustand, Framer Motion, i18n (en/ar)
│   ├── mock-server/       Node WS + REST replaying /fixtures, including the full burst sequence
│   └── src/
└── scripts/               dev.sh (runs both), demo_check.py
```

## 2. Milestones

| Phase | Task | Owner | Effort |
| --- | --- | --- | --- |
| **P0 Contract (~2h)** | Pydantic schemas for all shapes | CC | 1h |
| | Fixtures for every message and endpoint, plus the burst sequence | CC | 45m |
| | Schema tests that validate the fixtures | CC | 15m |
| | App shell, design tokens, layout skeleton, i18n, mock server | CX (parallel) | 2h |
| **P1 Thin slice: water end to end (~4h)** | Topology: 6 zones on real coordinates, water network (reservoir, pumps, pipes, sensors) | CC | 1h |
| | Seeded engine, 2 s tick, daily and weekly profile, temperature, noise | CC | 1h |
| | FastAPI: topology, health, `/ws/stream` with snapshot and batched readings | CC | 1h |
| | Map with zones, glowing water layer, flow animation, sensor markers, live bottom charts, connection state | CX | 3h |
| | Review CX output against the contract, connect to the real backend, screenshot | CC | 30m |
| **P2 Detection, cost, Water Agent (~5h)** | Pipe burst and stuck sensor events, propagation | CC | 1h |
| | Detection (quality, anomaly, leak, faulty sensor) and alert grouping into incidents | CC | 1.5h |
| | Assumptions config, cost formulas, calculation records, sensitivity, plus tests | CC | 1.5h |
| | LLM wrapper, Water Agent, number checker, fallback cache | CC | 1h |
| | Alert panel, finding card (reasons, formula view, assumptions panel, act now vs delay) | CX | 3h |
| **P3 Electricity, cooling, orchestrator (~6h)** | Electricity and cooling networks, cross links (powers, cools), substation trip, pump failure, plant fault, heat wave, holiday demand | CC | 2h |
| | Electricity and Cooling Agents, LangGraph orchestrator with routing, chat streaming | CC | 2.5h |
| | Scenario control API and reset | CC | 30m |
| | Electricity and cooling layers, scenario lab, chat panel with live routing, asset drawer | CX | 4h |
| **P4 ML (~4h)** | 60-day history, LightGBM forecaster per network and zone, metrics, importances | CC | 2h |
| | Failure prediction, health scores, early warning | CC | 1h |
| | Forecast overlays and band, model performance view, explainability | CX | 2.5h |
| **P5 Extras (~3h)** | PDF report, compare, sensor placement, drift endpoints | CC | 2h |
| | Compare view, drift and placement overlays, command palette, shortcuts, Arabic RTL | CX | 3h |
| **P6 Polish and rehearsal (~4h)** | Record the fallback cache for every demo step, offline test | CC | 1h |
| | Visual polish: empty, loading and error states, motion, projector check, frame rate | CX + CC review | 2h |
| | Demo run 3 times, fix issues, final checkpoint | CC | 1h |

Total is about 28 hours of agent work across 2.5 days, with CC and CX working in parallel. If time runs short, cut in this order (spec 12): voice input, Arabic RTL, drift, sensor placement, compare. The core is never cut.

## 3. Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| Groq is down or rate limited during the demo | The fallback cache answers every demo question, the UI shows a "Fallback" badge, and the offline test runs in P6 |
| The LLM puts wrong numbers in its text | Numbers come from code, and the checker strips or flags any mismatch |
| Codex output drifts from the contract | Fixture-driven mock server, CC review before merge, TS types checked against the fixtures |
| Map performance with 3 layers and animation | deck.gl TripsLayer for flow, a capped number of particles, readings applied in batches with requestAnimationFrame |
| Simulation looks fake | Real demand profiles per zone type, physical propagation (pressure drop downstream, load shift) |
| Codex or Claude usage limits | Heavy reasoning on CC, bulk UI on CX, small focused Codex briefs, progress tracked in the checkpoints |
| No network at the venue | Basemap tiles cached or a local style fallback (plain zone polygons on a dark background), LLM fallback mode |
| ML training is slow or unstable | Small LightGBM models trained at startup in under 10 s and saved to disk |

## 4. Usage budget

| Phase | CC share | CX share |
| --- | --- | --- |
| P0 | 10% | 10% |
| P1 | 15% | 20% |
| P2 | 20% | 15% |
| P3 | 25% | 20% |
| P4 | 12% | 10% |
| P5 | 8% | 15% |
| P6 | 10% | 10% |

If one phase goes about 30% over budget, it is reported at the checkpoint and the cut order applies.

## 5. Demo script (about 7 min)

1. **Open (30 s):** the live twin, all three layers glowing, charts moving, top bar shows Live and LLM Live.
2. **Forecast (1 min):** open Model Performance and show the 24 h water forecast for the Government District with its band, MAE/RMSE/MAPE, detection precision and recall, and the top features.
3. **Pipe burst (1.5 min):** Scenario Lab, pipe burst in zone-gov. The pipe pulses red, pressure falls on the chart, and one grouped alert slides in. The Water Agent reasoning streams in, then the finding: numbered reasons, the formula `Q_leak × t × P_water` with real values, act now vs delay, saving, and the ±20% sensitivity.
4. **Edit an assumption (30 s):** raise the water price and watch the saving recompute.
5. **Substation trip (1.5 min):** elec-sub-02 trips, pumps stop, water pressure falls, cooling is affected. One cross-network incident.
6. **Ask the orchestrator (1.5 min):** "What is the best plan right now and what will it cost?" The chat shows the routing to Electricity, Water and Cooling, then a combined ranked plan with total costs and a link to the calculation record.
7. **Close (30 s):** export the PDF report, reset the twin, future work (gas agent, real SCADA data).

## 6. Definition of done

- [ ] Every contract message and endpoint is served by the real backend and validated against the fixtures
- [ ] Cost formula tests pass, including the saving and the sensitivity
- [ ] The simulation is deterministic with a fixed seed (test)
- [ ] All 7 scenarios work, and reset returns the baseline
- [ ] The 4 agents work live, the routing is visible, and the number checker is active
- [ ] Fallback mode is verified with the network off
- [ ] Every recommendation shows reasons, formula, assumptions, act now vs delay, saving, confidence and sensitivity
- [ ] Empty, loading and error states exist on every panel, plus a reconnecting state
- [ ] The UI holds a steady frame rate with 3 layers and the charts running
- [ ] The demo script runs end to end 3 times in a row without a restart (`scripts/demo_check.py` plus a manual run)
