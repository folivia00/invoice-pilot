#!/bin/sh
# Container entry point: apply the database migrations, then exec the bot or the API.
set -eu

case "${1:-bot}" in
    bot | api) component="${1:-bot}" ;;
    *) exec "$@" ;;
esac

python -m alembic -c /app/alembic.ini upgrade head
exec python -m "invoice_pilot.$component"
