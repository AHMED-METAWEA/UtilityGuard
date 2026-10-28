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

Fields: currency (EGP), actNow (total), delayPerHour (total), expectedSaving (total), and breakdown (array of items, each with label, amount, and cause). The backend computes every number in code, and the frontend only displays them.

### 4.6A Explanation and calculation record

Every agent finding and every cost object also carries an explanation block, so the UI can show why a decision was made and how each number was reached. Fields: reasons (an ordered array of objects with text, evidenceType of measured, forecast, rule, or assumed, and evidenceRef), calculations (an array of objects with name, formula as plain text, inputs as name and value and unit and source, and result), assumptions (an array with name, value, unit, source, and whether the user changed it), sensitivity (an array with the changed input, the change in percent, and the new saving), and confidence (0 to 1). The frontend shows these as numbered reasons, a formula view with the real values filled in, and an assumptions panel. GET /api/v1/calculations/{id} returns the full record, and GET and PUT /api/v1/assumptions read and update the price and parameter configuration, which triggers a recomputation of the active incident.

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
- `chat_chunk` also has `agent`. `chat_done` has `agentsConsulted`, `routing: [{agent, reason}]`, `fallback`, `calculationId` and `findingIds`.
- The demo sequence is in fixtures/sequence/burst.json, as an array of `{delayMs, message}`.

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
