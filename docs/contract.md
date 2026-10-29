# UtilityGuard Contract: Backend/Frontend Interface and UI/UX Standards

This document is the single agreement between the two coding agents. Claude Code owns the backend and AI. Codex owns the frontend. Neither side changes a shape in this document without updating it first. Read it fully before writing code.

## 1. Ownership

**Claude Code (backend and AI) is responsible for:**

- Simulation engine for water, electricity, and cooling, including event injection and cross-network effects.
- Detection layer: anomalies, leaks, faulty sensors, alert grouping.
- ML: demand forecasting and failure prediction, with evaluation metrics.
- Cost model.
- LangGraph agents: Water, Electricity, Cooling, and the Orchestrator.
- FastAPI REST and WebSocket server, and the fallback cache of recorded agent responses.

**Codex (frontend) is responsible for:**

- The map, layers, and asset interactions.
- Live charts, alert panel, scenario lab, agent chat, cost view, model performance view.
- All visual design, animation, state management, and the WebSocket client.
- A mock server that replays sample messages in the exact shapes below, so the frontend can be built without waiting for the backend.

**Shared:** this contract, the sample fixtures folder, and the ID naming rules.

## 2. Transport

- REST base path: /api/v1
- WebSocket: /ws/stream, one connection per client, server pushes JSON messages.
- All timestamps are ISO 8601 UTC strings.
- All messages are JSON objects with a type field and a payload field.
- Numbers use SI-style units fixed per sensor type (listed in section 4). The unit is also sent with each sensor for display.

## 3. ID Naming Rules

- Zones: zone-gov, zone-cbd, zone-res-1, and so on.
- Networks: water, electricity, cooling.
- Assets: net-type-number, for example water-pump-03, elec-sub-02, cool-plant-01.
- Sensors: sensor id equals asset id plus a suffix, for example water-pump-03-pressure.
- Alerts: alert-number. Incidents: inc-number. Scenarios: scn-name.

## 4. Data Shapes

### 4.1 Topology (REST, loaded once)

GET /api/v1/topology returns the static description of the twin:

- zones: array of objects with id, name, type (government, business, residential, services), center (lat and lng), and polygon (array of lat and lng pairs).
- assets: array of objects with id, network, kind (pipe, pump, reservoir, substation, feeder, transformer, plant, loop), name, zoneId, geometry (point or line), and status.
- sensors: array of objects with id, assetId, network, kind (pressure, flow, load, voltage, temperature), unit, normalRange (min and max), and position.
- links: array of dependencies between networks, each with fromAsset, toAsset, and relation (for example powers, cools).

### 4.2 Sensor reading (WebSocket)

Message type: reading. Payload fields: sensorId, timestamp, value, quality (ok, suspect, missing), and an optional forecast value. Readings are batched: one message may carry an array of readings for the same tick, with a tick counter.

### 4.3 Asset status update (WebSocket)

Message type: asset_status. Payload fields: assetId, status (normal, warning, fault, offline), healthScore (0 to 100), and failureProbability (0 to 1) when available.

### 4.4 Alert (WebSocket and REST)

Message type: alert. Payload fields: id, incidentId, network, zoneId, assetIds (array), severity (info, warning, critical), title, summary, detectedAt, state (open, acknowledged, resolved), and confidence (0 to 1). Grouped alerts share an incidentId, and updates to an alert reuse the same id.

### 4.5 Agent finding (WebSocket and REST)

Message type: agent_finding. Payload fields: id, incidentId, agent (water, electricity, cooling, orchestrator), headline, explanation (plain language), probableCauses (array), predictedImpact (text and affected zone ids), recommendedAction (text, crewType, urgency), cost (a cost object, see 4.6), and reasoningSteps (array of short strings shown in the UI as the agent thinking).

### 4.6 Cost object

Fields: currency (EGP), actNow (total), delayPerHour (total), expectedSaving (total), and breakdown (array of items, each with label, amount, cause, and a unique `group` cost owner). The backend computes every number in code, and the frontend only displays them.

### 4.6A Explanation and calculation record

Every agent finding and every cost object also carries an explanation block, so the UI can show why a decision was made and how each number was reached. Fields: reasons (an ordered array of objects with text, evidenceType of measured, forecast, rule, or assumed, and evidenceRef), calculations (an array of objects with name, formula as plain text, inputs as name, value, unit, source, basis, optional status, and optional assumption key, and result), assumptions (an array of the assumption registry entries defined in 4.10), sensitivity (an array with the changed input, the change in percent, and the new saving), tornado (rows `{input, name, low, high, savingAtLow, savingAtHigh}`, sorted by descending absolute saving swing), and confidence (0 to 1). The frontend shows these as numbered reasons, a formula view with the real values filled in, and an assumptions panel. GET /api/v1/calculations/{id} returns the full record, and GET and PUT /api/v1/assumptions read and update the price and parameter configuration, which triggers a recomputation of the active incident.

### 4.7 Forecast (REST and WebSocket)

GET /api/v1/forecast?network=water&zoneId=zone-gov&horizonHours=24 returns a series of points with timestamp, predicted, lower, and upper, plus the latest metrics (mae, rmse, mape). The WebSocket sends type forecast_update when a forecast is refreshed.

### 4.8 Model metrics

GET /api/v1/metrics returns forecasting metrics per network, detection precision and recall, and the date of the last model run. Also top feature importances per model for the explainability view.

### 4.9 Clarifications fixed in Phase 0

The fixtures folder is the exact reference for these shapes, and the backend Pydantic models live in backend/app/schemas.

- JSON field names are camelCase.
- The reading payload is `{tick, timestamp, readings: [Reading]}`.
- Asset geometry is `{type: point|line, coordinates: [{lat, lng}]}`.
- Pipe sensors use suffixes `-flow-in`, `-flow-out` and `-pressure`. Cooling plants use `-supply-temp`, and loops use `-return-temp`.
- An agent finding keeps `explanation` as plain text, and its explanation block (4.6A) is in `explain`. The cost object carries the same block in `cost.explanation`. The finding also has `createdAt`, `fallback` and `numberCheck` (passed or corrected).
- The cost object adds `delayHours` and `costOfDelay` (that is, `delayPerHour × delayHours + lateRepair`), so the UI can show the act now / delay / saving tiles without doing any math.
- An assumption has a stable `key`, which is used by PUT /assumptions as a `{key: value}` map, and by sensitivity rows as `input`.
- An incident is `{id, title, networks, zoneIds, severity, state, openedAt, alertIds, findingIds, calculationId}`.
- On connect, the WebSocket sends `snapshot` first: `{tick, timestamp, topology, latest, assets, alerts, incidents, findings, scenarios, llm}`.
- New WS type `chat_route` `{messageId, routing: [{agent, reason}]}` is sent as soon as the orchestrator decides, before any chunk, so the UI can show the routing live. `chat_chunk` also has `agent`. `chat_done` has `agentsConsulted`, `routing: [{agent, reason}]`, `fallback`, `calculationId` and `findingIds`.
- GET /api/v1/findings lists agent findings. PUT /api/v1/assumptions takes a `{key: value}` map and returns `{assumptions, recomputed}`; recomputed findings are re-sent over the WS with the same id.
- The demo sequence is in fixtures/sequence/burst.json, as an array of `{delayMs, message}`.

### 4.10 Dashboard and savings endpoints (added in the redesign)

All numbers are computed by the backend. Fixtures: fixtures/rest/{kpis,savings,series_demand,series_substations,series_cooling_efficiency}.json.

- GET /api/v1/kpis returns a list of `{id, label, value, unit, digits, target, better (lower|higher), delta, series [{t, v}], note, formula, network}`. `delta` is the change versus the same hour yesterday. KPI ids: nrw, water_production, electric_load, cooling_efficiency, districts_served, savings_available.
- GET /api/v1/series/demand?network=water&zoneId=all&hours=24 returns `{network, zoneId, unit, now, actual [{t, v}], forecast [{timestamp, predicted, lower, upper}], metrics {mae, rmse, mape}}`. `zoneId=all` sums the zones.
- GET /api/v1/series/substations returns `{unit, capacity, lines [{id, name, points [{t, v}]}]}`, 24 h of load per substation.
- GET /api/v1/series/cooling-efficiency?days=30 returns `{unit, target, days [{date, value, ambientMax}]}`, kW per ton of cooling per day.
- GET /api/v1/sensors/{sensorId}/history?minutes=10 returns `{sensorId, unit, normalRange, points [{t, v, expected}]}`, the last minutes of one sensor with what the twin expected. It lets a page show the lead-up to an incident.
- GET /api/v1/savings returns `{generatedAt, totalMonthly, totalInvestment, totalRange {p10, p50, p90, runs}, opportunities [...]}`. An opportunity is `{id, title, network, where, summary, monthlySaving, savingRange, investment, paybackDays, paybackMonths, npv3Years, formula (values filled in), calculations, inputs, reasons, assumptions, confidence, status}`. It is recomputed when PUT /assumptions changes a price.
- Cost object changes: `actNow` is now the repair cost plus the losses until the crew arrives (`actNowLosses`, `responseHours`), so `expectedSaving = costOfDelay − actNow`. New field `savingRange {p10, p50, p90, runs}` comes from a seeded Monte Carlo over the uncertain assumptions (uniform within each registered `[low, high]`) and the leak estimate (±15%). These are scenario percentiles, not calibrated probabilities.
- Assumptions added: response_hours, setback_reduction, setback_invest, pm_target_pressure, pm_n1, pm_invest, reset_delta_c, reset_pct_per_c, reset_invest, clean_invest, planned_factor.

- Every assumption in GET/PUT `/assumptions`, `Explanation.assumptions`, and `Opportunity.assumptions` carries `{key, name, value, unit, source, userChanged, basis, status, low, high, reviewed}`. `status` is `official|benchmark|engineering|planning`; `reviewed` is a YYYY-MM-DD date. `source` retains the existing default/user attribution. Overrides retain the original planning/benchmark basis and range; they do not become verified evidence. Ranges describe uncertainty, not validation limits.
- Flat `penalty_*`, `delay_hours`, and `late_multiplier` keys are replaced by `customer_hour_{zoneType}`, `voll_{zoneType}`, `delay_hours_{kind}`, and `late_multiplier_{kind}`. Kinds are `pipe_burst`, `pump_failure`, `substation_trip`, `cooling_fault`, `stuck_sensor`, and `energy_waste`; existing detector names `leak` and `sensor_fault` remain accepted. Customer counts are registered as `customers_zone_*`, seeded by `ZONE_CUSTOMERS` in topology. For unserved MW without zone metering, customer shares allocate the total once across zones. VoLL owns customer economic outage harm; disruption owns only utility notification/support administration. Secondary energy owns only extra purchased energy, and cooling shortfall already covered by an electrical outage is excluded.
- `Cost.review` (boolean) and optional `Cost.reviewReason` flag savings outside `saving_min_{kind}` / `saving_max_{kind}`; amounts are preserved. Every breakdown `group` is unique: `loss:*`, `disruption:{zoneId}`, `secondary:electricity`, or `repair`. Breakdown amounts sum exactly to `costOfDelay`; the repair row includes the late multiplier once.
- `Explanation.tornado` recomputes the real saving at each used assumption's registered endpoints with all other inputs fixed; only assumptions that move the saving appear. Legacy `sensitivity` remains available.
- Opportunities add `paybackMonths` (investment / unrounded monthly saving; omitted if no positive saving), `npv3Years` (EGP, 36 month-end net savings discounted at `(1 + discount_rate)^(1/12) - 1`, less upfront investment), and named `calculations` for monthly saving, investment, payback, and NPV. `discount_rate = 0.18` is an effective annual planning rate to verify with finance. Planned maintenance models a recurring monthly expected net benefit, already subtracting planned repair expenditure, with no separate upfront investment. `paybackDays` is retained (30 days/month).
- `/compare` adds `explanation`, the same incident formula/assumption record used for its two totals and saving. Response losses are included exactly once in the act-now side.
- Savings totals are sums of displayed opportunity amounts; `totalRange` samples the same assumptions jointly to preserve shared-price dependence. These opportunities are standalone estimates; the total assumes independent, additive interventions without physical overlap. See `docs/cost-model.md` for formulas, all input bases, worked examples, and limitations.

- Forecast changes. `GET /series/demand` adds `baseline` (the same forecast on a normal day, only while a heat-wave or holiday scenario runs), `hindcast` (the model's values for the past hours, to lay over `actual`), `hindcastMape`, and `drivers {temperatureNow, temperaturePeak, heatWave, holiday, liveCorrection, correctionHours, weatherErrorC, trainedThrough}`. The forecast takes the running scenario as input, so it moves when a scenario starts. `liveCorrection` is the live demand over the model's value for now, applied to the near term and faded over `correctionHours`. The band is the model's own relative error on out-of-fold days, kept apart for normal, heat-wave and holiday days; for the all-districts view it is the measured band of the total, not a sum of district bands.
- `GET /forecast/evidence?network=` returns `{network, unit, trainDays, testDays, points, trainedThrough, weatherErrorC, model {mae, rmse, mape, bias}, baselines [{name, mae, mape}], skill, coverage, nominal, byHour [{hour, mape}], scenario {days, mape, mapeBlind}, liveCorrection {tauHours, maeWithout, maeWith}, zones [{zoneId, name, mae, rmse, mape}], importances}`. Scores are on the last `testDays` days, which no model was fitted on. `skill` is one minus the model's MAE over the best naive baseline's MAE. `coverage` is the share of test points inside the band, against `nominal`. The models train on the latest `trainDays + testDays` days and are refreshed daily. 404 `no_forecast` for an unknown network.

## 5. Scenario Control

- GET /api/v1/scenarios lists available scenarios with id, name, description, network, and parameters (for example severity and target asset).
- POST /api/v1/scenarios/run with a scenario id and parameters starts a scenario and returns a run id.
- POST /api/v1/scenarios/stop with the run id ends it and restores normal behaviour.
- The WebSocket sends scenario_state messages with runId, scenarioId, state (running, stopped), and startedAt.
- Starter scenarios: pipe burst, stuck sensor, substation trip, pump failure, cooling plant fault, heat wave, holiday demand.
- A reset endpoint, POST /api/v1/scenarios/reset, returns the whole twin to its normal baseline. This is essential for demo rehearsals.

## 6. Agent Chat

- POST /api/v1/chat with a message and an optional incidentId. The reply streams over the WebSocket as chat_chunk messages (with messageId and text) and ends with a chat_done message that includes which agents were consulted, in order.
- Each chat reply also includes the routing information: which specialist agent or agents the orchestrator called and why, so the UI can show the routing live.
- If the Groq call fails, the backend serves the recorded fallback answer and marks the message with fallback set to true.

## 7. Other REST Endpoints

- GET /api/v1/health returns server status and whether the LLM is live or in fallback mode.
- GET /api/v1/alerts and GET /api/v1/incidents list current and recent items.
- POST /api/v1/alerts/{id}/acknowledge acknowledges an alert.
- GET /api/v1/incidents/{id}/report returns an exportable incident report as a PDF.
- GET /api/v1/compare?scenarioId=... returns the before-and-after comparison of the same scenario with and without intervention.
- GET /api/v1/sensor-placement returns suggested locations for extra sensors with an estimated detection improvement.
- GET /api/v1/drift returns the twin-versus-reality drift indicators.

## 8. Behaviour Rules

- The backend sends a full snapshot on WebSocket connect, so a refreshed page recovers immediately.
- Reading frequency is one tick every 2 seconds, and the frontend must handle bursts and out-of-order messages gracefully.
- The frontend never computes costs, forecasts, or detection results. It only renders what the backend sends.
- Unknown fields must be ignored by both sides, so the contract can grow without breaking either one.
- Every error response has a code and a human-readable message.
- Any change to a shape is made in this document first, with the fixtures updated in the same change.

## 9. Fixtures and Mock Server

- A fixtures folder holds one realistic sample for every message type and endpoint response above.
- Codex builds a small mock server that replays these fixtures, including a full scenario sequence (normal, burst, alert, finding, resolution), so the entire UI can be built and polished before the real backend is ready.
- Claude Code validates its real outputs against the same fixtures with schema tests, so a mismatch is caught immediately.

## 10. Frontend Standards: Professional UI and UX

The frontend must look like it was built by a senior front-end engineer with twenty years of experience in UI and UX. It should feel like a real control-room product, not a student project.

### 10.1 Design principles

- Clarity first: the most important status is visible within two seconds of opening the page.
- Progressive disclosure: overview first, detail on click or hover, never everything at once.
- Consistency: one design system for spacing, color, typography, and components throughout.
- Calm by default, loud only when needed: normal state is subdued, and warnings and critical alerts get clear emphasis.
- Every interaction gives immediate feedback.

### 10.2 Visual language

- Dark command-center theme with deep navy or charcoal backgrounds, subtle gradients, and soft glass-style panels with fine borders.
- A restrained accent palette: one color per network (for example blue for water, amber for electricity, teal or cyan for cooling), and the standard green, amber, and red for status.
- Glowing lines for networks, with animated flow along pipes and cables. Animation speed reflects real flow.
- A modern typeface such as Inter for the interface and a monospaced or tabular font for numbers so values do not jitter when they update.
- A strict spacing scale (multiples of 4 or 8 pixels), consistent corner radius, and a clear type hierarchy.

### 10.3 Layout

- Full-screen map as the hero, with floating panels on top rather than a cluttered page.
- Top bar: project name, live connection status, LLM live or fallback indicator, clock, and the scenario lab entry.
- Left side: layer toggles and zone filter. Right side: alerts and agent findings. Bottom: live charts, collapsible.
- Agent chat as a docked panel that expands, showing routing steps and which agent is answering.
- Click on any asset to open a detail drawer with health score, live chart, forecast, and recent alerts.

### 10.4 Motion and feedback

- Smooth, short transitions (150 to 300 milliseconds) with easing, no jarring jumps.
- Chart updates animate gently, and new alerts slide in with a short highlight.
- A faulty sensor pulses on the map, and the affected zone is softly highlighted.
- When a scenario runs, the map visibly reacts, and the agent reasoning steps stream in one by one so the audience can follow the AI thinking.
- Skeleton loaders instead of spinners, and a graceful reconnecting state if the WebSocket drops.

### 10.5 UX details that signal quality

- Keyboard shortcuts for layer toggles and the scenario lab, and a command palette.
- Tooltips with units on every number, and consistent number formatting with thousands separators.
- Empty, loading, and error states designed for every panel.
- Clear severity hierarchy in alerts, with grouped incidents expandable into their individual alerts.
- Cost shown as a clear comparison: cost of acting now versus cost of delay, with the breakdown one click away.
- Responsive for large screens and projectors first, and still usable on a laptop.
- Accessible contrast and never relying on color alone, so icons and labels back up the status colors.

### 10.6 Suggested frontend stack

React with TypeScript and Vite, Tailwind CSS with a small component library, MapLibre GL or Deck.gl for the map, ECharts or Recharts for charts, Zustand for state, and Framer Motion for animation.

### 10.7 Arabic and English

Build with text in a translation file from the start, so an Arabic right-to-left version can be added without rewriting components.

## 11. Backend Standards

- Typed schemas with Pydantic for every message in this contract, so invalid output is rejected before it is sent.
- Deterministic simulation with a seed, so a demo can be reproduced exactly.
- Clear separation: simulation, detection, ML, cost, agents, and API in separate modules.
- All LLM calls go through one client wrapper that handles retries, timeouts, structured output, and the fallback cache.
- Logging of every agent step, so the reasoning shown in the UI is real and traceable.

## 12. Order of Work

1. Both agents read this contract and agree on any needed changes.
2. Claude Code writes the Pydantic schemas and the fixtures. Codex builds the app shell, design system, and mock server in parallel.
3. Claude Code builds the water simulation and WebSocket stream. Codex connects the map and live charts to it.
4. Add alerts and the Water Agent, then electricity and cooling, then the orchestrator.
5. Add forecasting, cost, scenario lab, and extras.
6. Polish, rehearse the demo script, and test fallback mode with the internet off.

## 13. Definition of Done

- Every message and endpoint in this contract works against the real backend and passes the fixture tests.
- The demo script runs end to end three times in a row without a manual restart.
- The UI holds a steady frame rate with all three layers and live charts running.
- The app works in fallback mode if the Groq API is unavailable.
