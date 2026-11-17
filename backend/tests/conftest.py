"""Tests never send anything to Telegram on their own.

`app/agents/llm.py` loads `backend/.env` when it is imported, so a developer's real bot token reaches every test that starts the app. The alerts would then send a
real message for each scenario a test runs. This switches them off for the whole run; `tests/test_alerts.py` turns them on again for itself, with a fake Telegram.
"""

import os

os.environ["ALERTS_ENABLED"] = "0"
# the language chosen for the agents is kept in a file; a test run must not read or change the developer's own choice
import tempfile  # noqa: E402

os.environ["UG_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ug-settings-"), "settings.json")
