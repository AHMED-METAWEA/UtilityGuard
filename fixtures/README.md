# Fixtures

One JSON sample for every REST endpoint and every WebSocket message. They are the shared contract between the backend and the frontend:

- the backend tests validate each file against the Pydantic schemas and require a lossless round trip (`backend/tests/test_fixtures.py`);
- the frontend parses the same files in its tests and in the mock server.

Do not edit them by hand. Regenerate with the real functions on a fixed clock:

```bash
cd backend && PYTHONPATH=. .venv/bin/python scripts/make_fixtures.py
# partial: --approval-only (board decision, approval, decision log), --forecast-evidence-only
```

| Folder | Contents |
| --- | --- |
| `rest/` | Responses of the REST endpoints (`topology`, `alerts`, `forecast`, `forecast_evidence`, `board_session`, `board_decision_executing`, `audit`, …). |
| `ws/` | One message per WebSocket type (`snapshot`, `reading`, `alert`, `board_decision`, `audit_entry`, …). |
| `sequence/` | A recorded pipe-burst sequence with delays, used by the mock server. |

When a schema changes: update `backend/app/schemas`, regenerate, update `docs/contract.md` and the frontend types, then run both test suites.
