#!/bin/bash
# Launch ManhwaStudio — double-click this file in Finder to start the app.
#
# Starts the Vite dev server and the FastAPI + native window process together,
# and shuts both down cleanly when you close the app window or this terminal.

set -u
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_PYTHON="./venv/bin/python3"
PIDS=()

cleanup() {
    echo ""
    echo "Shutting down ManhwaStudio…"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait 2>/dev/null
}
trap cleanup EXIT INT TERM

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    echo "Create it first with: python3 -m venv venv && ./venv/bin/pip install -r <requirements>"
    read -p "Press Enter to close…"
    exit 1
fi

echo "Starting Vite dev server…"
(cd ui && npm run dev) &
PIDS+=($!)

echo "Starting ManhwaStudio (API + window)…"
(cd scripts && "../$VENV_PYTHON" app.py) &
APP_PID=$!
PIDS+=($APP_PID)

# Wait for the main app process (the window) to exit, then clean up Vite too.
wait "$APP_PID"
