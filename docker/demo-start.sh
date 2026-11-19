#!/bin/sh
# Start command of the public demo (render.yaml). Runs on every boot: a free instance sleeps when idle and
# boots again on the next visit, and the database may be brand new.
#   1. migrations (no-op when up to date)
#   2. seed the shop + policies (idempotent upsert; leaves tickets alone, the nightly reset clears them)
#   3. the API, trusting the host's proxy for the client IP (the live-mode per-visitor cap needs it)
set -e
alembic upgrade head
python -m data.seed
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips '*' \
  --log-config app/uvicorn_log.json
