#!/bin/bash
# Docker entrypoint — starts the API and runs the scraper on a nightly schedule.
# All config can be overridden via environment variables (see README).

set -e

CONFIG=/app/config.json
DATA_DIR=${DATA_DIR:-/data}

# ---------------------------------------------------------------------------
# Apply environment variable overrides to config.json
# This allows Docker/Unraid users to set credentials via env vars
# rather than mounting a config file.
# ---------------------------------------------------------------------------
apply_env_overrides() {
    local tmp=$(mktemp)
    python3 - << PYEOF
import json, os, sys

with open('$CONFIG') as f:
    cfg = json.load(f)

# ESB credentials
if os.environ.get('ESB_USERNAME'):
    cfg['esb']['username'] = os.environ['ESB_USERNAME']
if os.environ.get('ESB_PASSWORD'):
    cfg['esb']['password'] = os.environ['ESB_PASSWORD']
if os.environ.get('ESB_MPRN'):
    cfg['esb']['mprn'] = os.environ['ESB_MPRN']

# 2captcha
if os.environ.get('TWO_CAPTCHA_KEY'):
    cfg['two_captcha_api_key'] = os.environ['TWO_CAPTCHA_KEY']

# Data directory
cfg['data_dir'] = os.environ.get('DATA_DIR', '/data')

# Night hours
if os.environ.get('NIGHT_START'):
    cfg['night_hours']['start'] = os.environ['NIGHT_START']
if os.environ.get('NIGHT_END'):
    cfg['night_hours']['end'] = os.environ['NIGHT_END']

# VAT rate
if os.environ.get('VAT_RATE'):
    cfg['vat_rate'] = float(os.environ['VAT_RATE'])

with open('$CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)

print("Config updated from environment variables")
PYEOF
}

# ---------------------------------------------------------------------------
# Nightly scheduler — runs scraper at SCRAPER_TIME (default 02:30)
# ---------------------------------------------------------------------------
run_scheduler() {
    SCRAPER_TIME=${SCRAPER_TIME:-"02:30"}
    echo "[scheduler] Nightly scraper scheduled for ${SCRAPER_TIME} (container local time)"

    while true; do
        NOW=$(date +%H:%M)
        if [ "$NOW" = "$SCRAPER_TIME" ]; then
            echo "[scheduler] Running scraper at $NOW..."
            cd /app && python -m scraper.download 2>&1 | sed 's/^/[scraper] /'
            # Sleep 61 seconds to avoid running twice in the same minute
            sleep 61
        fi
        sleep 30
    done
}

# ---------------------------------------------------------------------------
# Initialise
# ---------------------------------------------------------------------------
echo "=== ESB Energy Tracker starting ==="

apply_env_overrides

# Ensure data directory exists and initialise DB
mkdir -p "$DATA_DIR"
cd /app && python -m api.init_db

# Run scraper on startup if requested (useful for first run)
if [ "${RUN_ON_START:-false}" = "true" ]; then
    echo "[startup] RUN_ON_START=true — running scraper now..."
    python -m scraper.download 2>&1 | sed 's/^/[scraper] /' || true
fi

# Start scheduler in background
run_scheduler &

# Start API (foreground — this keeps the container alive)
echo "[api] Starting FastAPI on port ${PORT:-8000}..."
exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info
