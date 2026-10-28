# UtilityGuard frontend · Phase 0

Run from `frontend/` with Node 22+:

```sh
npm install
npm run mock
# In a second terminal:
npm run dev
npm run build
npm run typecheck
npm run lint
npm test
```

Vite proxies `/api` and `/ws` to port 8000. The mock resolves **repository-root** `fixtures/` relative to its own file, regardless of the launch directory. It never writes fixtures. Missing JSON returns HTTP 404 `{code, message}`; a missing WS snapshot closes with code 1013 and the client reconnects. `UTILITYGUARD_FIXTURES` and `PORT` allow isolated mock tests. Optional burst sequence delays are cumulative (each `delayMs` is relative to the previous entry). Baseline noise pauses during a replay; sequence reading ticks advance monotonically so a late demo run is not rejected as stale. Chat replays the provided chunk and done fixtures. Report export returns a structured 404 because no PDF fixture is specified.

The scenario lab is intentionally a Phase 0 placeholder. Asset overlays and telemetry use connected data only. The CARTO basemap falls back to a dark canvas without blocking panels. Fonts are bundled locally. Arabic is a partial translation stub with English fallback; changing language sets document `lang` and `dir`. Keyboard: `1/2/3` layers, `L` scenario lab, `C` charts, `Cmd/Ctrl+K` quick actions. Dialogs trap focus and support Escape; reduced motion is respected.

## Contract ambiguities requiring coordination

- The task fixes batched readings as `{tick, timestamp, readings}`; contract 4.2 also describes a singular reading. The task's batch is implemented.
- No snapshot payload is specified in the contract prose. The shared fixture resolves it as `{tick, timestamp, topology, latest, assets, alerts, incidents, findings, scenarios, llm}`. Optional collections default empty. Readings buffer until the snapshot is applied. Reconnect replaces streaming state with the new baseline; per-sensor ticks drop duplicates and older readings, retaining 300 samples. Unknown message types and additive fields are ignored.
- Geometry only says “point or line.” The shared fixtures resolve this to lowercase `point` / `line` and `{lat, lng}[]` coordinates; the map converts them to GeoJSON. Zone polygons also use `{lat, lng}[]`. No mock geometries are fabricated.
- Section 4.5 already uses `explanation` for plain text, while 4.6A requires an explanation block on findings. The shared fixture resolves this with `explain`; cost uses `explanation`. Fixture field names also resolve `userChanged`, sensitivity `input`, `changePercent`, `newSaving`, and impact `zoneIds`.
- Forecast wrappers, model metrics, assumptions, incidents, health, comparison, placement, drift and routing lack complete JSON schemas in the prose. Types follow the now-available shared fixtures: health `{status, llm: 'live'|'fallback'}`, chat done `agentsConsulted`, routing `{agent, reason}[]`, metrics `lastRun`, comparison `saving`, scenario parameter definitions as an array, and scenario network `multi` for cross-network events. These details should eventually be written into the shared contract by its owners.
- The scenario request calls for a “scenario id” without a key: this client types it as `scenarioId`. `delayMs` relative versus absolute is unspecified; the mock uses relative delays, preserving payloads except for monotonically advancing reading ticks. Reset reconnects clients to ensure a fresh baseline snapshot.
- No incident-report fixture filename is provided. The mock exposes the route with a structured missing-fixture 404.

No backend, shared fixture, or documentation files are changed by this frontend scaffold.

## Verification in this environment

`npm test`: 9 passing tests. `npm run lint`: passes. Both `npm run build` and `npm run typecheck` currently fail with `TS2307: Cannot find module 'echarts-for-react'`. Registry access fails with `ENOTFOUND registry.npmjs.org`; ECharts, echarts-for-react, both font packages and deck.gl geo-layers are absent from the local cache. The available real dependencies were restored from that cache to check the remaining source and run the tests. No dependency stubs were introduced. A full `npm install` is still required when registry access is available. The sandbox also denies binding a listening port (`listen EPERM`), so mock integration tests exercise real Express request/response serialization through in-memory streams.
