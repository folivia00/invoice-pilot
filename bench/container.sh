#!/bin/sh
# Runs inside python:3.12-slim (see bench/README.md). Mounts:
#   /src  repository (read-only)   /big  generated 5 MB files   /out  results
# The measured process gets the same allocator setting as run.sh on the server.
set -eu
RUNS="${RUNS:-3}"
pip install --quiet --disable-pip-version-check --root-user-action=ignore "uv==0.13.1"
mkdir -p /app
(cd /src && tar --exclude=./.venv --exclude='*/__pycache__' --exclude=./.git -cf - .) | tar -C /app -xf -
cd /app
UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev --no-editable --compile-bytecode --quiet
uv pip freeze --python /opt/venv/bin/python > /out/pip-freeze.txt
/opt/venv/bin/python -VV > /out/python.txt
: > /out/runs.jsonl
i=1
while [ "$i" -le "$RUNS" ]; do
    MALLOC_MMAP_THRESHOLD_=131072 /opt/venv/bin/python bench/memory.py --big-dir /big >> /out/runs.jsonl
    i=$((i + 1))
done
