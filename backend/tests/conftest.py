"""Tests never send anything to Telegram on their own.

`app/agents/llm.py` loads `backend/.env` when it is imported, so a developer's real bot token reaches every test that starts the app. The alerts would then send a
real message for each scenario a test runs. This switches them off for the whole run; `tests/test_alerts.py` turns them on again for itself, with a fake Telegram.
"""

import os

os.environ["ALERTS_ENABLED"] = "0"
