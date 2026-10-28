# UtilityGuard Multi-Agent Digital Twin: Project Specification

A live digital twin of a district of the New Administrative Capital, with specialist AI agents for water, electricity, and cooling, coordinated by an orchestrator that helps the business owner make decisions.

## 1. Vision

When the web app opens, the user sees an interactive map of a part of the capital, split into real districts such as the Government District, Central Business District, and residential zones. On top of the map are switchable layers for the water network, electricity network, and district cooling network, each with pipes or lines, stations, and sensors. Live charts below the map update in real time. The user can trigger scenarios, watch the twin react, and talk to an AI team that explains what is happening, predicts what comes next, and recommends what to do and what it will cost.

The goal is a demo that impresses a committee visually and shows real AI and data science depth underneath.

## 2. Key Constraints

- No real ACUD data is available, so the network is synthetic but realistic and follows the capital's real district layout. A satellite image or OpenStreetMap can be used as the visual background.
- Build window is 2 to 3 days, using Claude Code and Codex together.
- LLM provider is Groq through its API, using the user's own key.
- Numbers (costs, savings, forecasts) are computed by deterministic code or ML models. The LLM only explains, reasons, and routes. This keeps every number explainable.
- The audience mostly sees the frontend, so visual impact is the top priority.

## 3. System Overview

The system has five parts:

1. **Simulation engine** that generates live sensor readings for each network.
2. **Data quality and detection layer** that finds anomalies, leaks, and faulty sensors.
3. **ML layer** that does forecasting and failure prediction.
4. **Multi-agent layer** with one specialist agent per utility plus an orchestrator.
5. **Web application** with the digital twin map, live charts, alert panel, scenario lab, and chat.

## 4. The Digital Twin

### 4.1 Map and districts

A map of a slice of the capital divided into zones: Government District, CBD, residential districts, commercial and service areas. Each zone has a demand profile, for example government zones peak in working hours and residential zones peak morning and evening.

### 4.2 Network layers

The user toggles any combination of layers:

- **Water:** main pipes, branches, pumping stations, reservoirs, pressure and flow sensors.
- **Electricity:** substations, feeders, transformers, load and voltage sensors.
- **Cooling:** district cooling plants, chilled water loops, temperature and flow sensors.

Each element is clickable and shows its live status, recent history, and health score. Sensors are shown as colored markers (green normal, amber warning, red fault).

### 4.3 Cross-network dependencies

The networks affect each other, and this is what makes the demo impressive. Examples: a power outage at a substation stops pumps and lowers water pressure; high summer heat raises cooling demand, which raises electricity load; a cooling plant failure in a district shifts load elsewhere. The orchestrator reasons across these links.

## 5. Simulation Engine

- Generates readings every 2 to 3 seconds per sensor: pressure, flow, load, voltage, temperature.
- Uses daily and weekly demand patterns, weather (temperature) influence, and random noise.
- Supports injected events: pipe burst, sensor drift or stuck sensor, substation trip, pump failure, cooling plant fault, heat wave, holiday demand change.
- Events propagate through the network model so downstream sensors react realistically.
- Streams to the frontend through WebSocket.

## 6. Data Quality and Detection

- Missing values, stuck values, spikes, and out-of-range readings flagged per sensor.
- Anomaly detection with statistical methods and an ML model such as Isolation Forest.
- Leak detection by pressure drop and flow mismatch between neighbouring sensors.
- Faulty sensor detection by comparing a sensor against its neighbours and its own history.
- Alert grouping and deduplication so that one incident produces one alert, not fifty.

## 7. Forecasting and Prediction (AI and Data Science Core)

This is the part that shows the AI and Data Science depth.

- **Demand forecasting:** short-term forecast of water demand, electricity load, and cooling load per zone for the next hours and days. Candidate models: gradient boosting, Prophet, or a small LSTM. Show forecast against actual with a confidence band.
- **Failure prediction:** predictive maintenance score per asset (pump, transformer, pipe segment) estimating the probability of failure in the next period.
- **Anomaly forecasting:** early warning when a trend suggests an upcoming fault.
- **Model evaluation shown in the app:** MAE, RMSE, and MAPE for forecasts, precision and recall for fault detection, so the committee sees the models are measured, not just shown.
- **Explainability:** feature importance or SHAP-style reasons for each prediction.

## 8. Multi-Agent Layer

### 8.1 Specialist agents

Each agent is an expert in only its own domain and treats it as the most important thing in the system.

- **Water Agent:** leaks, pressure problems, pump failures, water loss, repair cost, and water-specific recommendations.
- **Electricity Agent:** overloads, outages, voltage issues, transformer health, load balancing, repair cost.
- **Cooling Agent:** plant efficiency, temperature deviations, cooling demand spikes, repair and energy cost.
- **Future agents:** Gas Agent and others plug in the same way.

Each agent has its own tools, for example read sensors, query forecast, run detection, estimate cost, and look up maintenance playbooks. Each agent returns a structured finding: what happened, severity, probable cause, predicted impact, recommended action, estimated cost, and estimated saving if acted on early.

### 8.2 Orchestrator

The orchestrator is the single point of contact for the business owner.

- Understands the owner's question or request in natural language.
- **Routes** the request to the right specialist agent, or to several agents at once for cross-network questions.
- Combines the agents' findings, resolves conflicts, ranks priorities, and produces one clear recommendation.
- Can proactively raise issues when agents report them.

Example: the owner asks what happens if substation 3 trips. The orchestrator asks the Electricity Agent for the load impact, then the Water Agent for pump and pressure impact, then the Cooling Agent for cooling loss, and finally summarises the combined consequence, the action plan, and the total cost.

### 8.3 Decision support output

Every recommendation includes: the action, who to send (maintenance team type), urgency, expected cost of acting now, expected cost of delay, and expected saving. All costs come from a transparent cost model, not from the LLM.

### 8.4 Implementation

- Framework: LangGraph, with the orchestrator as the supervisor node and each specialist as a sub-graph.
- LLM: Groq API (fast inference, suitable for live demo). Use a strong model for the orchestrator and a faster one for specialists if needed.
- Structured outputs with a JSON schema for agent findings.
- A fallback cache of recorded responses so the demo survives a network failure.

## 9. Cost Model

A deterministic module calculates, for each incident:

- Repair cost (parts, crew, equipment).
- Cost of delay per hour (water loss, energy waste, service disruption, penalties).
- Saving from early intervention.
- Every number comes with a breakdown of its causes so it can be explained line by line.

## 9A. Decision Value and Explainability (Core Requirement)

The purpose of the whole system is to produce decisions that save real money, and every decision must show why it was made. This section overrides any shortcut elsewhere in the document.

### Principles

- **Real savings only.** Every recommendation states the saving as the difference between two costs computed by the same formula: the cost if nothing is done, and the cost if the action is taken. No saving is shown without both sides.
- **Every number has a formula.** Costs, forecasts, savings, and risk scores are computed by code from a named formula and named inputs. The LLM never invents or estimates a number. It only reads the computed result and explains it.
- **Every decision has numbered reasons.** Each recommendation lists its reasons as numbered points (1, 2, 3, 4), each tied to a measured value, a forecast, or a formula result.
- **Every assumption is visible.** If a value is assumed (for example a repair crew cost), it is listed as an assumption with its value and unit, and the user can change it.

### Price and parameter table

All unit prices and parameters live in one editable configuration, shown in the UI as an assumptions panel. The formulas read from it, so changing a price recomputes everything. Defaults are clearly marked as assumptions until real figures are supplied. If the LLM is given a web search tool, it may fetch a current price (for example the price per cubic metre of water or per kilowatt-hour) and propose it, but the value only enters the configuration after the user accepts it, with its source and date stored beside it.

| Parameter | Unit | Used by |
| --- | --- | --- |
| Water production cost | EGP per cubic metre | Water loss cost |
| Electricity tariff | EGP per kWh | Energy waste, outage cost |
| Cooling energy cost | EGP per ton-hour | Cooling inefficiency cost |
| Crew call-out cost | EGP per dispatch | Repair cost |
| Repair parts cost by asset type | EGP per repair | Repair cost |
| Service disruption penalty | EGP per hour per zone | Cost of delay |

### Core formulas

Water loss cost during a leak:

```latex
C_{water} = Q_{leak} \times t_{delay} \times P_{water}
```

Here Q_leak is the estimated leak flow in cubic metres per hour, t_delay is the hours until repair, and P_water is the water production cost per cubic metre. Q_leak is estimated from the measured flow mismatch between the upstream and downstream sensors.

Energy waste cost:

```latex
C_{energy} = (E_{actual} - E_{expected}) \times P_{kWh}
```

Total cost of delay per hour and the saving from acting now:

```latex
C_{delay} = C_{loss} + C_{disruption} + C_{secondary}
```

```latex
Saving = (C_{delay} \times t_{delay} + C_{repair\_late}) - (C_{repair\_now})
```

Here C_secondary is the knock-on cost in another network (for example a power trip stopping pumps), and C_repair_late is the repair cost after the problem has grown, which is higher than repairing now.

A forecast saving, such as shifting load away from a peak, uses the same structure with the forecast demand and the tariff difference between the two time windows.

### What every recommendation displays

1. The action and the target asset.
2. The numbered reasons, each linked to its evidence (a sensor reading, a trend, a forecast, or a rule).
3. The formula used, with the actual values filled in, for example 12 cubic metres per hour times 6 hours times 8 EGP.
4. The assumptions used, each flagged as measured, forecast, or assumed.
5. The cost of acting now, the cost of delay, and the saving, with a confidence level.
6. A sensitivity line showing how the saving changes if the main assumption is 20 percent higher or lower.

### Explaining predictions

Every forecast or failure prediction shows the factors behind it: the inputs that pushed it most (for example temperature, hour of day, recent trend), the model accuracy on past data, and the uncertainty band. The statement is always of the form: this result is based on these inputs and this model with this error.

### Guardrails for the LLM

- The LLM receives computed results as structured data and must quote them exactly.
- A checker compares every number in the LLM's answer against the computed data, and flags or removes any figure that does not match.
- If the data is missing, the agent says so instead of guessing.
- Every agent answer carries a link to the calculation record, so the user can open the exact formula and inputs.

## 10. Web Application

### 10.1 Screens

- **Main screen:** the digital twin map with layer toggles, zoom to any network, and clickable assets.
- **Live panel:** real-time charts for pressure, flow, load, temperature, with forecast overlays.
- **Alert panel:** grouped alerts with severity and the agent's recommendation.
- **Scenario lab:** buttons for burst pipe, sensor fault, substation trip, heat wave, pump failure, and holiday demand. Triggering one makes the map react live and the agents respond.
- **Agent chat:** the business owner talks to the orchestrator, which routes to the specialists. Show which agent answered.
- **Cost and impact view:** cost of action versus delay for the active incident.
- **Model performance view:** forecast accuracy and detection metrics.

### 10.2 Design

Futuristic and striking: dark theme, glowing network lines, animated flow along pipes and cables, smooth transitions, and a clean command-center feel.

### 10.3 Suggested stack

- Frontend: React with Vite, a map library such as MapLibre or Deck.gl, and a charting library such as Recharts or ECharts.
- Backend: FastAPI with WebSocket streaming.
- Agents: LangGraph with the Groq API.
- ML: scikit-learn, XGBoost or LightGBM, and optionally Prophet or PyTorch.
- Storage: SQLite or in-memory for the demo.

## 11. Extra Features to Impress

- Twin versus reality drift monitoring.
- Extra-sensor placement analysis, showing where adding a sensor would improve detection most.
- Alert grouping and deduplication.
- Exportable incident report as PDF.
- Before and after comparison of the same scenario, with and without intervention.
- Voice or text questions to the orchestrator.

## 12. Delivery Plan (2 to 3 days)

**Work split between the two coding agents.** First agree a data contract: the JSON shapes for sensor readings, alerts, agent findings, and scenario events, sent over WebSocket. Then work in parallel.

- **Claude Code (backend and AI):** simulation engine, detection, forecasting models, cost model, LangGraph agents and orchestrator, API and WebSocket.
- **Codex (frontend):** map and layers, live charts, alert panel, scenario lab, chat UI, and visual design.

**Day 1:** data contract, simulation engine for water, basic map and live charts, first Water Agent.

**Day 2:** electricity and cooling networks, cross-network dependencies, forecasting and failure prediction, orchestrator, scenario lab.

**Day 3:** cost model, extras, polish, model metrics view, demo script, fallback cache, rehearsal.

If time is tight, the minimum impressive demo is: map with three layers, live charts, three agents plus the orchestrator, forecasting, and four working scenarios.

## 13. Future Work

- Add a gas network with its own Gas Agent, using the same agent template.
- Connect to real ACUD SCADA and IoT data in place of the simulator.
- Add road traffic, waste management, and street lighting as further domains.
- Reinforcement learning for optimal dispatch of maintenance crews.
- Calibrate the twin with real pipe and cable geometry from GIS data.
- Mobile app and push alerts for field teams.
- Digital twin validation against real incidents.

## 14. Demo Script Idea

1. Open on the live twin with all three layers on and charts moving.
2. Show the forecast and the model accuracy.
3. Trigger a pipe burst in the Government District and watch the water agent react.
4. Trigger a substation trip and show water and cooling being affected across networks.
5. Ask the orchestrator for the best plan and show the routed answer with cost of action versus delay.
6. Export the incident report and close with the future work slide on gas and real data.
