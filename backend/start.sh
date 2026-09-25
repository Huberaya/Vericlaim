#!/usr/bin/env sh
set -e

echo "[VeriClaim AI] Checking and applying database migrations..."
if [ -n "$DATABASE_URL" ]; then
  alembic upgrade head || echo "[VeriClaim AI] Warning: Migration check failed, starting app anyway..."
fi

echo "[VeriClaim AI] Starting FastAPI engine on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
