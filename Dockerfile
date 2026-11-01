# Full profile / quick start image (clients and local checks; the demo server runs
# without Docker, see deploy/). One image, two processes: "bot" and "api".
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    MALLOC_MMAP_THRESHOLD_=131072 \
    DATA_DIR=/data

# Runtime dependencies only: pinned, hashed wheels from uv.lock, nothing built from source.
COPY deploy/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-input --disable-pip-version-check --no-cache-dir \
        --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /app app \
    && mkdir /data && chown app:app /data

WORKDIR /app
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker/entrypoint.sh ./entrypoint.sh

USER app
VOLUME /data
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["bot"]
