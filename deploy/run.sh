#!/bin/sh
# Server entry point (Compact profile). The host's systemd unit runs app/run.sh, a link
# to this file, in the foreground and loads the secrets from its EnvironmentFile; this
# script never reads .env and never prints secret values. See deploy/README.md.
set -eu

APP_DIR="${APP_DIR:-/var/lib/invoicepilot/app}"
VENV="${VENV:-/var/lib/invoicepilot/.venv}"
DATA_DIR="${DATA_DIR:-/var/lib/invoicepilot/data}"

missing=""
for name in BOT_TOKEN GEMINI_API_KEY; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        missing="$missing $name"
    fi
done
unset value
if [ -n "$missing" ]; then
    echo "run.sh: missing required variables:$missing (set them in the service .env file)" >&2
    exit 2
fi

# The code runs from src/ (not installed as a package); glibc returns large freed
# buffers to the system right away (memory benchmark, bench/README.md).
PYTHONPATH="$APP_DIR/src"
MALLOC_MMAP_THRESHOLD_=131072
PYTHONUNBUFFERED=1
export APP_DIR DATA_DIR PYTHONPATH MALLOC_MMAP_THRESHOLD_ PYTHONUNBUFFERED

cd "$APP_DIR"
mkdir -p "$DATA_DIR"
"$VENV/bin/python" -m alembic -c "$APP_DIR/alembic.ini" upgrade head
exec "$VENV/bin/python" -m invoice_pilot.bot
