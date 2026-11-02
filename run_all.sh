#!/usr/bin/env bash
# Starts the twin (port 8000) and the healthcare team's own dashboard (port 8501) together.
#
#   ./run_all.sh                 # the twin on http://localhost:8000, their dashboard on http://localhost:8501 (also inside the twin, "Healthcare" tab)
#
# Their project is never copied into this repository and never edited: the first run clones it (read-only use) into integration/health/their-app, which is
# git-ignored, and gives it its own virtual environment. Settings, all optional:
#   HEALTH_REPO     where to clone it from   (default: their GitHub repository)
#   HEALTH_APP_DIR  where it lives            (default: integration/health/their-app)
#   HEALTH_PYTHON   a Python that already has their requirements installed (skips the virtual environment)
#   HEALTH_ENTRY    their Streamlit entry file (default: app.py)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_REPO="${HEALTH_REPO:-https://github.com/Gehan3/utilization-Environmental-measurements-Healthcare_Investment_Planning.git}"
HEALTH_APP_DIR="${HEALTH_APP_DIR:-$ROOT/integration/health/their-app}"
HEALTH_ENTRY="${HEALTH_ENTRY:-app.py}"

if [ ! -d "$HEALTH_APP_DIR/.git" ]; then
  echo "Cloning the healthcare project into $HEALTH_APP_DIR (once)"
  git clone --quiet "$HEALTH_REPO" "$HEALTH_APP_DIR"
fi
if [ -z "${HEALTH_PYTHON:-}" ]; then
  if [ ! -x "$HEALTH_APP_DIR/.venv/bin/python" ]; then
    echo "Setting up their environment (once, a few minutes)"
    python3 -m venv "$HEALTH_APP_DIR/.venv"
    "$HEALTH_APP_DIR/.venv/bin/pip" install --quiet --default-timeout=120 --retries 10 -r "$HEALTH_APP_DIR/requirements.txt"
  fi
  HEALTH_PYTHON="$HEALTH_APP_DIR/.venv/bin/python"
fi
if [ ! -d "$ROOT/frontend/dist" ]; then
  echo "Building the twin's web app (once)"
  (cd "$ROOT/frontend" && npm install --silent && npm run build --silent)
fi

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

(cd "$HEALTH_APP_DIR" && exec "$HEALTH_PYTHON" -m streamlit run "$HEALTH_ENTRY" --server.port 8501 --server.headless true --browser.gatherUsageStats false) &
PIDS+=($!)
(cd "$ROOT/backend" && PYTHONPATH=. exec .venv/bin/uvicorn app.main:app --port 8000) &
PIDS+=($!)

echo
echo "  The twin               http://localhost:8000"
echo "  Healthcare dashboard   http://localhost:8501   (also inside the twin: Healthcare tab)"
echo "  Press Ctrl+C to stop both."
echo
wait
