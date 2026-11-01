# Server deployment (Compact profile)

The demo runs on a shared Ubuntu 24.04 host as a systemd service provided by the host:
user `invoicepilot`, home `/var/lib/invoicepilot`, system Python 3.12 with `venv`, no
Docker. The unit starts `/var/lib/invoicepilot/app/run.sh` in the foreground, loads the
secrets from `/var/lib/invoicepilot/.env` and restarts the process on failure. The bot uses
long polling: no incoming ports.

| Path | Contents |
|---|---|
| `/var/lib/invoicepilot/app/` | the code from the release archive; `run.sh` links to `deploy/run.sh` |
| `/var/lib/invoicepilot/.venv/` | runtime dependencies only |
| `/var/lib/invoicepilot/data/` | SQLite database |
| `/var/lib/invoicepilot/.env` | secrets, `chmod 600`, owned by the service user |

## What is deployed

Only `src/`, `migrations/`, `alembic.ini` and `deploy/`. The code runs from `src/` through
`PYTHONPATH` (set by `run.sh`); the project itself is not installed, so `pip` needs no build
backend and every installed file comes from a hashed wheel. `eval/`, `tests/`, `bench/` and
the docs stay out of the archive.

## Files

- `requirements.txt`: the runtime dependencies from `uv.lock`, pinned, with hashes, without the
  dev group and without the project itself. Regenerate it after every change to `uv.lock`
  (a test fails when it is out of date):

  ```sh
  uv export --frozen --no-dev --no-emit-project --no-header --format requirements.txt \
    --output-file deploy/requirements.txt
  ```

  The same command is `make deploy-requirements`.
- `run.sh`: checks that `BOT_TOKEN` and `GEMINI_API_KEY` are set (values are never printed),
  sets `PYTHONPATH`, `MALLOC_MMAP_THRESHOLD_=131072` (keeps the memory peak of a series of
  large documents within the target, see `bench/README.md`), applies the database migrations
  in a separate process, then `exec`s the bot. `APP_DIR`, `VENV` and `DATA_DIR` default to the
  server paths above and can be overridden. It does not read `.env`: the unit does.

## `.env`

One `KEY=value` per line, no `export` (systemd skips such lines silently), no quotes, no `#`
inside values (it becomes part of the value). Required: `BOT_TOKEN`, `GEMINI_API_KEY`. Usual
additions for the demo: `ADMIN_IDS`, and the limits from the main README's configuration
table. `PROFILE` defaults to `compact`, `DATA_DIR` is set by `run.sh`.

## Release archive

On the development machine, from the repository root:

```sh
git archive --format=tar.gz --output=invoice-pilot-app.tar.gz HEAD \
  src migrations alembic.ini deploy
tar -tzf invoice-pilot-app.tar.gz   # only src/, migrations/, alembic.ini, deploy/
```

## Install or update

Outside the host's maintenance windows (UTC 00:30–00:45, 02:30–02:50, 03:30–03:45,
Sun 01:15–01:30). All commands run as `invoicepilot`; the archive is copied to
`/var/lib/invoicepilot/` first (`scp` or `rsync` over SSH).

```sh
cd /var/lib/invoicepilot

# 1. stop the service (an update only; the first start has no run.sh yet)
sudo systemctl stop invoicepilot.service

# 2. code: replace the contents of app/ with the archive, link the entry point
find app -mindepth 1 -delete
tar -xzf invoice-pilot-app.tar.gz -C app
ln -s deploy/run.sh app/run.sh
chmod 755 app/deploy/run.sh

# 3. dependencies (the venv is created once; later runs only reinstall the pins)
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --no-input --disable-pip-version-check --no-cache-dir \
  --require-hashes --only-binary=:all: -r app/deploy/requirements.txt
rm -rf .cache/pip   # left by installs made before --no-cache-dir

# 4. permissions
chmod 600 .env
chmod 750 data

# 5. start; migrations run inside run.sh before the bot
sudo systemctl start invoicepilot.service
systemctl status invoicepilot.service
journalctl -u invoicepilot.service -n 50
```

`--only-binary=:all:` makes `pip` fail instead of building anything from source: the host
has no compilers, and every runtime dependency has a wheel for CPython 3.12 on Linux x86-64.
`--no-cache-dir` keeps the downloaded wheels out of `~/.cache/pip`, which would otherwise
take about 20 MB of the service's disk quota.

A healthy start logs, in this order: the Alembic migration lines, `startup: stale
reservations marked failed=N`, `bot profile: set 3 of 3`, `bot starting: profile=compact …`
and aiogram's `Start polling`. `sudo systemctl stop invoicepilot.service` sends SIGTERM:
the bot stops polling, closes its sessions and exits with code 0.
