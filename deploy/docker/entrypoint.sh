#!/bin/sh
# API container entrypoint: migrate, optionally seed the demo company, then serve.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  python -m alembic upgrade head
fi

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "Seeding demo users, documents and the PHX project..."
  python -m scripts.run_demo --seed-only
fi

exec uvicorn backend.app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
