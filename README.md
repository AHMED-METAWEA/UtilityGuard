# UtilityGuard Twin

A live digital twin of a district of the New Administrative Capital, with water, electricity and district cooling networks,
specialist AI agents, and a savings advisor. Every number is computed by code from a named formula; the AI only explains.

Docs: [spec](docs/spec.md) · [contract](docs/contract.md) · [decisions](docs/decisions.md) · [CEO playbook](docs/ceo-playbook.md) · [ideas](docs/wow-ideas.md) · [design system](docs/design-system.md)

## Run it (demo)

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # once
cp .env.example .env   # then put your GROQ_API_KEY in it (without it the agents answer from recorded responses)
cd ../frontend && npm install && npm run build                                         # once
cd ../backend && .venv/bin/uvicorn app.main:app --port 8000                            # first start trains the models (about 1 minute)
```

Open http://localhost:8000. For frontend development run `npm run dev` in `frontend/` (port 5173, proxies to 8000).
Everything the map needs (OpenStreetMap buildings, roads, parks, the Green River) is stored in `frontend/public/geo`, so it works offline.

## Check it

```bash
cd backend && .venv/bin/pytest -q                      # 70+ tests: schemas against fixtures, cost formulas, detection, API
cd backend && .venv/bin/python -m scripts.demo_check   # runs the whole demo path 3 times in a row against a running server
cd frontend && npm run typecheck && npm test && npm run build
```

Fallback mode: start the server with `LLM_OFFLINE=1` and run `demo_check` again. Recorded answers are stored in
`backend/data/fallback_cache.json` the first time each question is answered live.

## Layout

`backend/app` simulation (`sim`), detection, ML (`ml`), cost model (`cost`), agents (`agents`), dashboard numbers (`dashboard`, `savings`), API (`main`).
`frontend/src` pages, charts, map, panels, design tokens (`design/theme.css`). `fixtures/` is the shared contract sample data.
