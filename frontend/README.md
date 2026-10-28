# UtilityGuard frontend

Run from `frontend/` with the installed dependencies:

```sh
npm run mock
# In another terminal:
npm run dev
```

Vite proxies `/api` and `/ws` to port 8000, for either the real backend or the mock. No packages need adding. Fonts and the topology map style are local; the map uses no remote tiles or glyph services.

The interface follows contract §4.9 and the regenerated fixtures. Costs, forecasts, explanations, sensitivities, and calculation results are rendered from backend data. Formula substitution is text-only; unknown inputs remain named instead of receiving invented values. Findings replace previous entries with the same ID. Open alerts include both `open` and `acknowledged` states.

The decision card exposes evidence tags, the three main calculations, backend cost tiles, sensitivity, crew actions, staggered reasoning, verification/fallback badges, PDF download, and the full calculation drawer. Assumptions use stable keys in PUT requests; the backend re-sends updated findings. Scenario controls provide parameter sliders/dropdowns, run/stop, and a confirmed reset. Asset and sensor selection opens health, failure probability, all sensor charts, and recent alerts. Solid chart lines are measured; dashed lines use `reading.forecast`.

Keyboard: `1/2/3` toggle networks, `S` (also `L`) opens Scenario Lab, `C` toggles charts, and `Cmd/Ctrl+K` opens commands, including assumptions. Dialogs trap focus and support Escape. Reduced motion is respected. English strings are in `src/i18n/en.json`; Arabic remains a partial stub with English fallback and RTL layout.

## Mock behavior and limits

The mock reads repository-root `fixtures/`, without writing them. `UTILITYGUARD_FIXTURES` and `PORT` permit isolated tests. Burst sequence delays are cumulative; readings advance monotonically. Snapshot reconnects retain the replay state. Chat emits `chat_route`, `chat_chunk`, then `chat_done`, using unique IDs for subsequent requests. Assumption saves return `{assumptions, recomputed}` and re-emit the recorded finding with its existing ID and updated assumption metadata.

Only the pipe-burst simulation has a recorded sequence. Other scenario controls exercise run/stop state but cannot synthesize unprovided simulation results. Assumption changes in the mock retain recorded monetary results; real cost recomputation belongs to the backend. Report export returns a structured 404 because there is no PDF fixture. The UI displays that error; the real backend report endpoint is wired for PDF download.

## Contract questions for the backend owner

- Scenario parameter fixtures have no `options`. The UI accepts provided options and otherwise derives asset choices of the default asset's kind, or sensor choices of the default sensor's kind, from topology. Should the backend supply explicit allowed options?
- The recorded “Repair now” formula includes `equipment` without a matching input. The UI leaves that token visible; what input value should the backend include?
- Hourly calculation records use `unit: "EGP"` although their names/inputs indicate EGP/h. The UI preserves the supplied result unit. Should these records use `EGP/h`?

## Verification

```sh
npm run typecheck
npm run lint
npm run build
npm test
```

Tests cover real fixture parsing, per-sensor ordering, reconnect hydration, alert counting including acknowledgement, same-ID finding replacement, three complete burst sequence rehearsals, live chat routing and completion, assumptions HTTP serialization/re-emission, and rendered decision-card evidence/totals. HTTP integration runs through Express with in-memory streams, without opening a port. These state-level rehearsals do not establish browser frame rate or visual correctness.

In this sandbox, `npm run mock` and `npm run dev` are blocked by `listen EPERM`. Browser visual review also requires an approved computer-use surface. Real-backend/browser integration and frame-rate review remain to be performed in a normal local session.
