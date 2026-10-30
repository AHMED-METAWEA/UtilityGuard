# UtilityGuard frontend

React 18 + TypeScript + Vite. State in Zustand, the map in MapLibre GL (no remote tiles: the buildings, roads, parks and the Green River are local GeoJSON in `public/geo`), charts are hand-written SVG, colours and spacing come from CSS variables in `src/design/theme.css` (light and dark).

```sh
npm install
npm run build        # the backend serves dist/ at http://localhost:8000
npm run dev          # port 5173, proxies /api and /ws to localhost:8000 (the backend must be running)
npm run lint && npm run typecheck && npm test
```

## Pages (hash routes)

| Route | Page | What is on it |
| --- | --- | --- |
| `#/live` | Live twin | Map with network layers, KPIs, live charts, alerts, asset and sensor drawers, the Scenario Lab. |
| `#/incidents/{id}` | Incidents | One incident: map with the suspected leak pipes, the finding (what happened, why we are sure, what it costs, what to do), Decision Studio, **the board of agents and the decision control**. |
| `#/savings/{id}` | Savings | Savings opportunities with formula, range and payback; edit assumptions. |
| `#/forecast` | Forecast and models | Demand forecast with band, backtest evidence against baselines, inputs, and the comparison of five models. |
| `#/lab` | AI lab | Detector benchmark, localization, sensor value, data quality. |
| `#/executive` | Executive | Scorecard against target, where money can be saved, what could fail next (risk ranking), district health. |
| (cards and drawers) | Early warning cards on Live and Executive, the **Why drawer** (any number with a `Why?` control), and **Try to fool the AI** (button on an incident). |
| `#/audit/{sessionId?}` | Decision log | The hash-chained log: verification, filters by group and session, CSV export. |

Keyboard: `Cmd/Ctrl+K` command palette (includes assumptions), `S` Scenario Lab, `C` chat, `T` theme. Dialogs trap focus and close on Escape. Reduced motion is respected.

## Layout of `src`

| Folder | Contents |
| --- | --- |
| `api/` | `client.ts` (fetch with a typed error), `ws.ts` (WebSocket client with runtime validation of every message and reconnect), `types.ts` (hand-written mirror of the Pydantic schemas), `dash.ts`, `lab.ts`. |
| `store/` | Zustand slices: readings and history, alerts, incidents, findings (stored newest-first, read with `latestFinding`), board sessions, the decision log, UI state. |
| `ui/` | Shared components (`Card`, `Meter`, `StatusChip`…), `Decision.tsx` and `Provenance.tsx` (evidence tags, calculation view), `Board.tsx` (transcript, crew timeline, decision card) and `DecisionControl.tsx` (approve, change the order, reject, execution progress). |
| `pages/`, `panels/` | The pages above and the drawers, palette, chat dock and Scenario Lab. |
| `charts/` | `TimeChart`, `Columns`, `LeakCurve`, `Sparkline`, `ModelBars`, `gantt.ts` (crew timeline layout), `scale.ts`. |
| `map/CityMap.tsx` | The map, asset markers, leak suspects layers. `window.__map` exists in dev builds only. |
| `lib/` | Pure helpers: formatting, route parsing, `present.ts` (what to show for a finding), `decision.ts` (approval helpers and error texts), `audit.ts` (grouping and labels for the log). |
| `i18n/` | English copy lives in the code as default values; `ar.json` is a partial Arabic stub (navigation and top bar only) with RTL layout. |

## Rules

- **Never invent a number.** Costs, forecasts, figures in the board and the calculation view are rendered from the backend. Unknown inputs stay named.
- **Contract first.** `src/api/types.ts` and the runtime validators in `ws.ts` follow `docs/contract.md` and the files in `../fixtures`. A message that fails validation is dropped, not guessed.
- **No optimistic updates for decisions.** The approval status changes only when the server says so (the response or the `board_decision` message).
- Evidence tags (Measured, Calculated, Assumed, Rule) appear next to numbers; hover or open the calculation to see the formula and the assumptions.

## Tests

`npm test` runs Node's test runner over `tests/*.test.ts|mjs` (61 tests): fixture parsing, per-sensor ordering, reconnect hydration, alert counting, same-id finding replacement, burst sequence rehearsals, chat routing, assumptions serialization, lab keys, board state, approval and audit helpers, and the forecast comparison helpers. These are state-level tests; they do not measure frame rate or visual correctness. Visual checks are done in the browser at desktop width and at 390 px (no horizontal overflow).

## Mock server

`npm run mock` replays `../fixtures` (a recorded pipe-burst sequence, chat and assumptions) without the backend. It predates the board, approval and decision log and does not serve those endpoints; use the real backend for them.

## Known loose ends

- `echarts`, `echarts-for-react`, `deck.gl` and `framer-motion` are listed in `package.json` but no source file imports them. Removing them would shrink the install and the bundle.
- The production bundle is one chunk of about 1.3 MB (the Vite warning limit is raised to 1200 kB). Route-level code splitting would fix it.
- Two `react-hooks/exhaustive-deps` warnings remain in `map/CityMap.tsx` (ref values in effect cleanups).
- Arabic is a stub.
